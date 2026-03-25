"""VM provisioning, listing, protection, destroy, and bootstrap routes."""

import json
import os
import requests
import time

from flask import Blueprint, Response, jsonify, request
from utils.inventory_yaml import (
    find_host_group,
    list_staging_hosts,
    move_host,
    PROMOTABLE_GROUPS,
    OS_TYPE_GROUP_MAP,
)

from config import cfg
from .audit import audit_log
from .shared import (
    PM_API_URL,
    ROOT_DIR,
    TF_LINUX_STATE_PATH,
    TF_LINUX_TFVARS_PATH,
    TF_VYOS_TFVARS_PATH,
    TF_VTHUNDER_STATE_PATH,
    TF_VYOS_STATE_PATH,
    TF_VTHUNDER_TFVARS_PATH,
    api_error,
    get_vm_health,
    load_vm_track,
    pm_headers,
    save_vm_track,
    validate_vm_params,
)

vms_bp = Blueprint("vms", __name__)


# ── Helpers ─────────────────────────────────────────────────

def _check_vmid_available(vmid: int) -> tuple:
    """Check if a VMID is available (not used by any VM or template in Proxmox)."""
    headers = pm_headers()
    if not headers:
        return False, "Proxmox API not configured"
    try:
        url = f"{PM_API_URL}/cluster/resources?type=vm"
        r = requests.get(url, headers=headers, verify=False, timeout=5)
        r.raise_for_status()
        data = r.json().get("data", [])
        existing = {item["vmid"] for item in data if "vmid" in item}
        if vmid in existing:
            # Find the name for a better error message
            name = next((item.get("name", "unknown") for item in data if item.get("vmid") == vmid), "unknown")
            return False, f"VMID {vmid} is already in use by '{name}'"
        return True, ""
    except Exception as e:
        return False, f"Could not verify VMID: {e}"


# ── Routes ──────────────────────────────────────────────────

@vms_bp.route("/api/subnets")
def api_subnets():
    """Return configured subnets for the subnet dropdown."""
    subnets = []
    for network, conf in cfg.SUBNETS.items():
        subnets.append({
            "network": network,
            "gateway": conf["gateway"],
            "dns": conf["dns"],
        })
    return jsonify(subnets)


@vms_bp.route("/api/check_vmid/<int:vmid>")
def api_check_vmid(vmid):
    """Check if a VMID is available for use."""
    available, reason = _check_vmid_available(vmid)
    return jsonify({"available": available, "reason": reason})


@vms_bp.route("/api/create_vm", methods=["POST"])
def api_create_vm():
    data = request.json or {}
    hostname = data.get("hostname")
    ip = data.get("ip")
    vm_id = data.get("vm_id")
    template = data.get("template")
    template_id = data.get("template_id", "")
    cpu = data.get("cpu")
    ram = data.get("ram")
    disk = data.get("disk")
    node = data.get("node", cfg.PM_NODE)
    vm_type = data.get("vm_type", "linux")
    acos_version = data.get("acos_version", "")

    bridges = data.get("bridges")
    if not bridges:
        bridge = data.get("bridge", "vmbr0")
        bridges = [bridge] if bridge else ["vmbr0"]

    if not hostname or not ip or not vm_id:
        return api_error("hostname, ip, and vm_id are required")
    if not template_id:
        return api_error("template_id is required")

    validation_err = validate_vm_params(data)
    if validation_err:
        return api_error(validation_err)

    vm_id_int = int(vm_id)

    # Dynamic VMID check against Proxmox (covers templates + running VMs)
    available, reason = _check_vmid_available(vm_id_int)
    if not available:
        return api_error(reason)

    audit_log("vm_create", f"{hostname} (VMID {vm_id_int})", f"type={vm_type} ip={ip} template={template}")

    if vm_type not in ("vthunder", "linux", "vyos"):
        return api_error(f"Unknown vm_type '{vm_type}'")

    # Dispatch Celery task
    from tasks.workflows import provision_vm
    task = provision_vm.delay(
        hostname=hostname, ip=ip, vm_id=vm_id_int, template=template,
        cpu=cpu or 2, ram=ram or 4096, disk=disk or 32, node=node,
        vm_type=vm_type, acos_version=acos_version, bridges=bridges,
        template_id=int(template_id) if template_id else 0,
    )

    return jsonify({
        "task_id": task.id,
        "message": f"Provisioning {vm_type} VM: {hostname} (VMID {vm_id_int})",
        "stream_url": f"/api/tasks/{task.id}/stream",
    })


@vms_bp.route("/api/vm_health/<vmid>")
def api_vm_health(vmid):
    try:
        health = get_vm_health(cfg.PM_NODE, int(vmid))
        return jsonify(health)
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)})


@vms_bp.route("/api/list_vms")
def api_list_vms():
    vms = []

    tfvars_ip_map = {}
    for tfvars_path, config_key in [
        (TF_LINUX_TFVARS_PATH, "vm_configs"),
        (TF_VTHUNDER_TFVARS_PATH, "vthunder_configs"),
        (TF_VYOS_TFVARS_PATH, "vyos_configs"),
    ]:
        if os.path.exists(tfvars_path):
            try:
                with open(tfvars_path, "r") as f:
                    tfvars = json.load(f)
                for name, c in tfvars.get(config_key, {}).items():
                    ip_with_cidr = c.get("ipv4_address", "")
                    tfvars_ip_map[name] = ip_with_cidr.split("/")[0] if ip_with_cidr else ""
            except Exception:
                pass

    state_files = [TF_LINUX_STATE_PATH, TF_VTHUNDER_STATE_PATH, TF_VYOS_STATE_PATH]

    for state_path in state_files:
        if not os.path.exists(state_path):
            continue
        try:
            with open(state_path, "r") as f:
                state = json.load(f)
            for res in state.get("resources", []):
                rtype = res.get("type")

                if rtype == "proxmox_vm_qemu":
                    for inst in res.get("instances", []):
                        attrs = inst.get("attributes", {})
                        node = attrs.get("target_node", cfg.PM_NODE)
                        vid = attrs.get("vmid")
                        health = get_vm_health(node, vid)
                        vms.append({
                            "vm_id": vid, "name": attrs.get("name"),
                            "ip": attrs.get("ipconfig0", ""),
                            "node": node, "status": attrs.get("vm_state", ""),
                            "health": health,
                        })

                elif rtype == "proxmox_virtual_environment_vm":
                    for inst in res.get("instances", []):
                        attrs = inst.get("attributes", {})
                        node = attrs.get("node_name", cfg.PM_NODE)
                        vid = attrs.get("vm_id") or attrs.get("id")
                        vm_name = attrs.get("name", "")

                        ip = ""
                        init_list = attrs.get("initialization") or []
                        if init_list:
                            ip_configs = init_list[0].get("ip_config") or []
                            if ip_configs:
                                ipv4_list = ip_configs[0].get("ipv4") or []
                                if ipv4_list:
                                    ip_with_cidr = ipv4_list[0].get("address", "")
                                    ip = ip_with_cidr.split("/")[0] if ip_with_cidr else ""

                        if not ip:
                            ipv4_addrs = attrs.get("ipv4_addresses") or []
                            ip = ipv4_addrs[0] if ipv4_addrs else ""
                        if not ip and vm_name in tfvars_ip_map:
                            ip = tfvars_ip_map[vm_name]

                        started = attrs.get("started")
                        status = "running" if started else "stopped"
                        health = get_vm_health(node, int(vid))

                        vms.append({
                            "vm_id": vid, "name": vm_name, "ip": ip,
                            "node": node, "status": status, "health": health,
                        })
        except Exception:
            continue

    track = load_vm_track()
    now = int(time.time())
    for vm in vms:
        vid = str(vm["vm_id"])
        if vid not in track:
            track[vid] = {"first_seen": now, "last_seen": now}
        else:
            track[vid]["last_seen"] = now
        vm["inventory_group"] = find_host_group(vm.get("name", "")) or ""
    save_vm_track(track)

    return jsonify({"vms": vms, "track": track})


@vms_bp.route("/api/list_vmids")
def api_list_vmids():
    headers = pm_headers()
    if not headers:
        return api_error("Proxmox API token not configured", 503)
    try:
        url = f"{PM_API_URL}/cluster/resources?type=vm"
        r = requests.get(url, headers=headers, verify=False, timeout=3)
        r.raise_for_status()
        data = r.json().get("data", [])
        vms = sorted(
            [{"vmid": item["vmid"], "name": item.get("name", "unknown")} for item in data if "vmid" in item],
            key=lambda x: x["vmid"],
        )
        return jsonify({"vms": vms})
    except Exception as e:
        return api_error(str(e), 500)


@vms_bp.route('/api/vm/protect', methods=['POST'])
def api_vm_protect():
    data = request.get_json()
    vm_id = str(data.get("vm_id", ""))
    protected = bool(data.get("protected", False))
    track = load_vm_track()
    if vm_id not in track:
        return api_error(f"VM {vm_id} not found in tracking", 404)
    track[vm_id]["protected"] = protected
    save_vm_track(track)
    audit_log("vm_protect", f"VMID {vm_id}", f"protected={protected}")
    return jsonify({"ok": True, "vm_id": vm_id, "protected": protected})


@vms_bp.route('/api/destroy_vm', methods=['POST'])
def api_destroy_vm():
    data = request.get_json()
    vm_id = data.get("vm_id")
    track = load_vm_track()
    if track.get(str(vm_id), {}).get("protected"):
        return Response(
            f"[PERIMETER] ✖ VM {vm_id} is PROTECTED. Disable protection first.\n",
            status=403, mimetype='text/plain',
        )

    audit_log("vm_destroy", f"VMID {vm_id}")

    from tasks.workflows import destroy_vm as destroy_vm_task
    task = destroy_vm_task.delay(vm_id=int(vm_id))

    return jsonify({
        "task_id": task.id,
        "message": f"Destroy sequence initiated for VM_ID {vm_id}",
        "stream_url": f"/api/tasks/{task.id}/stream",
    })


@vms_bp.route("/api/rerun_bootstrap", methods=["POST"])
def api_rerun_bootstrap():
    data = request.json or {}
    vm_id = data.get("vm_id")
    if not vm_id:
        return api_error("vm_id is required")

    audit_log("vm_rebootstrap", f"VMID {vm_id}")

    # Look up VM info from Terraform state (synchronous — fast)
    vm_info = None
    vm_type = None
    for state_path, vtype in [(TF_LINUX_STATE_PATH, "linux"), (TF_VTHUNDER_STATE_PATH, "vthunder"), (TF_VYOS_STATE_PATH, "vyos")]:
        if vm_info:
            break
        if not os.path.exists(state_path):
            continue
        with open(state_path, "r") as f:
            tf_state = json.load(f)
        for resource in tf_state.get("resources", []):
            if resource.get("type") == "proxmox_virtual_environment_vm":
                for instance in resource.get("instances", []):
                    attrs = instance.get("attributes", {})
                    if attrs.get("vm_id") == int(vm_id):
                        vm_info = attrs
                        vm_type = vtype
                        break
            if vm_info:
                break

    if not vm_info:
        return api_error(f"VM {vm_id} not found in any Terraform workspace state", 404)

    hostname = vm_info.get("name", "unknown")
    ip = None
    init_config = vm_info.get("initialization", [])
    if init_config and len(init_config) > 0:
        ip_config = init_config[0].get("ip_config", [])
        if ip_config and len(ip_config) > 0:
            ipv4 = ip_config[0].get("ipv4", [])
            if ipv4 and len(ipv4) > 0:
                ip_addr = ipv4[0].get("address", "")
                ip = ip_addr.split("/")[0] if "/" in ip_addr else ip_addr

    if not ip or not hostname:
        return api_error(f"Could not extract hostname/IP for VMID {vm_id}")

    if vm_type != "linux":
        return api_error(f"Re-bootstrap not supported for {vm_type} VMs")

    from tasks.workflows import bootstrap_vm
    task = bootstrap_vm.delay(hostname=hostname, ip=ip)

    return jsonify({
        "task_id": task.id,
        "message": f"Re-running bootstrap for {hostname} ({ip})",
        "stream_url": f"/api/tasks/{task.id}/stream",
    })


# ── Inventory promotion ───────────────────────────────────

@vms_bp.route("/api/inventory/staging")
def api_inventory_staging():
    """List all hosts currently in staging groups."""
    return jsonify(list_staging_hosts())


@vms_bp.route("/api/inventory/groups")
def api_inventory_groups():
    """List valid promotion target groups with os_type suggestions."""
    groups = []
    for g in PROMOTABLE_GROUPS:
        suggested_for = [os for os, grp in OS_TYPE_GROUP_MAP.items() if grp == g]
        groups.append({"name": g, "suggested_for": suggested_for})
    return jsonify(groups)


@vms_bp.route("/api/inventory/promote", methods=["POST"])
def api_inventory_promote():
    """Promote a host from a staging group to a target production group."""
    data = request.get_json() or {}
    hostname = data.get("hostname", "").strip()
    target_group = data.get("target_group", "").strip()

    if not hostname or not target_group:
        return api_error("hostname and target_group are required")

    if target_group not in PROMOTABLE_GROUPS:
        return api_error(f"Invalid target group: {target_group}")

    # Find current group
    current_group = find_host_group(hostname)
    if not current_group:
        return api_error(f"Host '{hostname}' not found in inventory", 404)

    if current_group == target_group:
        return api_error(f"Host '{hostname}' is already in '{target_group}'")

    try:
        move_host(hostname, current_group, target_group)
    except KeyError as e:
        return api_error(str(e), 400)

    audit_log("inventory_promote", hostname, f"{current_group} → {target_group}")
    return jsonify({
        "ok": True,
        "hostname": hostname,
        "from_group": current_group,
        "to_group": target_group,
    })


# ── Fleet Deployments ────────────────────────────────────

@vms_bp.route("/api/fleet/deploy", methods=["POST"])
def api_fleet_deploy():
    """Deploy multiple VMs in parallel as a fleet."""
    import uuid
    import redis as _redis_mod

    data = request.json or {}
    fleet_name = data.get("fleet_name", "").strip() or f"fleet-{int(time.time())}"
    vms = data.get("vms", [])

    if not vms or not isinstance(vms, list):
        return api_error("vms array is required with at least one VM definition")

    if len(vms) > 20:
        return api_error("Maximum 20 VMs per fleet")

    # Pre-validate ALL VMs before dispatching any
    seen_vmids = set()
    seen_ips = set()
    seen_hostnames = set()

    for i, vm in enumerate(vms):
        hostname = vm.get("hostname", "").strip()
        ip = vm.get("ip", "").strip()
        vm_id = vm.get("vm_id")
        template_id = vm.get("template_id")

        if not hostname or not ip or not vm_id:
            return api_error(f"VM #{i+1}: hostname, ip, and vm_id are required")
        if not template_id:
            return api_error(f"VM #{i+1} ({hostname}): template_id is required")

        # Check for duplicates within the fleet
        if vm_id in seen_vmids:
            return api_error(f"Duplicate VMID {vm_id} in fleet")
        seen_vmids.add(vm_id)

        if ip in seen_ips:
            return api_error(f"Duplicate IP {ip} in fleet")
        seen_ips.add(ip)

        if hostname in seen_hostnames:
            return api_error(f"Duplicate hostname {hostname} in fleet")
        seen_hostnames.add(hostname)

        # Validate each VM's params
        validation_err = validate_vm_params(vm)
        if validation_err:
            return api_error(f"VM #{i+1} ({hostname}): {validation_err}")

        # Check VMID availability
        available, reason = _check_vmid_available(int(vm_id))
        if not available:
            return api_error(f"VM #{i+1} ({hostname}): {reason}")

    # All validation passed — dispatch fleet orchestrator task
    # Same-type VMs run sequentially (Terraform state lock), different types run in parallel
    fleet_id = f"fleet-{uuid.uuid4().hex[:12]}"

    from tasks.workflows import run_fleet
    fleet_task = run_fleet.delay(fleet_id=fleet_id, vms=vms)

    # Build task list — individual task IDs will be set by the orchestrator
    # For now, use fleet_id + index as placeholder task IDs
    tasks = []
    for i, vm in enumerate(vms):
        task_id = f"{fleet_id}-vm-{i}"
        tasks.append({
            "hostname": vm["hostname"].strip(),
            "task_id": task_id,
            "stream_url": f"/api/tasks/{task_id}/stream",
        })

    # Store fleet metadata in Redis
    r = _redis_mod.Redis.from_url(cfg.REDIS_URL)
    fleet_data = {
        "fleet_name": fleet_name,
        "total": str(len(tasks)),
        "created_at": str(time.time()),
    }
    for i, t in enumerate(tasks):
        fleet_data[f"task_{i}_id"] = t["task_id"]
        fleet_data[f"task_{i}_hostname"] = t["hostname"]
    r.hset(f"perimeter:fleet:{fleet_id}", mapping=fleet_data)
    r.expire(f"perimeter:fleet:{fleet_id}", 86400)  # 24h TTL
    r.close()

    audit_log("fleet_deploy", fleet_name, f"{len(tasks)} VMs")

    return jsonify({
        "fleet_id": fleet_id,
        "fleet_name": fleet_name,
        "tasks": tasks,
    })


@vms_bp.route("/api/fleet/<fleet_id>")
def api_fleet_status(fleet_id):
    """Get aggregate status of a fleet deployment."""
    import redis as _redis_mod

    r = _redis_mod.Redis.from_url(cfg.REDIS_URL)
    fleet_raw = r.hgetall(f"perimeter:fleet:{fleet_id}")

    if not fleet_raw:
        r.close()
        return api_error("Fleet not found", 404)

    fleet = {k.decode(): v.decode() for k, v in fleet_raw.items()}
    total = int(fleet.get("total", 0))

    vms = []
    completed = 0
    failed = 0
    running = 0
    queued = 0

    for i in range(total):
        task_id = fleet.get(f"task_{i}_id", "")
        hostname = fleet.get(f"task_{i}_hostname", "")

        # Get task status from Redis job metadata
        job_meta = r.hgetall(f"perimeter:job:{task_id}")
        if job_meta:
            status = job_meta.get(b"status", b"queued").decode()
        else:
            status = "queued"

        if status == "success":
            completed += 1
        elif status == "failed" or status == "error":
            failed += 1
        elif status == "running":
            running += 1
        else:
            queued += 1

        vms.append({
            "hostname": hostname,
            "task_id": task_id,
            "status": status,
            "stream_url": f"/api/tasks/{task_id}/stream",
        })

    r.close()

    return jsonify({
        "fleet_id": fleet_id,
        "fleet_name": fleet.get("fleet_name", ""),
        "total": total,
        "completed": completed,
        "failed": failed,
        "running": running,
        "queued": queued,
        "vms": vms,
    })


@vms_bp.route("/api/fleet/<fleet_id>/cancel", methods=["POST"])
def api_fleet_cancel(fleet_id):
    """Cancel all pending/running tasks in a fleet."""
    import redis as _redis_mod
    from celery_app import celery

    r = _redis_mod.Redis.from_url(cfg.REDIS_URL)
    fleet_raw = r.hgetall(f"perimeter:fleet:{fleet_id}")

    if not fleet_raw:
        r.close()
        return api_error("Fleet not found", 404)

    fleet = {k.decode(): v.decode() for k, v in fleet_raw.items()}
    total = int(fleet.get("total", 0))
    cancelled = 0

    for i in range(total):
        task_id = fleet.get(f"task_{i}_id", "")
        job_meta = r.hgetall(f"perimeter:job:{task_id}")
        status = job_meta.get(b"status", b"queued").decode() if job_meta else "queued"

        if status in ("queued", "running"):
            celery.control.revoke(task_id, terminate=True, signal="SIGTERM")
            r.hset(f"perimeter:job:{task_id}", "status", "cancelled")
            cancelled += 1

    r.close()

    return jsonify({
        "fleet_id": fleet_id,
        "cancelled": cancelled,
    })

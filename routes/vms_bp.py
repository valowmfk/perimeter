"""VM provisioning, listing, protection, destroy, and bootstrap routes."""

import json
import os
import requests
import subprocess
import time

from flask import Blueprint, Response, g, jsonify, request
from threading import Thread

from utils.qlog import get_correlation_id
from utils.inventory_yaml import (
    find_host_group,
    list_staging_hosts,
    move_host,
    PROMOTABLE_GROUPS,
    OS_TYPE_GROUP_MAP,
)

from config import cfg
from utils.metrics import VM_PROVISIONS, VM_PROVISIONS_ACTIVE
from .audit import audit_log
from .shared import (
    JOB_STATUS,
    PM_API_URL,
    ROOT_DIR,
    LINUX_SCRIPT_DIR,
    SUBPROCESS_TIMEOUT,
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


# ── Background job runner ───────────────────────────────────

def run_vm_job(job_id, hostname, ip, vm_id, template, cpu, ram, disk, node,
               vm_type, acos_version, bridges):
    JOB_STATUS[job_id] = {"status": "Running", "log": []}
    VM_PROVISIONS_ACTIVE.inc()
    bridges_json = json.dumps(bridges)

    if vm_type == "vthunder":
        script = os.path.join(ROOT_DIR, "python", "workflows", "provision_vthunder.py")
        JOB_STATUS[job_id]["log"].append(
            f"[PERIMETER] Using Python provisioner: {script} (vm_type={vm_type}, bridges={bridges}, acos_version={acos_version})\n"
        )
    elif vm_type == "linux":
        script = os.path.join(ROOT_DIR, "python", "workflows", "provision_linux.py")
        JOB_STATUS[job_id]["log"].append(
            f"[PERIMETER] Using Python provisioner: {script} (vm_type={vm_type}, bridges={bridges})\n"
        )
    elif vm_type == "vyos":
        script = os.path.join(ROOT_DIR, "python", "workflows", "provision_vyos.py")
        JOB_STATUS[job_id]["log"].append(
            f"[PERIMETER] Using Python provisioner: {script} (vm_type={vm_type}, bridges={bridges})\n"
        )
    else:
        script = os.path.join(LINUX_SCRIPT_DIR, "create_vm.sh")
        JOB_STATUS[job_id]["log"].append(
            f"[PERIMETER] Using legacy bash script: {script} (vm_type={vm_type})\n"
        )

    try:
        if vm_type in ("vthunder", "linux", "vyos"):
            work_dir = os.path.join(ROOT_DIR, "python")
            script_arg = f"workflows/provision_{vm_type}.py"
            cmd = ["python3", script_arg, hostname, ip, str(vm_id), template,
                   str(cpu), str(ram), str(disk), node, vm_type, acos_version, bridges_json]
        else:
            work_dir = None
            cmd = [script, hostname, ip, str(vm_id), template,
                   str(cpu), str(ram), str(disk), node, vm_type, acos_version, bridges_json]

        env = os.environ.copy()
        cid = get_correlation_id()
        if cid:
            env["PERIMETER_CORRELATION_ID"] = cid

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, cwd=work_dir, env=env,
        )

        for line in proc.stdout:
            JOB_STATUS[job_id]["log"].append(line)
        try:
            proc.wait(timeout=SUBPROCESS_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            JOB_STATUS[job_id]["log"].append(
                f"[PERIMETER] ERROR: Process killed after {SUBPROCESS_TIMEOUT}s timeout\n"
            )

        status = "success" if proc.returncode == 0 else "failed"
        JOB_STATUS[job_id]["status"] = "Success" if proc.returncode == 0 else "Failed"
        JOB_STATUS[job_id]["_finished_at"] = time.time()
        VM_PROVISIONS.labels(vm_type=vm_type, status=status).inc()
        VM_PROVISIONS_ACTIVE.dec()

    except Exception as e:
        JOB_STATUS[job_id]["status"] = "Error"
        JOB_STATUS[job_id]["log"].append(f"[PERIMETER] ERROR in run_vm_job: {e}\n")
        JOB_STATUS[job_id]["_finished_at"] = time.time()
        VM_PROVISIONS.labels(vm_type=vm_type, status="error").inc()
        VM_PROVISIONS_ACTIVE.dec()


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

    bridges_json = json.dumps(bridges)

    def generate():
        yield f"[PERIMETER] Provisioning {vm_type} VM: {hostname} (VMID {vm_id_int})\n"
        yield f"[PERIMETER] IP: {ip}, Template: {template} (ID {template_id}), NICs: {len(bridges)} ({', '.join(bridges)})\n"

        if vm_type == "vthunder":
            yield f"[PERIMETER] ACOS Version: {acos_version}\n"

        if vm_type in ("vthunder", "linux", "vyos"):
            work_dir = os.path.join(ROOT_DIR, "python")
            cmd = ["python3", f"workflows/provision_{vm_type}.py", hostname, ip,
                   str(vm_id_int), template, str(template_id), str(cpu), str(ram),
                   str(disk), node, vm_type, acos_version, bridges_json]
        else:
            yield f"[PERIMETER] ERROR: Unknown vm_type '{vm_type}'\n"
            return

        env = os.environ.copy()
        env["FORCE_COLOR"] = "1"
        env["ANSIBLE_FORCE_COLOR"] = "1"
        cid = get_correlation_id()
        if cid:
            env["PERIMETER_CORRELATION_ID"] = cid

        try:
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, cwd=work_dir, env=env,
            )
            for line in process.stdout:
                yield line
            try:
                process.wait(timeout=SUBPROCESS_TIMEOUT)
            except subprocess.TimeoutExpired:
                process.kill()
                yield f"\n[PERIMETER] ✖ Process killed after {SUBPROCESS_TIMEOUT}s timeout\n"
                return

            if process.returncode == 0:
                yield f"\n[PERIMETER] ✔ Provisioning completed successfully for {hostname}\n"
            else:
                yield f"\n[PERIMETER] ✖ Provisioning failed for {hostname} (exit code {process.returncode})\n"
        except Exception as e:
            yield f"[PERIMETER] ERROR: {e}\n"

    return Response(generate(), mimetype='text/plain')


@vms_bp.route("/api/vm_status/<job_id>")
def api_vm_status(job_id):
    return jsonify(JOB_STATUS.get(job_id, {"status": "NotFound"}))


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

    script = os.path.join(ROOT_DIR, "python", "workflows", "destroy_vm.py")
    work_dir = os.path.join(ROOT_DIR, "python")

    def generate():
        yield f"[PERIMETER] Destroy sequence initiated for VM_ID {vm_id}\n"
        env = os.environ.copy()
        cid = get_correlation_id()
        if cid:
            env["PERIMETER_CORRELATION_ID"] = cid
        process = subprocess.Popen(
            ["python3", script, str(vm_id)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, cwd=work_dir, env=env,
        )
        for line in process.stdout:
            yield line
        try:
            process.wait(timeout=SUBPROCESS_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            yield f"[PERIMETER] ✖ Process killed after {SUBPROCESS_TIMEOUT}s timeout\n"
            return
        if process.returncode == 0:
            yield f"[PERIMETER] ✔ Destroy completed successfully for VM_ID {vm_id}\n"
        else:
            yield f"[PERIMETER] ✖ Destroy failed for VM_ID {vm_id}\n"

    return Response(generate(), mimetype='text/plain')


@vms_bp.route("/api/rerun_bootstrap", methods=["POST"])
def api_rerun_bootstrap():
    data = request.json or {}
    vm_id = data.get("vm_id")
    if not vm_id:
        return api_error("vm_id is required")

    audit_log("vm_rebootstrap", f"VMID {vm_id}")

    def generate():
        yield f"[PERIMETER] Re-running bootstrap for VMID {vm_id}...\n"
        try:
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
                yield f"[PERIMETER] ERROR: VM {vm_id} not found in any Terraform workspace state\n"
                return

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
                yield f"[PERIMETER] ERROR: Could not extract hostname/IP for VMID {vm_id}\n"
                return

            yield f"[PERIMETER] Found VM: {hostname} ({ip}) type={vm_type}\n"

            if vm_type == "linux":
                script = os.path.join(ROOT_DIR, "python", "workflows", "bootstrap_linux.py")
                cmd = ["python3", script, hostname, ip]
                work_dir = os.path.join(ROOT_DIR, "python")
            elif vm_type == "vthunder":
                yield "[PERIMETER] ERROR: vThunder re-bootstrap not yet implemented\n"
                return
            else:
                yield f"[PERIMETER] ERROR: Unknown VM type for VMID {vm_id}\n"
                return

            bs_env = os.environ.copy()
            cid = get_correlation_id()
            if cid:
                bs_env["PERIMETER_CORRELATION_ID"] = cid
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, cwd=work_dir, env=bs_env,
            )
            for line in proc.stdout:
                yield line
            try:
                proc.wait(timeout=SUBPROCESS_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                yield f"\n[PERIMETER] ✖ Process killed after {SUBPROCESS_TIMEOUT}s timeout\n"
                return
            yield f"\n[PERIMETER] Bootstrap exited with code {proc.returncode}\n"

        except Exception as e:
            yield f"[PERIMETER] ERROR: {e}\n"

    return Response(generate(), mimetype="text/plain")


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

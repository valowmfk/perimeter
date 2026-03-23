"""System status, cluster health, network bridges, vThunder query, and template management routes."""

import os
import subprocess
import uuid
import time
import requests

from flask import Blueprint, jsonify, request, Response

from config import cfg
from utils.metrics import VIP_OPERATIONS, TEMPLATE_REFRESHES
from .shared import (
    JOB_STATUS,
    PM_API_URL,
    TERRAFORM_DIR,
    api_error,
    get_node_bridges,
    get_vthunder_host_credentials,
    is_safe_filename,
    load_inventory_yaml,
    pm_headers,
)

from axapi.client import AxapiClient
from axapi.errors import AxapiAuthError, AxapiError, AxapiTransportError
from helpers.slb_manager import create_slb_config, destroy_slb_config

system_bp = Blueprint("system", __name__)


@system_bp.route("/api/network_bridges")
def api_network_bridges():
    node = request.args.get("node", "goldfinger")
    bridges = get_node_bridges(node)
    return jsonify({"node": node, "bridges": bridges})


@system_bp.route("/api/cluster_health")
def api_cluster_health():
    headers = pm_headers()
    if not headers:
        return api_error("Proxmox API token not configured", 503)
    try:
        resp = requests.get(
            f"{PM_API_URL}/cluster/status",
            headers=headers,
            verify=False,
            timeout=5,
        )
        resp.raise_for_status()
        return jsonify({"success": True, "data": resp.json()})
    except Exception as e:
        return api_error(str(e), 500)


@system_bp.route("/api/system_status")
def api_system_status():
    # Proxmox
    try:
        headers = pm_headers()
        url = f"{PM_API_URL}/nodes"
        r = requests.get(url, headers=headers, verify=False, timeout=3)
        r.raise_for_status()
        nodes = r.json().get("data", [])
        prox = {"status": "online", "label": "online", "extra": f"{len(nodes)} node(s)"}
    except Exception:
        prox = {"status": "error", "label": "unreachable"}

    # Pi-hole
    try:
        r = requests.get(f"{cfg.PIHOLE_URL}/admin", timeout=2)
        pihole = {"status": "online", "label": "online"} if r.ok else {"status": "error", "label": f"http {r.status_code}"}
    except Exception:
        pihole = {"status": "error", "label": "unreachable"}

    # Terraform lock
    linux_lock = os.path.join(TERRAFORM_DIR, "linux_vm", "terraform-linux.tfstate.lock.info")
    vthunder_lock = os.path.join(TERRAFORM_DIR, "vthunder_vm", "terraform-vthunder.tfstate.lock.info")
    if os.path.exists(linux_lock) or os.path.exists(vthunder_lock):
        terraform = {"status": "locked", "label": "locked"}
    else:
        terraform = {"status": "idle", "label": "idle"}

    # Bootstrap queue
    active = sum(1 for j in JOB_STATUS.values() if j.get("status") in ("Running", "Starting"))
    if active > 0:
        bootstrap = {"status": "busy", "label": "busy", "extra": f"{active} job(s)"}
    else:
        bootstrap = {"status": "idle", "label": "idle"}

    return jsonify({"proxmox": prox, "pihole": pihole, "terraform": terraform, "bootstrap": bootstrap})


# ── vThunder query routes ───────────────────────────────────

def _get_vthunder_partitions(host, username, password, port=443):
    try:
        with AxapiClient(host=host, username=username, password=password, port=port) as client:
            client.login()
            result = client.get("/axapi/v3/partition")
            return result.get("partition-list", [])
    except (AxapiAuthError, AxapiTransportError, AxapiError) as e:
        raise Exception(str(e))


def _get_vthunder_vips(host, username, password, partition, port=443):
    try:
        with AxapiClient(host=host, username=username, password=password, port=port) as client:
            client.login()
            client.post(f"/axapi/v3/active-partition/{partition}", body={})
            result = client.get("/axapi/v3/slb/virtual-server")
            return result.get("virtual-server-list", [])
    except (AxapiAuthError, AxapiTransportError, AxapiError) as e:
        raise Exception(str(e))


def _get_vthunder_certs(host, username, password, partition, port=443):
    try:
        with AxapiClient(host=host, username=username, password=password, port=port) as client:
            client.login()
            client.post(f"/axapi/v3/active-partition/{partition}", body={})
            result = client.get("/axapi/v3/file/ssl-cert/oper")
            return result.get("ssl-cert", {}).get("oper", {}).get("file-list", [])
    except (AxapiAuthError, AxapiTransportError, AxapiError) as e:
        raise Exception(str(e))


def _get_vthunder_ssl_templates(host, username, password, partition, port=443):
    try:
        with AxapiClient(host=host, username=username, password=password, port=port) as client:
            client.login()
            client.post(f"/axapi/v3/active-partition/{partition}", body={})
            result = client.get("/axapi/v3/slb/template/client-ssl")
            return result.get("client-ssl-list", [])
    except (AxapiAuthError, AxapiTransportError, AxapiError) as e:
        raise Exception(str(e))


def _get_vthunder_service_groups(host, username, password, partition, port=443):
    try:
        with AxapiClient(host=host, username=username, password=password, port=port) as client:
            client.login()
            client.post(f"/axapi/v3/active-partition/{partition}", body={})
            result = client.get("/axapi/v3/slb/service-group")
            return result.get("service-group-list", [])
    except (AxapiAuthError, AxapiTransportError, AxapiError) as e:
        raise Exception(str(e))


def _get_vthunder_servers(host, username, password, partition, port=443):
    try:
        with AxapiClient(host=host, username=username, password=password, port=port) as client:
            client.login()
            client.post(f"/axapi/v3/active-partition/{partition}", body={})
            result = client.get("/axapi/v3/slb/server")
            return result.get("server-list", [])
    except (AxapiAuthError, AxapiTransportError, AxapiError) as e:
        raise Exception(str(e))


@system_bp.route("/api/vthunder/hosts", methods=["POST"])
def api_vthunder_hosts():
    data = request.json or {}
    inventory_file = data.get("inventory_file", "").strip()
    group_name = data.get("group_name", "").strip()
    if not inventory_file or not group_name:
        return api_error("inventory_file and group_name are required")
    if not is_safe_filename(inventory_file):
        return api_error("Invalid inventory filename")
    try:
        inventory = load_inventory_yaml(inventory_file)
        if group_name not in inventory:
            return api_error(f"Group '{group_name}' not found in inventory", 404)
        hosts = list(inventory[group_name].get('hosts', {}).keys())
        return jsonify({"hosts": hosts})
    except Exception as e:
        return api_error(f"Failed to load inventory: {str(e)}", 500)


@system_bp.route("/api/vthunder/partitions", methods=["POST"])
def api_vthunder_partitions():
    data = request.json or {}
    inventory_file = data.get("inventory_file", "").strip()
    group_name = data.get("group_name", "").strip()
    host = data.get("host", "").strip()
    if not inventory_file or not group_name or not host:
        return api_error("inventory_file, group_name, and host are required")
    if not is_safe_filename(inventory_file):
        return api_error("Invalid inventory filename")
    try:
        creds = get_vthunder_host_credentials(inventory_file, group_name, host)
        if not creds:
            return api_error(f"Could not find credentials for host {host} in group {group_name}", 404)
        partitions = _get_vthunder_partitions(host=host, username=creds['username'],
                                               password=creds['password'], port=creds['port'])
        shared_partition = {"partition-name": "shared", "id": 0, "application-type": "adc"}
        return jsonify({"partitions": [shared_partition] + partitions})
    except Exception as e:
        return api_error(str(e), 500)


@system_bp.route("/api/vthunder/vips", methods=["POST"])
def api_vthunder_vips():
    data = request.json or {}
    inventory_file = data.get("inventory_file", "").strip()
    group_name = data.get("group_name", "").strip()
    host = data.get("host", "").strip()
    partition = data.get("partition", "").strip()
    if not inventory_file or not group_name or not host or not partition:
        return api_error("inventory_file, group_name, host, and partition are required")
    if not is_safe_filename(inventory_file):
        return api_error("Invalid inventory filename")
    try:
        creds = get_vthunder_host_credentials(inventory_file, group_name, host)
        if not creds:
            return api_error(f"Could not find credentials for host {host} in group {group_name}", 404)
        vips = _get_vthunder_vips(host=host, username=creds['username'], password=creds['password'],
                                  partition=partition, port=creds['port'])
        return jsonify({"vips": vips})
    except Exception as e:
        return api_error(str(e), 500)


def _resolve_vthunder_creds(data):
    """Common credential resolution for partition-scoped vThunder queries."""
    inventory_file = data.get("inventory_file", "").strip()
    group_name = data.get("group_name", "").strip()
    host = data.get("host", "").strip()
    partition = data.get("partition", "").strip()
    if not inventory_file or not group_name or not host or not partition:
        return None, None, None, "inventory_file, group_name, host, and partition are required"
    if not is_safe_filename(inventory_file):
        return None, None, None, "Invalid inventory filename"
    creds = get_vthunder_host_credentials(inventory_file, group_name, host)
    if not creds:
        return None, None, None, f"Could not find credentials for host {host} in group {group_name}"
    return host, partition, creds, None


@system_bp.route("/api/vthunder/certs", methods=["POST"])
def api_vthunder_certs():
    host, partition, creds, err = _resolve_vthunder_creds(request.json or {})
    if err:
        return api_error(err)
    try:
        certs = _get_vthunder_certs(host=host, username=creds['username'], password=creds['password'],
                                    partition=partition, port=creds['port'])
        return jsonify({"certs": certs})
    except Exception as e:
        return api_error(str(e), 500)


@system_bp.route("/api/vthunder/ssl-templates", methods=["POST"])
def api_vthunder_ssl_templates():
    host, partition, creds, err = _resolve_vthunder_creds(request.json or {})
    if err:
        return api_error(err)
    try:
        templates = _get_vthunder_ssl_templates(host=host, username=creds['username'], password=creds['password'],
                                                 partition=partition, port=creds['port'])
        return jsonify({"ssl_templates": templates})
    except Exception as e:
        return api_error(str(e), 500)


@system_bp.route("/api/vthunder/service-groups", methods=["POST"])
def api_vthunder_service_groups():
    host, partition, creds, err = _resolve_vthunder_creds(request.json or {})
    if err:
        return api_error(err)
    try:
        groups = _get_vthunder_service_groups(host=host, username=creds['username'], password=creds['password'],
                                               partition=partition, port=creds['port'])
        return jsonify({"service_groups": groups})
    except Exception as e:
        return api_error(str(e), 500)


@system_bp.route("/api/vthunder/servers", methods=["POST"])
def api_vthunder_servers():
    host, partition, creds, err = _resolve_vthunder_creds(request.json or {})
    if err:
        return api_error(err)
    try:
        servers = _get_vthunder_servers(host=host, username=creds['username'], password=creds['password'],
                                        partition=partition, port=creds['port'])
        return jsonify({"servers": servers})
    except Exception as e:
        return api_error(str(e), 500)


@system_bp.route("/api/vthunder/create-vip", methods=["POST"])
def api_vthunder_create_vip():
    """Create a full SLB stack (servers, health monitor, service group, SSL template, VIP)."""
    data = request.json or {}
    host, partition, creds, err = _resolve_vthunder_creds(data)
    if err:
        return api_error(err)

    config = data.get("config")
    if not config or not isinstance(config, dict):
        return api_error("config object is required")

    required_fields = ["vip_name", "vip_ip", "backends"]
    missing = [f for f in required_fields if not config.get(f)]
    if missing:
        return api_error(f"Missing required config fields: {', '.join(missing)}")

    backends = config.get("backends", [])
    if not isinstance(backends, list) or len(backends) == 0:
        return api_error("At least one backend server is required")

    for b in backends:
        if not b.get("name") or not b.get("ip") or not b.get("port"):
            return api_error("Each backend requires name, ip, and port")

    try:
        result = create_slb_config(
            host=host,
            username=creds["username"],
            password=creds["password"],
            port=creds["port"],
            partition=partition,
            config=config,
        )
        VIP_OPERATIONS.labels(action="create", status="success" if result["success"] else "failed").inc()
        status_code = 200 if result["success"] else 500
        return jsonify(result), status_code
    except Exception as e:
        VIP_OPERATIONS.labels(action="create", status="error").inc()
        return api_error(str(e), 500)


@system_bp.route("/api/vthunder/destroy-vip", methods=["POST"])
def api_vthunder_destroy_vip():
    """Destroy a VIP and its associated SLB resources."""
    data = request.json or {}
    host, partition, creds, err = _resolve_vthunder_creds(data)
    if err:
        return api_error(err)

    vip_name = data.get("vip_name", "").strip()
    if not vip_name:
        return api_error("vip_name is required")

    cleanup_servers = data.get("cleanup_servers", True)

    try:
        result = destroy_slb_config(
            host=host,
            username=creds["username"],
            password=creds["password"],
            port=creds["port"],
            partition=partition,
            vip_name=vip_name,
            cleanup_servers=cleanup_servers,
        )
        VIP_OPERATIONS.labels(action="destroy", status="success" if result["success"] else "failed").inc()
        status_code = 200 if result["success"] else 500
        return jsonify(result), status_code
    except Exception as e:
        VIP_OPERATIONS.labels(action="destroy", status="error").inc()
        return api_error(str(e), 500)


# ── Template Management ────────────────────────────────────

_refresh_processes = {}


@system_bp.route("/api/proxmox/templates")
def api_proxmox_templates():
    """List all templates with metadata for the Proxmox flyout."""
    node = request.args.get("node", "goldfinger")
    headers = pm_headers()
    if not headers:
        return api_error("Proxmox API not configured")
    try:
        url = f"{PM_API_URL}/nodes/{node}/qemu"
        r = requests.get(url, headers=headers, verify=False, timeout=10)
        data = r.json().get("data", [])
    except Exception as e:
        return api_error(str(e), 500)

    # Load refresh timestamps
    import json as _json
    refresh_log_path = os.path.join(cfg.ROOT_DIR, "data", "template_refresh.json")
    refresh_timestamps = {}
    try:
        with open(refresh_log_path) as f:
            refresh_timestamps = _json.load(f)
    except Exception:
        pass

    templates = []
    for vm in data:
        if vm.get("template", 0) != 1:
            continue
        name = vm.get("name", "")
        vmid = vm.get("vmid")
        is_vendor = name.startswith("acos") or name.startswith("vyos")
        last_refreshed = refresh_timestamps.get(name)

        templates.append({
            "name": name,
            "vmid": vmid,
            "status": "vendor" if is_vendor else "ready",
            "refreshable": not is_vendor,
            "last_refreshed": last_refreshed,
        })
    return jsonify({"templates": sorted(templates, key=lambda t: t["name"])})


@system_bp.route("/api/template/refresh", methods=["POST"])
def api_template_refresh():
    """Start a template refresh. Returns a session_id for SSE streaming."""
    data = request.json or {}
    template_name = data.get("template_name", "").strip()
    node = data.get("node", "goldfinger").strip()

    if not template_name:
        return api_error("template_name is required")

    if template_name.startswith("acos") or template_name.startswith("vyos"):
        return api_error("Vendor templates cannot be refreshed")

    session_id = str(uuid.uuid4())

    cmd = [
        "python3", "-u",
        os.path.join(cfg.ROOT_DIR, "python", "workflows", "refresh_template.py"),
        template_name,
        "--node", node,
    ]

    env = os.environ.copy()
    from utils.qlog import get_correlation_id
    cid = get_correlation_id()
    if cid:
        env["PERIMETER_CORRELATION_ID"] = cid

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
        cwd=cfg.ROOT_DIR,
    )

    _refresh_processes[session_id] = {
        "process": process,
        "template": template_name,
        "start_time": time.time(),
    }

    from .audit import audit_log
    audit_log("template_refresh", template_name, f"node={node}")

    return jsonify({"session_id": session_id})


@system_bp.route("/api/template/refresh/stream/<session_id>")
def api_template_refresh_stream(session_id):
    """SSE stream for template refresh progress."""
    data = _refresh_processes.get(session_id)
    if not data:
        return api_error("Invalid session", 404)

    process = data["process"]

    template_name = data.get("template", "unknown")

    def generate():
        for raw in process.stdout:
            line = raw.rstrip()
            yield f"data: {line}\n\n"
        process.wait(timeout=1800)
        status = "success" if process.returncode == 0 else "failed"
        TEMPLATE_REFRESHES.labels(template=template_name, status=status).inc()
        yield "data: __COMPLETE__\n\n"
        _refresh_processes.pop(session_id, None)

    return Response(generate(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


@system_bp.route("/api/proxmox/node-status")
def api_proxmox_node_status():
    """Get node-level status for the Proxmox flyout."""
    node = request.args.get("node", "goldfinger")
    headers = pm_headers()
    if not headers:
        return api_error("Proxmox API not configured")
    try:
        url = f"{PM_API_URL}/nodes/{node}/status"
        r = requests.get(url, headers=headers, verify=False, timeout=10)
        data = r.json().get("data", {})
        return jsonify({
            "node": node,
            "uptime": data.get("uptime", 0),
            "cpu": round(data.get("cpu", 0) * 100, 1),
            "memory_used": data.get("memory", {}).get("used", 0),
            "memory_total": data.get("memory", {}).get("total", 0),
            "rootfs_used": data.get("rootfs", {}).get("used", 0),
            "rootfs_total": data.get("rootfs", {}).get("total", 0),
        })
    except Exception as e:
        return api_error(str(e), 500)

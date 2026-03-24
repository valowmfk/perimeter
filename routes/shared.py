"""Shared state, helpers, and imports used across all blueprints."""

import os
import re
import json
import time
import yaml
import requests
from flask import jsonify

from config import cfg

# ── Shared mutable state ────────────────────────────────────
JOB_STATUS = {}
cert_sessions = {}
running_processes = {}
_netbox_cache = {"data": None, "ts": 0}

# TTL for completed jobs and cert sessions (seconds)
COMPLETED_TTL = 3600  # 1 hour

# Maximum runtime for subprocess operations (seconds)
SUBPROCESS_TIMEOUT = 3600  # 1 hour — covers Terraform + Ansible runs


def api_error(message: str, status: int = 400):
    """Return a consistent JSON error response. Single place to change the format."""
    return jsonify({"success": False, "error": message}), status

# ── Config aliases (read-only, for convenience) ─────────────
ROOT_DIR = str(cfg.ROOT_DIR)
SCRIPTS_DIR = str(cfg.SCRIPTS_DIR)
LINUX_SCRIPT_DIR = str(cfg.LINUX_SCRIPT_DIR)
VTHUNDER_SCRIPT_DIR = str(cfg.VTHUNDER_SCRIPT_DIR)
HELPERS_SCRIPT_DIR = str(cfg.HELPERS_SCRIPT_DIR)
PLAYBOOK_DIR = str(cfg.PLAYBOOK_DIR)
PLAYBOOK_TEMPLATE_DIR = str(cfg.PLAYBOOK_TEMPLATE_DIR)
INVENTORY_DIR = str(cfg.INVENTORY_DIR)
TERRAFORM_DIR = str(cfg.TERRAFORM_DIR)
CERTIFICATE_DIR = str(cfg.CERTIFICATE_DIR)
TF_LINUX_STATE_PATH = str(cfg.TF_LINUX_STATE_PATH)
TF_VTHUNDER_STATE_PATH = str(cfg.TF_VTHUNDER_STATE_PATH)
TF_VYOS_STATE_PATH = str(cfg.TF_VYOS_STATE_PATH)
TF_LINUX_TFVARS_PATH = str(cfg.TF_LINUX_TFVARS_PATH)
TF_VTHUNDER_TFVARS_PATH = str(cfg.TF_VTHUNDER_TFVARS_PATH)
TF_VYOS_TFVARS_PATH = str(cfg.TF_VYOS_TFVARS_PATH)
VM_TRACK_FILE = str(cfg.VM_TRACK_FILE)
CERT_DOMAINS = cfg.cert_domains
ALLOWED_CERT_FILES = cfg.ALLOWED_CERT_FILES

PM_API_URL = cfg.PM_API_URL
PM_API_TOKEN_ID = cfg.PM_API_TOKEN_ID
PM_API_TOKEN_SECRET = cfg.PM_API_TOKEN_SECRET
NETBOX_URL = cfg.NETBOX_URL
NETBOX_API_TOKEN = cfg.NETBOX_API_TOKEN
NETBOX_CACHE_TTL = cfg.NETBOX_CACHE_TTL

pm_headers = cfg.pm_headers
netbox_headers = cfg.netbox_headers


# ── Input validation ───────────────────────────────────────

import ipaddress

# RFC 1123 label: lowercase alphanumeric, hyphens allowed (not leading/trailing), max 63 chars
_LABEL_RE = re.compile(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$')


def _valid_hostname(hostname: str) -> bool:
    """Validate hostname — accepts short names (snmp) or FQDNs (snmp.home.klouda.co)."""
    if not hostname or len(hostname) > 253:
        return False
    labels = hostname.split('.')
    return all(_LABEL_RE.match(label) for label in labels)


def validate_vm_params(data: dict):
    """Validate VM creation parameters. Returns error message or None if valid."""
    hostname = data.get("hostname", "")
    ip = data.get("ip", "")
    vm_id = data.get("vm_id")
    cpu = data.get("cpu")
    ram = data.get("ram")
    disk = data.get("disk")

    if not _valid_hostname(str(hostname)):
        return "Invalid hostname. Use lowercase alphanumeric with optional hyphens/dots (e.g. snmp or snmp.home.klouda.co)."

    if not ip:
        return "IP address is required."
    try:
        addr = ipaddress.IPv4Address(ip)
        from config import subnet_for_ip
        if subnet_for_ip(ip) is None:
            allowed = ", ".join(cfg.SUBNETS.keys())
            return f"IP {ip} is outside configured subnets ({allowed})."
    except (ipaddress.AddressValueError, ValueError):
        return f"Invalid IPv4 address: {ip}"

    if vm_id is not None:
        try:
            vid = int(vm_id)
        except (ValueError, TypeError):
            return "vm_id must be an integer."
        if not 100 <= vid <= 9999:
            return f"VMID {vid} out of range (100-9999)."

    if cpu is not None:
        try:
            cpu_val = int(cpu)
        except (ValueError, TypeError):
            return "cpu must be an integer."
        if not 1 <= cpu_val <= 128:
            return f"CPU count {cpu_val} out of range (1-128)."

    if ram is not None:
        try:
            ram_val = int(ram)
        except (ValueError, TypeError):
            return "ram must be an integer (MB)."
        if not 512 <= ram_val <= 262144:
            return f"RAM {ram_val} MB out of range (512-262144)."

    if disk is not None:
        try:
            disk_val = int(disk)
        except (ValueError, TypeError):
            return "disk must be an integer (GB)."
        if not 10 <= disk_val <= 5000:
            return f"Disk {disk_val} GB out of range (10-5000)."

    return None


# ── Security helpers ────────────────────────────────────────

def is_safe_filename(filename):
    """Validate filename to prevent path traversal attacks."""
    if not filename:
        return False
    dangerous_patterns = ['..', '/', '\\', '\0']
    for pattern in dangerous_patterns:
        if pattern in filename:
            return False
    if not re.match(r'^[a-zA-Z0-9._-]+$', filename):
        return False
    return True


# ── VM tracking ─────────────────────────────────────────────

def load_vm_track():
    if not os.path.exists(VM_TRACK_FILE):
        return {}
    try:
        with open(VM_TRACK_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_vm_track(data: dict):
    try:
        with open(VM_TRACK_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


# ── Playbook / Inventory helpers ────────────────────────────

def list_playbooks():
    if not os.path.isdir(PLAYBOOK_DIR):
        return []
    return sorted(
        f for f in os.listdir(PLAYBOOK_DIR)
        if f.endswith((".yml", ".yaml"))
    )


def list_inventories():
    if not os.path.isdir(INVENTORY_DIR):
        return []
    return sorted(
        f for f in os.listdir(INVENTORY_DIR)
        if f.endswith((".yml", ".yaml", ".ini"))
    )


def load_inventory_yaml(filename):
    """Load inventory YAML file with security validation."""
    if not filename or not is_safe_filename(filename):
        return {}
    path = os.path.join(INVENTORY_DIR, filename)
    real_inventory_dir = os.path.realpath(INVENTORY_DIR)
    real_file_path = os.path.realpath(path)
    if not real_file_path.startswith(real_inventory_dir):
        return {}
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        data = {}
    return data


# ── vThunder helpers ────────────────────────────────────────

def get_vthunder_host_credentials(inventory_file, group_name, host_address):
    """Extract vThunder credentials for a specific host from inventory."""
    inventory = load_inventory_yaml(inventory_file)
    if group_name not in inventory:
        return None
    group = inventory[group_name]
    hosts = group.get('hosts', {})
    if host_address not in hosts:
        return None
    group_vars = group.get('vars', {})
    username = group_vars.get('ansible_username', 'admin')
    password = os.getenv('VTH_ADMIN_PASS', '')
    if not password:
        password_raw = group_vars.get('ansible_password', '')
        if '{{' in str(password_raw):
            password = os.getenv('ANSIBLE_BECOME_PASS', '')
        else:
            password = password_raw
    port = group_vars.get('ansible_port', 443)
    return {'username': username, 'password': password, 'port': port}


# ── Proxmox helpers ─────────────────────────────────────────

def get_vm_health(node: str, vmid: int):
    """Fetch VM status from Proxmox API."""
    headers = pm_headers()
    if not headers:
        return {"status": "unknown", "qmpstatus": "unknown", "uptime": 0}
    try:
        url = f"{PM_API_URL}/nodes/{node}/qemu/{vmid}/status/current"
        r = requests.get(url, headers=headers, verify=False, timeout=5)
        r.raise_for_status()
        data = r.json().get("data", {})
        return {
            "status": data.get("status", "unknown"),
            "qmpstatus": data.get("qmpstatus", "unknown"),
            "uptime": data.get("uptime", 0),
        }
    except Exception:
        return {"status": "error", "qmpstatus": "unknown", "uptime": 0}


def list_proxmox_templates():
    """Query Proxmox API to discover templates and ACOS appliances."""
    headers = pm_headers()
    if not headers:
        return []
    url = f"{PM_API_URL}/nodes/{cfg.PM_NODE}/qemu"
    try:
        r = requests.get(url, headers=headers, verify=False)
        data = r.json().get("data") or []
    except Exception:
        return []
    templates = []
    for vm in data:
        name = vm.get("name", "")
        vmid = vm.get("vmid")
        is_template = vm.get("template", 0) == 1
        if is_template:
            templates.append({"name": name, "vmid": vmid})
        elif name.lower().startswith("acos"):
            templates.append({"name": name, "vmid": vmid})
    return sorted(templates, key=lambda t: t["name"])


def get_node_bridges(node: str = None):
    """Query Proxmox for vmbr* bridges on a node."""
    node = node or cfg.PM_NODE
    headers = pm_headers()
    if not headers:
        return []
    try:
        url = f"{PM_API_URL}/nodes/{node}/network"
        r = requests.get(url, headers=headers, verify=False, timeout=5)
        r.raise_for_status()
        data = r.json().get("data", [])
    except Exception:
        return []
    bridges = []
    for item in data:
        if item.get("type") == "bridge" and item.get("iface", "").startswith("vmbr"):
            bridges.append(item["iface"])
    return sorted(bridges)

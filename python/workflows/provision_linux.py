#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

# ──────────────────────────────────────────────
# Ensure python/ is on sys.path
# ──────────────────────────────────────────────
THIS_FILE = Path(__file__).resolve()
PYTHON_DIR = THIS_FILE.parent.parent
ROOT_DIR = PYTHON_DIR.parent

if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

# ──────────────────────────────────────────────
# Internal imports (AFTER sys.path setup)
# ──────────────────────────────────────────────
from workflows.bootstrap_linux import run_linux_bootstrap
from helpers.dns_manager import dns_add_record
from helpers.netbox_ipam import netbox_create_ip
from utils.inventory_yaml import add_host_to_group
from axapi.utils import qlog, qlog_success, qlog_warning, qlog_error
from utils.qlog import init_correlation_id_from_env
from utils.sops_env import load_env
from utils.tfvars_io import locked_update
from utils.terraform_runner import terraform_init, terraform_apply

COMPONENT = "LINUX-PROV"

# Workspace-specific Terraform directory (no more -target!)
TF_DIR = ROOT_DIR / "terraform" / "linux_vm"
TFVARS_PATH = TF_DIR / "perimeter-linux.auto.tfvars.json"
DEFAULT_ENV_FILE = str(ROOT_DIR / "secrets" / "perimeter.enc.env")

# Fallback defaults
DEFAULT_DATASTORE = "zfs-pool"
# DNS domain loaded from config at runtime

# ─────────────────────────────
# Helpers
# ─────────────────────────────

from utils.network import normalize_ip_cidr


def add_vm_to_tfvars(hostname: str, vm_config: Dict[str, Any]) -> None:
    """Add a VM config to tfvars with file locking and atomic write."""
    def updater(data: Dict[str, Any]) -> None:
        data.setdefault("vm_configs", {})[hostname] = vm_config

    locked_update(TFVARS_PATH, updater)
    qlog(COMPONENT, f"Saved tfvars to {TFVARS_PATH}")


def run_terraform(hostname: str, vmid: int) -> int:
    """Run terraform init + apply for Linux VM workspace."""
    if terraform_init(TF_DIR, COMPONENT) != 0:
        return 1
    return terraform_apply(TF_DIR, hostname, vmid, COMPONENT)

# ─────────────────────────────
# Main orchestration
# ─────────────────────────────

def provision_linux_vm(
    hostname: str,
    ip: str,
    vmid: int,
    template: str,
    template_id: int,
    cpu: int,
    ram: int,
    disk: int,
    node: str,
    bridges: list,
    env_file: str = DEFAULT_ENV_FILE,
) -> int:
    """
    Orchestrate Linux VM provisioning:
    1. Load environment config
    2. Validate template
    3. Normalize IP to CIDR
    4. Merge VM config into tfvars
    5. Run Terraform
    6. Update Ansible inventory
    7. Run bootstrap (SSH wait + Ansible)
    8. Add DNS record

    Returns:
        0 on success, 1 on failure
    """
    qlog(
        COMPONENT,
        f"Provisioning Linux VM: host={hostname} ip={ip} vmid={vmid} template={template} "
        f"cpu={cpu} ram={ram} disk={disk} node={node} bridges={bridges}",
    )

    env = load_env(env_file)
    if not env:
        qlog_error(COMPONENT, f"Failed to load environment from {env_file} — is SOPS configured?")
        return 1

    ssh_user = env.get("LINUX_SSH_USER", os.getenv("USER", "perimeter"))
    ssh_keys = []
    i = 1
    while True:
        key = env.get(f"LINUX_SSH_KEY_{i}", "")
        if not key:
            break
        ssh_keys.append(key)
        i += 1

    # Subnet-aware gateway/DNS lookup
    from config import subnet_for_ip
    subnet_info = subnet_for_ip(ip)
    gateway = env.get("LINUX_GATEWAY") or (subnet_info["gateway"] if subnet_info else "10.1.55.254")
    dns_env = env.get("LINUX_DNS_SERVERS", "")
    if dns_env:
        dns_servers = [s.strip() for s in dns_env.split(",")]
    else:
        dns_servers = subnet_info["dns"] if subnet_info else ["10.1.55.10", "10.1.55.11"]
    datastore = env.get("LINUX_DATASTORE", DEFAULT_DATASTORE)

    if not ssh_keys:
        qlog_warning(COMPONENT, "No SSH keys found in env, VM may not be accessible")

    # Derive os_type from template name (e.g. "rocky9-template" → "rocky9")
    os_type = template.replace("-template", "")

    ip_cidr = normalize_ip_cidr(ip)
    ip_no_cidr = ip_cidr.split("/")[0]

    qlog(COMPONENT, f"Template: {template} → ID {template_id}, OS {os_type}")
    qlog(COMPONENT, f"IP normalized: {ip} → {ip_cidr}")
    qlog(COMPONENT, f"SSH user: {ssh_user}, Keys: {len(ssh_keys)}, Gateway: {gateway}, DNS: {dns_servers}")

    vm_config = {
        "vm_id": vmid,
        "name": hostname,
        "node": node,
        "template_id": template_id,
        "datastore_id": datastore,
        "memory": ram,
        "cores": cpu,
        "disk_size": disk,
        "bridges": bridges,
        "ipv4_address": ip_cidr,
        "ipv4_gateway": gateway,
        "dns_servers": dns_servers,
        "ssh_username": ssh_user,
        "ssh_keys": ssh_keys,
        "os_type": os_type,
        "tags": ["lab"],
    }

    add_vm_to_tfvars(hostname, vm_config)

    qlog_success(COMPONENT, f"VM config for {hostname} merged into tfvars")

    rc = run_terraform(hostname, vmid)
    if rc != 0:
        return rc

    # Add to staging inventory BEFORE bootstrap so ansible can target it
    qlog(COMPONENT, f"Adding {hostname} to staging_linux inventory...")
    add_host_to_group(hostname, ip_no_cidr, "staging_linux", {"os_type": os_type})
    qlog_success(COMPONENT, f"Added {hostname} to staging_linux in inventory.yml")

    qlog(COMPONENT, f"Running Linux bootstrap for {hostname}...")
    rc_bootstrap = run_linux_bootstrap(hostname, ip_no_cidr)

    if rc_bootstrap != 0:
        qlog_error(COMPONENT, f"Bootstrap failed for {hostname}")
        return 1

    qlog_success(COMPONENT, f"Bootstrap completed for {hostname}")

    from config import cfg
    if cfg.FEATURES.get("dns", True):
        qlog(COMPONENT, f"Adding DNS record for {hostname}...")
        if not dns_add_record(ip_no_cidr, hostname):
            qlog_error(COMPONENT, f"DNS record creation failed for {hostname}")
            return 1
    else:
        qlog(COMPONENT, "DNS feature disabled — skipping DNS record creation")

    if cfg.FEATURES.get("ipam", True):
        qlog(COMPONENT, f"Adding Netbox IPAM entry for {hostname}...")
        if not netbox_create_ip(ip_no_cidr, hostname):
            qlog_warning(COMPONENT, f"Netbox IPAM entry creation failed for {hostname} — non-fatal")
    else:
        qlog(COMPONENT, "IPAM feature disabled — skipping Netbox entry")

    qlog_success(COMPONENT, f"Linux VM {hostname} ({ip_no_cidr}) provisioning complete")
    return 0

def main() -> int:
    """CLI entrypoint matching Flask arguments."""
    init_correlation_id_from_env()

    if len(sys.argv) < 13:
        qlog_error(
            COMPONENT,
            "Usage: provision_linux.py HOSTNAME IP VMID TEMPLATE TEMPLATE_ID CPU RAM DISK NODE VM_TYPE ACOS_VERSION BRIDGES_JSON",
        )
        return 1

    hostname = sys.argv[1]
    ip = sys.argv[2]
    vmid = int(sys.argv[3])
    template = sys.argv[4]
    template_id = int(sys.argv[5])
    cpu = int(sys.argv[6])
    ram = int(sys.argv[7])
    disk = int(sys.argv[8])
    node = sys.argv[9]
    vm_type = sys.argv[10]
    bridges_arg = sys.argv[12]

    # Parse bridges - accepts JSON array or single bridge string for backwards compat
    try:
        bridges = json.loads(bridges_arg)
        if isinstance(bridges, str):
            bridges = [bridges]
    except json.JSONDecodeError:
        bridges = [bridges_arg]

    if vm_type != "linux":
        qlog_error(COMPONENT, f"provision_linux.py called with vm_type={vm_type} (expected linux)")
        return 1

    return provision_linux_vm(hostname, ip, vmid, template, template_id, cpu, ram, disk, node, bridges)

if __name__ == "__main__":
    sys.exit(main())

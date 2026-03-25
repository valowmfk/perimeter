#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

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
from helpers.dns_manager import dns_remove_record
from helpers.netbox_ipam import netbox_delete_ip
from axapi.utils import qlog, qlog_success, qlog_warning, qlog_error  # type: ignore
from utils.qlog import init_correlation_id_from_env
from utils.tfvars_io import locked_update
from utils.inventory_yaml import remove_host as remove_host_from_yaml
from utils.vm_types import get_vm_type, find_vm_in_tfvars as find_vm_by_id

COMPONENT = "VM-DESTROY"
KNOWN_HOSTS = Path.home() / ".ssh" / "known_hosts"






def remove_ssh_host_keys(hostname: str, ip: str) -> None:
    """Remove SSH host keys from known_hosts."""
    if not KNOWN_HOSTS.exists():
        return

    qlog(COMPONENT, f"Removing SSH keys for {hostname} and {ip}")

    for target in [hostname, ip]:
        if target:
            subprocess.run(
                ["ssh-keygen", "-R", target],
                check=False,
                capture_output=True,
                text=True,
            )


def run_terraform_destroy(vm_type: str, hostname: str) -> int:
    """Run terraform destroy for workspace-specific VM using -target."""
    vt = get_vm_type(vm_type)
    tf_dir = vt.terraform_dir
    target = f'module.{vt.terraform_module}["{hostname}"]'

    qlog(COMPONENT, f"Running terraform destroy for {hostname} in {vm_type} workspace (target={target})...")

    result = subprocess.run(
        ["terraform", "destroy", "-auto-approve", f"-target={target}"],
        cwd=str(tf_dir),
        capture_output=True,
        text=True,
    )

    # Stream output
    if result.stdout:
        qlog(COMPONENT, result.stdout.rstrip())
    if result.stderr:
        qlog_warning(COMPONENT, result.stderr.rstrip())

    if result.returncode != 0:
        qlog_error(COMPONENT, f"Terraform destroy failed for {hostname}")
        return 1

    qlog_success(COMPONENT, f"Terraform destroy completed for {hostname}")
    return 0


# ─────────────────────────────
# Main destroy logic
# ─────────────────────────────

def destroy_vm(vmid: int) -> int:
    """
    Destroy VM by VMID:
    1. Find VM in tfvars
    2. Run terraform destroy
    3. Remove from tfvars
    4. Clean up inventory/DNS/known_hosts

    Returns:
        0 on success, 1 on failure
    """
    qlog(COMPONENT, f"Destroy requested for VMID {vmid}")

    # Find VM
    result = find_vm_by_id(vmid)

    if not result:
        qlog_error(COMPONENT, f"No VM found with VMID {vmid}")
        return 1

    vm_type, key, ip = result
    vt = get_vm_type(vm_type)

    qlog(COMPONENT, f"Found {vm_type} VM: {key} ({ip})")

    # DNS cleanup
    from config import cfg
    if cfg.FEATURES.get("dns", True):
        qlog(COMPONENT, f"Removing DNS record for {key}...")
        if not dns_remove_record(ip, key):
            qlog_warning(COMPONENT, f"DNS record removal failed for {key} — continuing with destroy")
    else:
        qlog(COMPONENT, "DNS feature disabled — skipping DNS record removal")

    # Netbox IPAM cleanup
    if cfg.FEATURES.get("ipam", True):
        qlog(COMPONENT, f"Removing Netbox IPAM entry for {key}...")
        if not netbox_delete_ip(ip):
            qlog_warning(COMPONENT, f"Netbox IPAM removal failed for {key} — continuing with destroy")
    else:
        qlog(COMPONENT, "IPAM feature disabled — skipping Netbox entry removal")

    # Terraform destroy
    rc = run_terraform_destroy(vm_type, key)
    if rc != 0:
        return rc

    # Remove from workspace-specific tfvars (locked + atomic)
    def remove_entry(data: dict) -> None:
        if key in data.get(vt.tfvars_section, {}):
            del data[vt.tfvars_section][key]

    locked_update(vt.tfvars_path, remove_entry)
    qlog(COMPONENT, f"Removed {key} from {vt.tfvars_section}")

    # Remove from inventory.yml (all VM types)
    removed_group = remove_host_from_yaml(key)
    if removed_group:
        qlog(COMPONENT, f"Removed {key} from inventory.yml group '{removed_group}'")

    # SSH known_hosts cleanup
    remove_ssh_host_keys(key, ip)

    qlog_success(COMPONENT, f"{vm_type.capitalize()} VM {key} (VMID {vmid}) destroyed")
    return 0


def main() -> int:
    """CLI entrypoint."""
    init_correlation_id_from_env()

    if len(sys.argv) < 2:
        qlog_error(COMPONENT, "Usage: destroy_vm.py VMID")
        return 1

    try:
        vmid = int(sys.argv[1])
    except ValueError:
        qlog_error(COMPONENT, f"Invalid VMID: {sys.argv[1]}")
        return 1

    return destroy_vm(vmid)


if __name__ == "__main__":
    sys.exit(main())

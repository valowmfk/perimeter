#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

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
from workflows.bootstrap_vthunder import run_vthunder_bootstrap
from helpers.dns_manager import dns_add_record
from helpers.netbox_ipam import netbox_create_ip
from utils.inventory_yaml import add_host_to_group
from axapi.utils import qlog, qlog_success, qlog_warning, qlog_error
from utils.qlog import init_correlation_id_from_env
from utils.sops_env import load_env
from utils.tfvars_io import locked_update
from utils.terraform_runner import terraform_init, terraform_apply, terraform_output_json
from workflows.dhcp_scanner import discover_dhcp_ip

COMPONENT = "VTH-PROV"

# Workspace-specific Terraform directory (no more -target!)
TF_DIR = ROOT_DIR / "terraform" / "vthunder_vm"
TFVARS_PATH = TF_DIR / "perimeter-vthunder.auto.tfvars.json"
DEFAULT_ENV_FILE = str(ROOT_DIR / "secrets" / "perimeter.enc.env")

DEFAULT_DATASTORE = "zfs-pool"
DEFAULT_DHCP_PREFIX = "10.1.55"
DEFAULT_DHCP_START = 175
DEFAULT_DHCP_END = 245

ACOS_DEFAULT_CPU = 4
ACOS_DEFAULT_RAM = 8192
ACOS_DEFAULT_DISK = 40
VTH_SSH_USER = "admin"


# ─────────────────────────────
# Env + tfvars helpers
# ─────────────────────────────


def merge_vthunder_config(
    hostname: str,
    vth_cfg: Dict[str, Any],
    tfvars_path: Path = TFVARS_PATH,
) -> None:
    """Add a vThunder config to tfvars with file locking and atomic write."""
    def updater(data: Dict[str, Any]) -> None:
        data.setdefault("vthunder_configs", {})[hostname] = vth_cfg

    locked_update(tfvars_path, updater)

    qlog_success(
        COMPONENT,
        f"Merged vThunder definition for {hostname} into {tfvars_path}.",
    )


# ─────────────────────────────
# Terraform helpers
# ─────────────────────────────

def get_mgmt_mac_from_terraform(hostname: str) -> Optional[str]:
    mapping = terraform_output_json(TF_DIR, "vthunder_mgmt_mac", COMPONENT)
    if mapping is None:
        return None

    mac = mapping.get(hostname)
    qlog(COMPONENT, f"DEBUG: vthunder_mgmt_mac lookup for {hostname!r} → {mac!r}")

    if not mac or mac == "null":
        qlog_error(
            COMPONENT,
            f"Could not extract mgmt MAC for {hostname} from terraform output vthunder_mgmt_mac.",
        )
        return None

    return str(mac)


# ───────────────────────────────────────────
# IP normalisation helpers - ACOS hates CIDR
# ───────────────────────────────────────────

def normalize_static_ip(ip_raw: str) -> Tuple[str, str]:
    from utils.network import normalize_static_ip as _normalize
    ip_no_cidr, ip_cidr = _normalize(ip_raw)

    qlog(
        COMPONENT,
        f"IP normalized: {ip_raw} → {ip_no_cidr} ({ip_cidr})",
    )

    if not ip_no_cidr:
        qlog_error(
            COMPONENT,
            "Static IP address is empty after normalisation; refusing to continue.",
        )
        raise ValueError("Empty IP after normalisation")

    return ip_no_cidr, ip_cidr


# ─────────────────────────────
# Core orchestration
# ─────────────────────────────

def run_provision_vthunder(
    hostname: str,
    ip_raw: str,
    vmid: int,
    template: str,
    template_id: int,
    cpu: int,
    ram_mb: int,
    disk_gb: int,
    node: str,
    vm_type: str,
    acos_version_in: str,
    bridges: list,
    env_file: str = DEFAULT_ENV_FILE,
) -> int:

    qlog(
        COMPONENT,
        f"Starting vThunder provisioning: host={hostname}, ip_raw={ip_raw}, vmid={vmid}, "
        f"template={template}, node={node}, vm_type={vm_type}, "
        f"acos_version_in={acos_version_in}, bridges={bridges}, env={env_file}",
    )

    if vm_type != "vthunder":
        qlog_error(
            COMPONENT,
            f"provision_vthunder called with vm_type={vm_type!r} (expected 'vthunder').",
        )
        return 1

    env = load_env(env_file)
    if not env:
        qlog_error(COMPONENT, f"No env data loaded from {env_file}")
        return 2

    # Derive ACOS version from template name if not provided
    # e.g. "acos6-template" → "6.0"
    acos_version: str = acos_version_in
    if not acos_version:
        import re
        m = re.search(r"acos(\d+)", template)
        acos_version = f"{m.group(1)}.0" if m else ""

    qlog(
        COMPONENT,
        f"Resolved template {template!r} → vmid={template_id}, ACOS={acos_version}.",
    )

    static_ip_cidr = ip_raw
    if "/" in static_ip_cidr:
        static_ip = static_ip_cidr.split("/", 1)[0]
    else:
        static_ip = static_ip_cidr

    ip_no_cidr, ip_cidr = normalize_static_ip(ip_raw)

    vth_ssh_key = env.get("VTHUNDER_SSH_KEY", "")
    # Subnet-aware gateway/DNS lookup
    from config import subnet_for_ip
    subnet_info = subnet_for_ip(ip_raw)
    default_gateway = env.get("VTH_MGMT_GATEWAY") or (subnet_info["gateway"] if subnet_info else "10.1.55.254")
    default_dns = env.get("VTH_DNS_SERVER") or (subnet_info["dns"][0] if subnet_info else "10.1.55.10")

    vth_cfg: Dict[str, Any] = {
        "vm_id": vmid,
        "name": hostname,
        "node": node,
        "template_id": template_id,
        "datastore_id": DEFAULT_DATASTORE,
        "memory": ram_mb or ACOS_DEFAULT_RAM,
        "cores": cpu or ACOS_DEFAULT_CPU,
        "disk_size": disk_gb or ACOS_DEFAULT_DISK,
        "bridges": bridges,
        "ipv4_address": ip_cidr,
        "ipv4_gateway": default_gateway,
        "dns_servers": [default_dns],
        "ssh_username": env.get("VTH_SSH_USER", VTH_SSH_USER),
        "ssh_keys": [vth_ssh_key] if vth_ssh_key else [],
        "acos_version": acos_version,
        "tags": ["lab", "vthunder"],
    }

    merge_vthunder_config(hostname, vth_cfg, TFVARS_PATH)

    if terraform_init(TF_DIR, COMPONENT) != 0:
        return 1

    if terraform_apply(TF_DIR, hostname, vmid, COMPONENT, module_name="vthunder_vm") != 0:
        return 1

    mgmt_mac = get_mgmt_mac_from_terraform(hostname)
    if not mgmt_mac:
        return 1

    dhcp_prefix = env.get("VTH_DHCP_PREFIX", DEFAULT_DHCP_PREFIX)
    dhcp_start = int(env.get("VTH_DHCP_START", str(DEFAULT_DHCP_START)))
    dhcp_end = int(env.get("VTH_DHCP_END", str(DEFAULT_DHCP_END)))

    qlog(
        COMPONENT,
        f"Mgmt MAC for {hostname} is {mgmt_mac}; detecting DHCP IP in "
        f"{dhcp_prefix}.{dhcp_start}-{dhcp_end}…",
    )

    dhcp_ip = discover_dhcp_ip(
        mac_address=mgmt_mac,
        prefix=dhcp_prefix,
        start=dhcp_start,
        end=dhcp_end,
        timeout_seconds=600,
        probe_interval=3,
    )

    if not dhcp_ip:
        qlog_error(
            COMPONENT,
            f"Failed to detect DHCP IP for {hostname} via MAC {mgmt_mac}.",
        )
        return 1

    qlog_success(
        COMPONENT,
        f"Detected DHCP IP for {hostname}: {dhcp_ip}",
    )

    qlog(
        COMPONENT,
        f"Running unified vThunder bootstrap engine for {hostname}: "
        f"static={ip_raw}, dhcp={dhcp_ip}, acos={acos_version_in}…",
    )

    rc_boot = run_vthunder_bootstrap(
        hostname=hostname,
        static_ip_cidr=ip_cidr,
        dhcp_ip=dhcp_ip,
        acos_version=acos_version_in,
        env_file=env_file,
    )

    if rc_boot != 0:
        qlog_error(
            COMPONENT,
            f"Unified vThunder bootstrap FAILED for {hostname} (rc={rc_boot}).",
        )
        return rc_boot

    qlog_success(
        COMPONENT,
        f"Unified vThunder bootstrap succeeded for {hostname}.",
    )

    from config import cfg
    if cfg.FEATURES.get("dns", True):
        qlog(COMPONENT, f"Adding DNS record for vThunder {hostname}...")
        if not dns_add_record(ip_no_cidr, hostname):
            qlog_error(COMPONENT, f"DNS record creation failed for {hostname}")
            return 1
    else:
        qlog(COMPONENT, "DNS feature disabled — skipping DNS record creation")

    acos_type = f"acos{acos_version.split('.')[0]}" if acos_version else "acos"
    qlog(COMPONENT, f"Adding {hostname} to staging_vthunder inventory...")
    add_host_to_group(hostname, ip_no_cidr, "staging_vthunder", {"os_type": acos_type})
    qlog_success(COMPONENT, f"Added {hostname} to staging_vthunder in inventory.yml")

    if cfg.FEATURES.get("ipam", True):
        qlog(COMPONENT, f"Adding Netbox IPAM entry for {hostname}...")
        if not netbox_create_ip(ip_no_cidr, hostname):
            qlog_warning(COMPONENT, f"Netbox IPAM entry creation failed for {hostname} — non-fatal")
    else:
        qlog(COMPONENT, "IPAM feature disabled — skipping Netbox entry")

    qlog_success(
        COMPONENT,
        f"vThunder VM {hostname} ({ip_no_cidr}) provisioning complete.",
    )
    return 0

# ─────────────────────────────
# CLI entrypoint
# ─────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    init_correlation_id_from_env()
    argv = argv or sys.argv[1:]

    if len(argv) < 12:
        qlog_error(
            COMPONENT,
            "Usage: provision_vthunder.py HOSTNAME IP VMID TEMPLATE TEMPLATE_ID CPU RAM DISK NODE "
            "VM_TYPE ACOS_VERSION BRIDGES_JSON",
        )
        return 1

    hostname = argv[0]
    ip_raw = argv[1]
    vmid = int(argv[2])
    template = argv[3]
    template_id = int(argv[4])
    cpu = int(argv[5])
    ram_mb = int(argv[6])
    disk_gb = int(argv[7])
    node = argv[8]
    vm_type = argv[9]
    acos_version_in = argv[10]
    bridges_arg = argv[11]

    # Parse bridges - accepts JSON array or single bridge string for backwards compat
    try:
        bridges = json.loads(bridges_arg)
        if isinstance(bridges, str):
            bridges = [bridges]
    except json.JSONDecodeError:
        bridges = [bridges_arg]

    return run_provision_vthunder(
        hostname=hostname,
        ip_raw=ip_raw,
        vmid=vmid,
        template=template,
        template_id=template_id,
        cpu=cpu,
        ram_mb=ram_mb,
        disk_gb=disk_gb,
        node=node,
        vm_type=vm_type,
        acos_version_in=acos_version_in,
        bridges=bridges,
        env_file=DEFAULT_ENV_FILE,
    )

if __name__ == "__main__":
    raise SystemExit(main())

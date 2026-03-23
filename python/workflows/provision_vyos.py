#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

# ──────────────────────────────────────────────
# Ensure automation-demo/python is on sys.path
# ──────────────────────────────────────────────
THIS_FILE = Path(__file__).resolve()
PYTHON_DIR = THIS_FILE.parent.parent
ROOT_DIR = PYTHON_DIR.parent

if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

# ──────────────────────────────────────────────
# Internal imports (AFTER sys.path setup)
# ──────────────────────────────────────────────
import requests
from helpers.dns_manager import dns_add_record
from helpers.netbox_ipam import netbox_create_ip
from utils.inventory_yaml import add_host_to_group
from axapi.utils import qlog, qlog_success, qlog_warning, qlog_error
from utils.qlog import init_correlation_id_from_env
from utils.sops_env import load_env
from utils.tfvars_io import locked_update
from utils.terraform_runner import terraform_init, terraform_apply

COMPONENT = "VYOS-PROV"

# Workspace-specific Terraform directory
TF_DIR = ROOT_DIR / "terraform" / "vyos_vm"
TFVARS_PATH = TF_DIR / "perimeter-vyos.auto.tfvars.json"
DEFAULT_ENV_FILE = str(ROOT_DIR / "secrets" / "automation-demo.enc.env")

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
        data.setdefault("vyos_configs", {})[hostname] = vm_config

    locked_update(TFVARS_PATH, updater)
    qlog(COMPONENT, f"Saved tfvars to {TFVARS_PATH}")


def run_terraform(hostname: str, vmid: int) -> int:
    """Run terraform init + apply for VyOS VM workspace."""
    if terraform_init(TF_DIR, COMPONENT) != 0:
        return 1
    return terraform_apply(TF_DIR, hostname, vmid, COMPONENT, module_name="vyos_vm")


def poll_guest_agent_ip(vmid: int, node: str, timeout: int = 120) -> Optional[str]:
    """Poll Proxmox guest agent for a non-loopback IPv4 address."""
    env = load_env()
    token_id = env.get("PM_API_TOKEN_ID", "")
    token_secret = env.get("PM_API_TOKEN_SECRET", "")
    base_url = env.get("PM_API_URL", "https://goldfinger.home.klouda.co:8006/api2/json")
    headers = {"Authorization": f"PVEAPIToken={token_id}={token_secret}"}

    qlog(COMPONENT, f"Waiting for DHCP IP via guest agent (timeout={timeout}s)...")
    start = time.time()

    while time.time() - start < timeout:
        try:
            r = requests.get(
                f"{base_url}/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces",
                headers=headers, verify=False, timeout=10,
            )
            if r.status_code == 200:
                for iface in r.json().get("data", {}).get("result", []):
                    if iface.get("name") == "lo":
                        continue
                    for addr in iface.get("ip-addresses", []):
                        if addr.get("ip-address-type") == "ipv4":
                            ip = addr["ip-address"]
                            if not ip.startswith("127."):
                                qlog_success(COMPONENT, f"DHCP IP detected: {ip}")
                                return ip
        except Exception:
            pass
        time.sleep(5)

    qlog_error(COMPONENT, f"Timed out waiting for guest agent IP on VMID {vmid}")
    return None


def wait_for_ssh(host: str, port: int = 22, timeout: int = 120) -> bool:
    """Wait for SSH port to become available."""
    qlog(COMPONENT, f"Waiting for SSH on {host}:{port} (timeout={timeout}s)...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection((host, port), timeout=5):
                qlog_success(COMPONENT, f"SSH is ready on {host}")
                return True
        except (socket.timeout, socket.error, ConnectionRefusedError, OSError):
            time.sleep(5)
    qlog_error(COMPONENT, f"SSH not available on {host}:{port} after {timeout}s")
    return False


def vyos_bootstrap(dhcp_ip: str, static_ip_cidr: str, gateway: str, ssh_user: str) -> int:
    """SSH into VyOS and configure static IP, removing DHCP."""
    static_ip = static_ip_cidr.split("/")[0]
    prefix = static_ip_cidr.split("/")[1] if "/" in static_ip_cidr else "24"

    # VyOS 1.5: source script-template for configure alias, pipe commands via vbash -s
    # NOTE: Add static IP first WITHOUT deleting DHCP in this session.
    # Deleting DHCP while SSH'd on the DHCP address kills the connection mid-commit.
    # Static IP is added alongside DHCP, then a second SSH session (on static IP)
    # removes DHCP cleanly.
    vyos_script = "\n".join([
        "source /opt/vyatta/etc/functions/script-template",
        "configure",
        f"set interfaces ethernet eth0 address {static_ip}/{prefix}",
        f"set protocols static route 0.0.0.0/0 next-hop {gateway}",
        "commit",
        "save /config/config.boot",
        "exit",
    ])

    qlog(COMPONENT, f"Configuring static IP {static_ip}/{prefix} (replacing DHCP)...")

    try:
        result = subprocess.run(
            [
                "ssh", "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "ConnectTimeout=10",
                "-i", os.path.expanduser("~/.ssh/ansible_qbranch"),
                f"{ssh_user}@{dhcp_ip}",
                "vbash", "-s",
            ],
            input=vyos_script,
            capture_output=True, text=True, timeout=60,
        )

        if result.stdout.strip():
            qlog(COMPONENT, f"Bootstrap stdout: {result.stdout.strip()}")
        if result.stderr.strip():
            qlog(COMPONENT, f"Bootstrap stderr: {result.stderr.strip()}")

        if result.returncode != 0:
            qlog_error(COMPONENT, f"VyOS bootstrap phase 1 failed (rc={result.returncode})")
            return 1

        qlog_success(COMPONENT, f"Static IP added: {static_ip}/{prefix}, gateway {gateway}")

    except subprocess.TimeoutExpired:
        qlog_error(COMPONENT, "VyOS bootstrap phase 1 SSH timed out")
        return 1
    except Exception as e:
        qlog_error(COMPONENT, f"VyOS bootstrap phase 1 error: {e}")
        return 1

    # Phase 2: SSH to the static IP to remove DHCP
    qlog(COMPONENT, f"Removing DHCP via static IP {static_ip}...")

    if not wait_for_ssh(static_ip, timeout=30):
        qlog_warning(COMPONENT, f"Cannot reach static IP {static_ip} — skipping DHCP removal")
        return 0  # Non-fatal: static IP is configured, DHCP will expire

    dhcp_cleanup = "\n".join([
        "source /opt/vyatta/etc/functions/script-template",
        "configure",
        "delete interfaces ethernet eth0 address dhcp",
        "commit",
        "save /config/config.boot",
        "exit",
    ])

    try:
        result2 = subprocess.run(
            [
                "ssh", "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "ConnectTimeout=10",
                "-i", os.path.expanduser("~/.ssh/ansible_qbranch"),
                f"{ssh_user}@{static_ip}",
                "vbash", "-s",
            ],
            input=dhcp_cleanup,
            capture_output=True, text=True, timeout=30,
        )

        if result2.stdout.strip():
            qlog(COMPONENT, f"Phase 2 stdout: {result2.stdout.strip()}")
        if result2.stderr.strip():
            qlog(COMPONENT, f"Phase 2 stderr: {result2.stderr.strip()}")

        if result2.returncode == 0:
            qlog_success(COMPONENT, "DHCP removed, static-only configuration saved")
        else:
            qlog_warning(COMPONENT, f"DHCP removal returned non-zero (rc={result2.returncode})")

    except Exception:
        qlog_warning(COMPONENT, "DHCP removal failed — non-fatal, static IP is active")

    return 0


# ─────────────────────────────
# Main orchestration
# ─────────────────────────────

def provision_vyos_vm(
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
    Orchestrate VyOS VM provisioning:
    1. Load environment config
    2. Normalize IP to CIDR
    3. Merge VM config into tfvars
    4. Run Terraform (cloud-init handles hostname, SSH keys, user, DNS)
    5. Detect DHCP IP via guest agent
    6. SSH bootstrap: set static IP, gateway, remove DHCP
    7. Update Ansible inventory
    8. Add DNS record
    9. Add Netbox IPAM entry

    Returns:
        0 on success, 1 on failure
    """
    qlog(
        COMPONENT,
        f"Provisioning VyOS VM: host={hostname} ip={ip} vmid={vmid} template={template} "
        f"cpu={cpu} ram={ram} disk={disk} node={node} bridges={bridges}",
    )

    env = load_env(env_file)
    if not env:
        qlog_error(COMPONENT, f"Failed to load environment from {env_file} — is SOPS configured?")
        return 1

    ssh_user = env.get("LINUX_SSH_USER", "mklouda")
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

    # Derive os_type from template name (e.g. "vyos15-template" → "vyos15")
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

    qlog_success(COMPONENT, f"Terraform apply completed for {hostname} (VMID {vmid})")

    # Detect DHCP IP via guest agent
    dhcp_ip = poll_guest_agent_ip(vmid, node)
    if not dhcp_ip:
        qlog_error(COMPONENT, f"Could not detect DHCP IP for {hostname}")
        return 1

    # Wait for SSH on the DHCP address
    if not wait_for_ssh(dhcp_ip):
        qlog_error(COMPONENT, f"SSH not available on {dhcp_ip}")
        return 1

    # SSH bootstrap: set static IP, remove DHCP
    rc_bootstrap = vyos_bootstrap(dhcp_ip, ip_cidr, gateway, ssh_user)
    if rc_bootstrap != 0:
        qlog_error(COMPONENT, f"VyOS bootstrap failed for {hostname}")
        return 1

    # Add to staging inventory
    qlog(COMPONENT, f"Adding {hostname} to staging_vyos inventory...")
    add_host_to_group(hostname, ip_no_cidr, "staging_vyos", {"os_type": os_type})
    qlog_success(COMPONENT, f"Added {hostname} to staging_vyos in inventory.yml")

    # DNS record
    from config import cfg
    if cfg.FEATURES.get("dns", True):
        qlog(COMPONENT, f"Adding DNS record for {hostname}...")
        if not dns_add_record(ip_no_cidr, hostname):
            qlog_error(COMPONENT, f"DNS record creation failed for {hostname}")
            return 1
    else:
        qlog(COMPONENT, "DNS feature disabled — skipping DNS record creation")

    # Netbox IPAM
    if cfg.FEATURES.get("ipam", True):
        qlog(COMPONENT, f"Adding Netbox IPAM entry for {hostname}...")
        if not netbox_create_ip(ip_no_cidr, hostname):
            qlog_warning(COMPONENT, f"Netbox IPAM entry creation failed for {hostname} — non-fatal")
    else:
        qlog(COMPONENT, "IPAM feature disabled — skipping Netbox entry")

    qlog_success(COMPONENT, f"VyOS VM {hostname} ({ip_no_cidr}) provisioning complete")
    return 0


def main() -> int:
    """CLI entrypoint matching Flask arguments."""
    init_correlation_id_from_env()

    if len(sys.argv) < 13:
        qlog_error(
            COMPONENT,
            "Usage: provision_vyos.py HOSTNAME IP VMID TEMPLATE TEMPLATE_ID CPU RAM DISK NODE VM_TYPE ACOS_VERSION BRIDGES_JSON",
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

    if vm_type != "vyos":
        qlog_error(COMPONENT, f"provision_vyos.py called with vm_type={vm_type} (expected vyos)")
        return 1

    return provision_vyos_vm(hostname, ip, vmid, template, template_id, cpu, ram, disk, node, bridges)

if __name__ == "__main__":
    sys.exit(main())

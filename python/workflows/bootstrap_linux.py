#!/usr/bin/env python3

from __future__ import annotations

import os
import subprocess
import sys
import time
import socket
from pathlib import Path

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
from axapi.utils import qlog, qlog_success, qlog_warning, qlog_error  # type: ignore
from utils.qlog import init_correlation_id_from_env

COMPONENT = "LINUX-BOOT"

INVENTORY_YML = ROOT_DIR / "inventories" / "inventory.yml"
BOOTSTRAP_PLAYBOOK = ROOT_DIR / "playbooks" / "01-linux-bootstrap.yml"
ANSIBLE_CFG = ROOT_DIR / "ansible.cfg"
SSH_KEY = Path(os.getenv("PERIMETER_SSH_KEY", os.path.expanduser("~/.ssh/ansible_perimeter")))

DEFAULT_SSH_TIMEOUT = 600


# ─────────────────────────────
# SSH wait helper
# ─────────────────────────────

def wait_for_ssh(host: str, port: int = 22, timeout: int = DEFAULT_SSH_TIMEOUT) -> bool:
    """
    Wait for SSH port to be available.

    Args:
        host: Target IP address
        port: SSH port (default 22)
        timeout: Max wait time in seconds

    Returns:
        True if SSH is available, False if timeout
    """
    qlog(COMPONENT, f"Waiting for SSH on {host}:{port} (timeout={timeout}s)...")
    start = time.time()

    while time.time() - start < timeout:
        try:
            with socket.create_connection((host, port), timeout=5):
                qlog_success(COMPONENT, f"SSH port {port} is open on {host}")
                return True
        except (socket.timeout, socket.error, ConnectionRefusedError, OSError):
            time.sleep(5)

    qlog_error(COMPONENT, f"SSH not available on {host}:{port} after {timeout}s")
    return False


# ─────────────────────────────
# Ansible bootstrap
# ─────────────────────────────

def run_ansible_bootstrap(hostname: str) -> int:
    """
    Run Ansible bootstrap playbook for target host.

    Args:
        hostname: Target hostname (must exist in inventory)

    Returns:
        0 on success, 1 on failure
    """
    if not BOOTSTRAP_PLAYBOOK.exists():
        qlog_error(COMPONENT, f"Playbook not found: {BOOTSTRAP_PLAYBOOK}")
        return 1

    if not INVENTORY_YML.exists():
        qlog_error(COMPONENT, f"Inventory not found: {INVENTORY_YML}")
        return 1

    qlog(COMPONENT, f"Running bootstrap playbook for {hostname}...")

    env = os.environ.copy()
    if ANSIBLE_CFG.exists():
        env["ANSIBLE_CONFIG"] = str(ANSIBLE_CFG)

    # Force ANSI color output for live streaming
    env["ANSIBLE_FORCE_COLOR"] = "1"
    env["ANSIBLE_STDOUT_CALLBACK"] = "default"

    result = subprocess.run(
        [
            "ansible-playbook",
            "-i", str(INVENTORY_YML),
            str(BOOTSTRAP_PLAYBOOK),
            "--limit", hostname,
        ],
        env=env,
        # Don't capture - let output stream directly to parent stdout
        stdout=None,
        stderr=None,
        text=True,
    )

    if result.returncode != 0:
        qlog_error(COMPONENT, f"Ansible bootstrap failed for {hostname}")
        return 1

    qlog_success(COMPONENT, f"Ansible bootstrap completed for {hostname}")
    return 0


# ─────────────────────────────
# Main bootstrap orchestration
# ─────────────────────────────

def run_linux_bootstrap(hostname: str, ip: str) -> int:
    """
    Bootstrap Linux VM:
    1. Wait for SSH availability
    2. Run Ansible bootstrap playbook

    Args:
        hostname: Target hostname
        ip: Target IP address

    Returns:
        0 on success, 1 on failure
    """
    qlog(COMPONENT, f"Bootstrap for Linux VM {hostname} ({ip}) started")

    # Wait for SSH
    if not wait_for_ssh(ip):
        qlog_error(COMPONENT, f"SSH timeout for {hostname} ({ip})")
        return 1

    # Additional sleep for cloud-init to settle
    qlog(COMPONENT, "Waiting 10s for cloud-init to settle...")
    time.sleep(10)

    # Run Ansible
    rc = run_ansible_bootstrap(hostname)

    if rc == 0:
        qlog_success(COMPONENT, f"Bootstrap completed for {hostname} ({ip})")
    else:
        qlog_error(COMPONENT, f"Bootstrap failed for {hostname} ({ip})")

    return rc


def main() -> int:
    """CLI entrypoint."""
    init_correlation_id_from_env()

    if len(sys.argv) < 3:
        qlog_error(COMPONENT, "Usage: bootstrap_linux.py HOSTNAME IP")
        return 1

    hostname = sys.argv[1]
    ip = sys.argv[2]

    return run_linux_bootstrap(hostname, ip)


if __name__ == "__main__":
    sys.exit(main())

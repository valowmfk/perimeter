"""
Template refresh workflow for Proxmox VM templates.

Clones a template → boots → updates via Ansible → cleans up → re-templates.
All operations use the Proxmox REST API (no SSH to the Proxmox host).
Linux templates only — ACOS/VyOS are vendor images.

Usage:
    python3 workflows/refresh_template.py <template_name> [--node goldfinger]
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from config import cfg
from axapi.utils import qlog, qlog_success, qlog_error, qlog_warning

COMPONENT = "TPL-REFRESH"
TEMP_VMID_OFFSET = 50000
REFRESH_LOG_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "template_refresh.json")
PLAYBOOK_DIR = os.path.join(cfg.ROOT_DIR, "playbooks")
SSH_KEY = os.getenv("PERIMETER_SSH_KEY", os.path.expanduser("~/.ssh/ansible_perimeter"))
SSH_USER = os.getenv("PERIMETER_SSH_USER", os.getenv("USER", "perimeter"))


def _pm_url(path: str) -> str:
    return f"{cfg.PM_API_URL}{path}"


def _pm_headers() -> dict:
    return cfg.pm_headers()


def _save_refresh_timestamp(template_name: str):
    """Record when a template was last refreshed."""
    import json
    os.makedirs(os.path.dirname(REFRESH_LOG_FILE), exist_ok=True)
    data = {}
    if os.path.exists(REFRESH_LOG_FILE):
        try:
            with open(REFRESH_LOG_FILE) as f:
                data = json.load(f)
        except Exception:
            pass
    data[template_name] = int(time.time())
    with open(REFRESH_LOG_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _pm_parse(r):
    """Safely parse Proxmox API response — handles empty bodies."""
    if r.status_code == 204 or not r.text.strip():
        return {}
    return r.json().get("data", r.json())


def _pm_get(path: str, timeout: int = 10):
    r = requests.get(_pm_url(path), headers=_pm_headers(), verify=False, timeout=timeout)
    r.raise_for_status()
    return _pm_parse(r)


def _pm_post(path: str, data: dict = None, timeout: int = 30):
    r = requests.post(_pm_url(path), headers=_pm_headers(), json=data or {},
                       verify=False, timeout=timeout)
    r.raise_for_status()
    return _pm_parse(r)


def _pm_put(path: str, data: dict = None, timeout: int = 10):
    r = requests.put(_pm_url(path), headers=_pm_headers(), json=data or {},
                      verify=False, timeout=timeout)
    r.raise_for_status()
    return _pm_parse(r)


def _pm_delete(path: str, timeout: int = 10):
    r = requests.delete(_pm_url(path), headers=_pm_headers(), verify=False, timeout=timeout)
    r.raise_for_status()
    return _pm_parse(r)


def _wait_for_task(node: str, upid: str, timeout: int = 300) -> bool:
    """Wait for a Proxmox task to complete."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            status = _pm_get(f"/nodes/{node}/tasks/{upid}/status")
            if status.get("status") == "stopped":
                return status.get("exitstatus") == "OK"
        except Exception:
            pass
        time.sleep(3)
    return False


def _wait_for_dhcp_ip(node: str, vmid: int, timeout: int = 120) -> str:
    """Poll QEMU guest agent for DHCP IP address."""
    print(f"[TPL-REFRESH] Waiting for DHCP IP via guest agent (timeout={timeout}s)...", flush=True)
    start = time.time()
    while time.time() - start < timeout:
        try:
            data = _pm_get(f"/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces")
            if isinstance(data, dict):
                interfaces = data.get("result", [])
            elif isinstance(data, list):
                interfaces = data
            else:
                interfaces = []
            for iface in interfaces:
                if iface.get("name", "") == "lo":
                    continue
                for addr in iface.get("ip-addresses", []):
                    if addr.get("ip-address-type") == "ipv4":
                        ip = addr.get("ip-address", "")
                        if ip and not ip.startswith("127."):
                            print(f"[TPL-REFRESH] ✔ DHCP IP detected: {ip}", flush=True)
                            return ip
        except Exception:
            pass
        time.sleep(5)
    return ""


def _wait_for_ssh(host: str, timeout: int = 120) -> bool:
    """Wait for SSH port to be available."""
    print(f"[TPL-REFRESH] Waiting for SSH on {host}:22 (timeout={timeout}s)...", flush=True)
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection((host, 22), timeout=5):
                print(f"[TPL-REFRESH] ✔ SSH is ready on {host}", flush=True)
                return True
        except (socket.timeout, socket.error, ConnectionRefusedError, OSError):
            time.sleep(5)
    return False


def _wait_for_stopped(node: str, vmid: int, timeout: int = 120) -> bool:
    """Wait for VM to reach stopped state."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            status = _pm_get(f"/nodes/{node}/qemu/{vmid}/status/current")
            if status.get("status") == "stopped":
                return True
        except Exception:
            pass
        time.sleep(5)
    return False


def _cleanup_temp_vm(node: str, vmid: int):
    """Best-effort cleanup of temp VM."""
    try:
        # Try to stop it first
        try:
            _pm_post(f"/nodes/{node}/qemu/{vmid}/status/stop")
            _wait_for_stopped(node, vmid, timeout=30)
        except Exception:
            pass
        _pm_delete(f"/nodes/{node}/qemu/{vmid}")
        print(f"[TPL-REFRESH] ⚠ Cleaned up temp VM {vmid}", flush=True)
    except Exception as e:
        print(f"[TPL-REFRESH] ✖ Failed to clean up temp VM {vmid}: {e}", flush=True)


def refresh_template(template_name: str, node: str = None):
    """
    Refresh a Linux VM template.

    Returns dict with step-by-step results.
    """
    node = node or cfg.PM_NODE
    results = {"steps": [], "success": True, "new_vmid": None}

    def _emit(msg):
        """Print directly to stdout for SSE streaming + qlog for journald."""
        print(msg, flush=True)

    def log_step(name, success, detail=""):
        results["steps"].append({"name": name, "success": success, "detail": detail})
        msg = f"{name}: {detail}" if detail else name
        if success:
            _emit(f"[TPL-REFRESH] ✔ {msg}")
        else:
            _emit(f"[TPL-REFRESH] ✖ {msg}")
            results["success"] = False

    def log(msg):
        _emit(f"[TPL-REFRESH] {msg}")

    # Discover template VMID
    log(f"Starting template refresh for '{template_name}' on node '{node}'")

    try:
        vms = _pm_get(f"/nodes/{node}/qemu")
        if isinstance(vms, dict):
            vms = []
    except Exception as e:
        log_step("Discover template", False, str(e))
        return results

    template_vmid = None
    for vm in vms:
        if vm.get("name") == template_name and vm.get("template", 0) == 1:
            template_vmid = vm.get("vmid")
            break

    if not template_vmid:
        log_step("Discover template", False, f"Template '{template_name}' not found")
        return results

    # Temp VMID: use 58000 range (separate from 59000 range where refreshed templates live)
    TEMP_VMID_MAP = {
        "rocky9-template": 58000,
        "ubuntu22-template": 58001,
        "rhel9-template": 58002,
    }
    temp_vmid = TEMP_VMID_MAP.get(template_name, 58000 + abs(hash(template_name)) % 100)

    # Safety: temp VMID must NEVER equal the template VMID
    if temp_vmid == template_vmid:
        temp_vmid = template_vmid + 1000

    log_step("Discover template", True, f"VMID={template_vmid}, temp={temp_vmid}")

    # Check if temp VM already exists (leftover from failed refresh)
    try:
        _pm_get(f"/nodes/{node}/qemu/{temp_vmid}/status/current")
        log(f"Temp VM {temp_vmid} already exists — cleaning up")
        _cleanup_temp_vm(node, temp_vmid)
        time.sleep(3)
    except Exception:
        pass  # Expected — temp VM shouldn't exist

    # 1. Clone template
    try:
        clone_data = _pm_post(f"/nodes/{node}/qemu/{template_vmid}/clone", {
            "newid": temp_vmid,
            "name": f"refresh-{template_name}",
            "full": 1,
        }, timeout=120)
        # Clone returns a task UPID
        upid = clone_data if isinstance(clone_data, str) else clone_data.get("data", "")
        if upid:
            log(f"Clone task started: {upid}")
            if not _wait_for_task(node, upid, timeout=300):
                log_step("Clone template", False, "Clone task did not complete")
                return results
        log_step("Clone template", True, f"{template_name} → VMID {temp_vmid}")
    except Exception as e:
        log_step("Clone template", False, str(e))
        return results

    # 2. Configure DHCP on temp VM (cloud-init needs IP config to activate network)
    try:
        _pm_put(f"/nodes/{node}/qemu/{temp_vmid}/config", {
            "ipconfig0": "ip=dhcp",
        })
        log_step("Configure DHCP", True, "ipconfig0=dhcp")
    except Exception as e:
        log_step("Configure DHCP", False, str(e))
        _cleanup_temp_vm(node, temp_vmid)
        return results

    # 3. Boot temp VM
    try:
        _pm_post(f"/nodes/{node}/qemu/{temp_vmid}/status/start")
        log_step("Boot temp VM", True, f"VMID {temp_vmid}")
    except Exception as e:
        log_step("Boot temp VM", False, str(e))
        _cleanup_temp_vm(node, temp_vmid)
        return results

    # 3. Wait for DHCP IP
    ip = _wait_for_dhcp_ip(node, temp_vmid, timeout=120)
    if not ip:
        log_step("Detect DHCP IP", False, "Timed out waiting for guest agent IP")
        _cleanup_temp_vm(node, temp_vmid)
        return results
    log_step("Detect DHCP IP", True, ip)

    # 4. Wait for SSH
    if not _wait_for_ssh(ip, timeout=120):
        log_step("Wait for SSH", False, f"SSH not available on {ip}")
        _cleanup_temp_vm(node, temp_vmid)
        return results
    log_step("Wait for SSH", True, ip)

    # 5. Run Ansible updates
    try:
        log("Running OS updates via Ansible...")
        update_cmd = [
            "ansible-playbook",
            "-i", f"{ip},",
            os.path.join(PLAYBOOK_DIR, "01-linux-updates.yml"),
            "-e", "allow_reboot=false",
            "--private-key", SSH_KEY,
            "-u", SSH_USER,
            "--become",
            "-e", f"ansible_become_password={os.environ.get('ANSIBLE_BECOME_PASS', '')}",
        ]
        proc = subprocess.run(update_cmd, capture_output=True, text=True, timeout=1200,
                              cwd=cfg.ROOT_DIR)
        if proc.returncode == 0:
            log_step("Run OS updates", True, "Updates completed")
        else:
            # Updates may return non-zero if no changes — check output
            if "changed=0" in proc.stdout or "ok=" in proc.stdout:
                log_step("Run OS updates", True, "No updates needed")
            else:
                log_step("Run OS updates", False, proc.stderr[-200:] if proc.stderr else "Unknown error")
                _cleanup_temp_vm(node, temp_vmid)
                return results
    except subprocess.TimeoutExpired:
        log_step("Run OS updates", False, "Timed out after 20 minutes")
        _cleanup_temp_vm(node, temp_vmid)
        return results

    # 6. Run cleanup
    try:
        log("Running pre-template cleanup...")
        # Detect package manager from template name
        if any(x in template_name for x in ["ubuntu", "debian"]):
            clean_cmd = "apt clean"
        else:
            clean_cmd = "dnf clean all"

        cleanup_shell = (
            f"{clean_cmd}; "
            "truncate -s 0 /var/log/*.log 2>/dev/null; "
            "cloud-init clean --logs 2>/dev/null; "
            "rm -f /etc/machine-id; "
            "true"
        )
        cleanup_cmd = [
            "ansible", "-i", f"{ip},", "all",
            "-m", "shell", "-a", cleanup_shell,
            "--become",
            "--private-key", SSH_KEY,
            "-u", SSH_USER,
            "-e", f"ansible_become_password={os.environ.get('ANSIBLE_BECOME_PASS', '')}",
        ]
        proc = subprocess.run(cleanup_cmd, capture_output=True, text=True, timeout=120,
                              cwd=cfg.ROOT_DIR)
        log_step("Pre-template cleanup", True, "Caches cleared, logs truncated, machine-id reset")
    except Exception as e:
        log_step("Pre-template cleanup", False, str(e))
        # Non-fatal — continue with shutdown

    # 7. Clear cloud-init DHCP config (so next clone starts fresh)
    try:
        _pm_put(f"/nodes/{node}/qemu/{temp_vmid}/config", {
            "ipconfig0": "",
        })
        log_step("Clear cloud-init DHCP", True)
    except Exception as e:
        log_step("Clear cloud-init DHCP", False, str(e))
        # Non-fatal — continue

    # 8. Shutdown temp VM
    try:
        _pm_post(f"/nodes/{node}/qemu/{temp_vmid}/status/shutdown")
        if _wait_for_stopped(node, temp_vmid, timeout=120):
            log_step("Shutdown temp VM", True)
        else:
            # Force stop if graceful shutdown didn't work
            _pm_post(f"/nodes/{node}/qemu/{temp_vmid}/status/stop")
            _wait_for_stopped(node, temp_vmid, timeout=30)
            log_step("Shutdown temp VM", True, "forced stop")
    except Exception as e:
        log_step("Shutdown temp VM", False, str(e))
        _cleanup_temp_vm(node, temp_vmid)
        return results

    # 8. Delete old template
    try:
        _pm_delete(f"/nodes/{node}/qemu/{template_vmid}")
        log_step("Delete old template", True, f"VMID {template_vmid}")
    except Exception as e:
        log_step("Delete old template", False, str(e))
        # Don't abort — try to convert anyway, old template may have already been replaced
        qlog_warning(COMPONENT, "Continuing despite delete failure...")

    # 9. Convert temp VM to template
    try:
        _pm_post(f"/nodes/{node}/qemu/{temp_vmid}/template")
        log_step("Convert to template", True, f"VMID {temp_vmid}")
    except Exception as e:
        log_step("Convert to template", False, str(e))
        return results

    # 10. Rename to original template name
    try:
        _pm_put(f"/nodes/{node}/qemu/{temp_vmid}/config", {"name": template_name})
        log_step("Rename template", True, f"→ {template_name}")
    except Exception as e:
        log_step("Rename template", False, str(e))
        # Non-fatal — template works, just has a different name

    results["new_vmid"] = temp_vmid
    _save_refresh_timestamp(template_name)
    log(f"✔ Template refresh complete! '{template_name}' is now VMID {temp_vmid}")
    return results


# CLI entry point
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Refresh a Proxmox VM template")
    parser.add_argument("template_name", help="Template name (e.g., rocky9-template)")
    parser.add_argument("--node", default=None, help="Proxmox node name (default: from PM_NODE env var)")
    parser.add_argument("--json", action="store_true", help="Print JSON result (CLI mode)")
    args = parser.parse_args()

    from utils.sops_env import load_env
    env = load_env()
    for k, v in env.items():
        os.environ.setdefault(k, v)

    result = refresh_template(args.template_name, args.node)
    if args.json:
        import json
        print(json.dumps(result, indent=2))
    sys.exit(0 if result["success"] else 1)

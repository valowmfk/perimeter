"""Thread-safe YAML inventory manipulation with file locking and comment preservation."""
from __future__ import annotations

import fcntl
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar

from ruamel.yaml import YAML

T = TypeVar("T")

# Paths
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
INVENTORY_YAML = ROOT_DIR / "inventories" / "inventory.yml"

# Round-trip YAML preserves comments, key order, and formatting
_yaml = YAML()
_yaml.preserve_quotes = True

# Staging groups where newly provisioned VMs land
STAGING_GROUPS = ("staging_linux", "staging_vthunder", "staging_vyos")

# os_type → suggested production group
OS_TYPE_GROUP_MAP: Dict[str, str] = {
    "rocky9": "prod_linux_dnf",
    "rhel9": "prod_linux_dnf",
    "ubuntu22": "prod_linux_apt",
    "vyos15": "prod_vyos",
}

# Groups that are valid promotion targets (leaf groups only, not parent/functional)
PROMOTABLE_GROUPS = (
    "prod_linux_dnf",
    "prod_linux_apt",
    "prod_docker",
    "k8s_linux",
    "prod_vthunder",
    "demo_vthunder",
    "demo_docker",
    "prod_vyos",
)


def _read_inventory(path: Path = INVENTORY_YAML) -> Any:
    """Read inventory YAML with round-trip parser."""
    with open(path, "r", encoding="utf-8") as fh:
        return _yaml.load(fh)


def _atomic_write(path: Path, data: Any) -> None:
    """Write YAML to a temp file, then atomically rename."""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            _yaml.dump(data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.rename(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _locked_inventory_update(
    updater: Callable[[Any], T],
    path: Path = INVENTORY_YAML,
) -> T:
    """Read-modify-write inventory.yml under exclusive file lock."""
    lock_path = Path(f"{path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with open(lock_path, "w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            data = _read_inventory(path)
            result = updater(data)
            _atomic_write(path, data)
            return result
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)


def _ensure_hosts_key(data: Any, group: str) -> None:
    """Ensure a group exists and has a 'hosts' mapping."""
    if group not in data:
        data[group] = {"hosts": {}}
    if data[group] is None:
        data[group] = {"hosts": {}}
    if "hosts" not in data[group]:
        data[group]["hosts"] = {}
    if data[group]["hosts"] is None:
        data[group]["hosts"] = {}


def add_host_to_group(
    hostname: str,
    ip: str,
    group: str,
    host_vars: Optional[Dict[str, str]] = None,
    path: Path = INVENTORY_YAML,
) -> None:
    """Add a host to a group in inventory.yml under file lock.

    Args:
        hostname: Inventory hostname key (e.g. 'qrocky224')
        ip: IP address for ansible_host var
        group: Target group (e.g. 'staging_linux')
        host_vars: Additional per-host variables (e.g. {'os_type': 'rocky9'})
        path: Inventory file path (default: inventories/inventory.yml)
    """
    def updater(data: Any) -> None:
        _ensure_hosts_key(data, group)
        entry: Dict[str, str] = {"ansible_host": ip}
        if host_vars:
            entry.update(host_vars)
        data[group]["hosts"][hostname] = entry

    _locked_inventory_update(updater, path)


def remove_host(hostname: str, path: Path = INVENTORY_YAML) -> Optional[str]:
    """Remove a host from all groups in inventory.yml.

    Returns the group name the host was found in, or None if not found.
    """
    def updater(data: Any) -> Optional[str]:
        for group_name, group_data in data.items():
            if not isinstance(group_data, dict):
                continue
            hosts = group_data.get("hosts")
            if not isinstance(hosts, dict):
                continue
            if hostname in hosts:
                del hosts[hostname]
                return group_name
        return None

    return _locked_inventory_update(updater, path)


def move_host(
    hostname: str,
    from_group: str,
    to_group: str,
    path: Path = INVENTORY_YAML,
) -> None:
    """Move a host from one group to another, preserving host vars.

    Raises:
        KeyError: If hostname not found in from_group or to_group doesn't exist
    """
    def updater(data: Any) -> None:
        # Validate from_group
        from_data = data.get(from_group, {})
        from_hosts = from_data.get("hosts", {}) if isinstance(from_data, dict) else {}
        if not isinstance(from_hosts, dict) or hostname not in from_hosts:
            raise KeyError(f"Host '{hostname}' not found in group '{from_group}'")

        # Validate to_group exists
        if to_group not in data:
            raise KeyError(f"Target group '{to_group}' does not exist in inventory")

        # Extract host entry (preserves all vars)
        host_entry = from_hosts.pop(hostname)

        # Add to target group
        _ensure_hosts_key(data, to_group)
        data[to_group]["hosts"][hostname] = host_entry

    _locked_inventory_update(updater, path)


def find_host_group(hostname: str, path: Path = INVENTORY_YAML) -> Optional[str]:
    """Find which leaf group a host belongs to.

    Skips parent/functional groups (all_linux, production, etc.) and returns
    the actual leaf group where the host is defined.
    """
    # Parent/functional groups that aggregate children — hosts aren't defined here
    AGGREGATE_GROUPS = {
        "all", "production", "staging", "demo", "proxmox",
        "all_linux", "all_vthunder", "all_docker", "all_dnf", "all_apt",
        "game_servers",
    }
    data = _read_inventory(path)
    for group_name, group_data in data.items():
        if group_name in AGGREGATE_GROUPS:
            continue
        if not isinstance(group_data, dict):
            continue
        hosts = group_data.get("hosts")
        if not isinstance(hosts, dict):
            continue
        if hostname in hosts:
            return group_name
    return None


def list_staging_hosts(path: Path = INVENTORY_YAML) -> List[Dict[str, Any]]:
    """List all hosts in staging groups with their vars.

    Returns list of dicts: [{"hostname": "...", "group": "...", "ip": "...", "os_type": "..."}]
    """
    data = _read_inventory(path)
    result: List[Dict[str, Any]] = []

    for group in STAGING_GROUPS:
        group_data = data.get(group, {})
        if not isinstance(group_data, dict):
            continue
        hosts = group_data.get("hosts")
        if not isinstance(hosts, dict):
            continue
        for hostname, host_vars in hosts.items():
            entry: Dict[str, Any] = {
                "hostname": hostname,
                "group": group,
            }
            if isinstance(host_vars, dict):
                entry["ip"] = host_vars.get("ansible_host", "")
                entry["os_type"] = host_vars.get("os_type", "")
                entry["suggested_group"] = OS_TYPE_GROUP_MAP.get(
                    host_vars.get("os_type", ""), ""
                )
            result.append(entry)

    return result

"""VM tracking helpers — shared between workflow scripts and Flask routes."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

VM_TRACK_FILE = Path(__file__).resolve().parent.parent.parent / "vm_track.json"


def _load() -> dict:
    if not VM_TRACK_FILE.exists():
        return {}
    try:
        return json.loads(VM_TRACK_FILE.read_text())
    except Exception:
        return {}


def _save(data: dict) -> None:
    try:
        VM_TRACK_FILE.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def mark_needs_cleanup(
    vmid: int,
    reason: str,
    *,
    hostname: Optional[str] = None,
    ip: Optional[str] = None,
    vm_type: Optional[str] = None,
) -> None:
    """Flag a VM as needing cleanup after a partial provisioning failure.

    The UI surfaces these VMs with a warning badge and a 'Clean Up' button
    that triggers the full destroy chain (terraform destroy + DNS/Netbox/
    tfvars/inventory/SSH key removal).
    """
    track = _load()
    vid = str(vmid)
    entry = track.setdefault(vid, {"first_seen": int(time.time())})
    entry["last_seen"] = int(time.time())
    entry["status"] = "needs_cleanup"
    entry["cleanup_reason"] = reason
    if hostname:
        entry["hostname"] = hostname
    if ip:
        entry["ip"] = ip
    if vm_type:
        entry["vm_type"] = vm_type
    _save(track)


def clear_cleanup_status(vmid: int) -> None:
    """Remove the needs_cleanup flag after successful cleanup or manual resolution."""
    track = _load()
    vid = str(vmid)
    if vid in track:
        track[vid].pop("status", None)
        track[vid].pop("cleanup_reason", None)
        track[vid].pop("hostname", None)
        track[vid].pop("ip", None)
        track[vid].pop("vm_type", None)
        _save(track)


def remove_vm_tracking(vmid: int) -> None:
    """Remove a VM entry from tracking entirely (after full cleanup)."""
    track = _load()
    vid = str(vmid)
    if vid in track:
        del track[vid]
        _save(track)

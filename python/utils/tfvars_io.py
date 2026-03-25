"""Thread-safe and process-safe tfvars.json I/O with file locking and atomic writes."""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, TypeVar

T = TypeVar("T")

# Default empty structure for new tfvars files
_SKELETON: Dict[str, Any] = {"vm_configs": {}, "vthunder_configs": {}}


def read_tfvars(path: Path) -> Dict[str, Any]:
    """Read and parse a tfvars JSON file. Returns skeleton if file is missing."""
    if not path.exists():
        return dict(_SKELETON)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _atomic_write(path: Path, data: Dict[str, Any]) -> None:
    """Write JSON to a temp file in the same directory, then atomically rename."""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.rename(tmp, str(path))
    except BaseException:
        # Clean up temp file on any failure
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def locked_update(
    path: Path,
    updater: Callable[[Dict[str, Any]], T],
) -> T:
    """
    Read-modify-write a tfvars file under an exclusive file lock.

    `updater` receives the current data dict and may mutate it in place.
    Its return value is passed back to the caller.

    The lock file is `<path>.lock` — a separate file so readers of the
    tfvars JSON itself are never blocked by a stale lock.

    Example::

        def add_vm(data):
            data["vm_configs"]["myhost"] = {...}

        locked_update(TFVARS_PATH, add_vm)
    """
    lock_path = Path(f"{path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with open(lock_path, "w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            data = read_tfvars(path)
            result = updater(data)
            _atomic_write(path, data)
            return result
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)


def merge_vm_config(
    hostname: str,
    vm_config: Dict[str, Any],
    tfvars_path: Path,
    section: str = "vm_configs",
) -> None:
    """Add or update a VM config in a tfvars file with locking and atomic write.

    Args:
        hostname: VM hostname (key in the section dict).
        vm_config: Full VM configuration dict.
        tfvars_path: Path to the .auto.tfvars.json file.
        section: Top-level key in tfvars ("vm_configs", "vyos_configs", "vthunder_configs").
    """
    def updater(data: Dict[str, Any]) -> None:
        data.setdefault(section, {})[hostname] = vm_config

    locked_update(tfvars_path, updater)

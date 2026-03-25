"""VM type registry — single source of truth for per-type paths and keys."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class VMTypeInfo:
    """Metadata for a VM type (linux, vyos, vthunder)."""

    name: str
    tfvars_section: str      # key inside the tfvars JSON ("vm_configs", etc.)
    terraform_module: str    # Terraform module name ("linux_vm", etc.)
    tfvars_path: Path
    terraform_dir: Path


def _build_registry() -> dict[str, VMTypeInfo]:
    """Build the registry lazily to avoid import-time cfg dependency."""
    from config import cfg

    return {
        "linux": VMTypeInfo(
            name="linux",
            tfvars_section="vm_configs",
            terraform_module="linux_vm",
            tfvars_path=cfg.TF_LINUX_TFVARS_PATH,
            terraform_dir=cfg.TERRAFORM_DIR / "linux_vm",
        ),
        "vyos": VMTypeInfo(
            name="vyos",
            tfvars_section="vyos_configs",
            terraform_module="vyos_vm",
            tfvars_path=cfg.TF_VYOS_TFVARS_PATH,
            terraform_dir=cfg.TERRAFORM_DIR / "vyos_vm",
        ),
        "vthunder": VMTypeInfo(
            name="vthunder",
            tfvars_section="vthunder_configs",
            terraform_module="vthunder_vm",
            tfvars_path=cfg.TF_VTHUNDER_TFVARS_PATH,
            terraform_dir=cfg.TERRAFORM_DIR / "vthunder_vm",
        ),
    }


def get_vm_type(vm_type: str) -> VMTypeInfo:
    """Look up metadata for a VM type. Raises ValueError for unknown types."""
    registry = _build_registry()
    if vm_type not in registry:
        raise ValueError(f"Unknown VM type: {vm_type!r} (expected one of {list(registry)})")
    return registry[vm_type]


def all_vm_types() -> list[VMTypeInfo]:
    """Return metadata for all known VM types."""
    return list(_build_registry().values())


def find_vm_in_tfvars(vmid: int) -> Optional[tuple[str, str, str]]:
    """Search all workspace tfvars for a VM by VMID.

    Returns:
        (vm_type, hostname_key, ip) or None if not found.
    """
    from utils.tfvars_io import read_tfvars

    for vt in all_vm_types():
        data = read_tfvars(vt.tfvars_path)
        for key, config in data.get(vt.tfvars_section, {}).items():
            if config.get("vm_id") == vmid:
                ip_cidr = config.get("ipv4_address", "")
                ip = ip_cidr.split("/")[0] if "/" in ip_cidr else ip_cidr
                return (vt.name, key, ip)
    return None

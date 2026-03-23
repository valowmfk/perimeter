"""Shared Terraform orchestration helpers used by provisioning workflows."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from axapi.utils import qlog, qlog_success, qlog_error


def terraform_init(tf_dir: Path, component: str) -> int:
    """Run terraform init -upgrade in the given directory. Returns exit code."""
    qlog(component, "Running terraform init...")
    proc = subprocess.run(
        ["terraform", "init", "-upgrade", "-input=false"],
        cwd=str(tf_dir),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        qlog_error(component, f"Terraform init failed:\n{proc.stderr}")
    else:
        qlog_success(component, "Terraform init completed.")
    return proc.returncode


def terraform_apply(
    tf_dir: Path,
    hostname: str,
    vmid: int,
    component: str,
    module_name: str = "linux_vm",
) -> int:
    """Run terraform apply -auto-approve targeting only the specific VM module instance.

    Uses -target to avoid touching other VMs in the workspace, preventing
    drift-related failures on existing VMs that were modified outside Terraform.
    """
    target = f'module.{module_name}["{hostname}"]'
    qlog(component, f"Applying Terraform for {hostname} (VMID {vmid}), target={target}...")
    proc = subprocess.run(
        ["terraform", "apply", "-auto-approve", f"-target={target}"],
        cwd=str(tf_dir),
        stdout=None,
        stderr=None,
        text=True,
    )
    if proc.returncode != 0:
        qlog_error(component, f"Terraform apply failed for {hostname} (exit code {proc.returncode})")
    else:
        qlog_success(component, f"Terraform apply completed for {hostname} (VMID {vmid})")
    return proc.returncode


def terraform_output_json(tf_dir: Path, output_name: str, component: str) -> Optional[Dict[str, Any]]:
    """Run terraform output -json and return parsed dict, or None on failure."""
    qlog(component, f"Retrieving Terraform output '{output_name}'...")
    try:
        proc = subprocess.run(
            ["terraform", "output", "-json", output_name],
            cwd=str(tf_dir),
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        qlog_error(component, "terraform binary not found in PATH.")
        return None

    if proc.returncode != 0:
        qlog_error(
            component,
            f"terraform output {output_name} failed (exit {proc.returncode}). "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}",
        )
        return None

    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        qlog_error(component, f"Failed to parse {output_name} JSON: {exc}. raw={proc.stdout!r}")
        return None

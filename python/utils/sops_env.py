"""SOPS-aware environment file loader."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Dict

ENCRYPTED_ENV = str(Path(__file__).resolve().parent.parent.parent / "secrets" / "automation-demo.enc.env")

# Resolve sops binary: prefer explicit path (systemd doesn't have ~/.local/bin in PATH)
_SOPS_CANDIDATES = [
    Path.home() / ".local" / "bin" / "sops",
    Path("/usr/local/bin/sops"),
]


def _find_sops() -> str:
    """Find sops binary, checking known locations then falling back to PATH."""
    for candidate in _SOPS_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    return shutil.which("sops") or "sops"


_SOPS_BIN = _find_sops()


def load_env(env_file: str = None) -> Dict[str, str]:
    """
    Load SOPS-encrypted env file and return parsed key-value dict.
    """
    enc_path = env_file or ENCRYPTED_ENV
    if Path(enc_path).exists():
        try:
            result = subprocess.run(
                [_SOPS_BIN, "-d", enc_path],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return _parse_env(result.stdout)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass  # sops not installed or timed out

    return {}


def _parse_env(text: str) -> Dict[str, str]:
    """Parse KEY=VALUE lines from env file content."""
    env: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            env[k] = v
    return env

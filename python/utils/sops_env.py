"""SOPS-aware environment file loader."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Dict

ENCRYPTED_ENV = str(Path(__file__).resolve().parent.parent.parent / "secrets" / "perimeter.enc.env")

# Resolve sops binary: prefer explicit path (systemd doesn't have ~/.local/bin in PATH)
_SOPS_CANDIDATES = [
    Path.home() / ".local" / "bin" / "sops",
    Path("/usr/local/bin/sops"),
]

# Decryption timeout — generous enough to survive boot-time contention.
# On failure we raise (and systemd's Restart=on-failure retries) rather than
# booting half-configured with empty secrets.
_SOPS_TIMEOUT = 15


class SopsEnvError(RuntimeError):
    """Raised when an expected SOPS-encrypted env file cannot be decrypted."""


def _find_sops() -> str:
    """Find sops binary, checking known locations then falling back to PATH."""
    for candidate in _SOPS_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    return shutil.which("sops") or "sops"


_SOPS_BIN = _find_sops()


def load_env(env_file: str = None, required: bool = True) -> Dict[str, str]:
    """
    Decrypt a SOPS-encrypted env file and return its KEY=VALUE pairs.

    Fails fast and loud: any decryption failure raises ``SopsEnvError`` so the
    caller crashes visibly instead of silently running with empty secrets (the
    2026-06-12 Proxmox outage, where a transient sops hiccup left the service
    half-configured for four days).

    Pass ``required=False`` to opt into best-effort behavior — returns ``{}`` on
    any failure — for contexts that legitimately run without secrets (tests,
    fresh checkouts, CI without an age key).
    """
    enc_path = env_file or ENCRYPTED_ENV

    if not Path(enc_path).exists():
        if required:
            raise SopsEnvError(f"secrets file not found: {enc_path}")
        return {}

    try:
        result = subprocess.run(
            [_SOPS_BIN, "-d", enc_path],
            capture_output=True, text=True, timeout=_SOPS_TIMEOUT,
        )
    except FileNotFoundError as e:
        if required:
            raise SopsEnvError(
                f"sops binary not found ({_SOPS_BIN}); cannot decrypt {enc_path}"
            ) from e
        return {}
    except subprocess.TimeoutExpired as e:
        if required:
            raise SopsEnvError(
                f"sops timed out after {_SOPS_TIMEOUT}s decrypting {enc_path}"
            ) from e
        return {}

    if result.returncode != 0:
        if required:
            stderr = (result.stderr or "").strip()
            raise SopsEnvError(
                f"sops failed (exit {result.returncode}) decrypting {enc_path}: {stderr}"
            )
        return {}

    return _parse_env(result.stdout)


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

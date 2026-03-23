"""
Miscellaneous helpers for AXAPI tooling:
- Perimeter logging
- URL helpers
- Environment helpers
"""

from __future__ import annotations

import os
import json
from typing import Any, Optional

from utils.qlog import get_logger


# ─────────────────────────────
# Logging helpers
# ─────────────────────────────

def qlog(component: str, message: str) -> None:
    """Emit a structured INFO log line with Perimeter formatting."""
    get_logger().info(message, extra={"component": component})


def qlog_success(component: str, message: str) -> None:
    """Emit a structured INFO log line prefixed with a checkmark."""
    get_logger().info(f"✔ {message}", extra={"component": component})


def qlog_warning(component: str, message: str) -> None:
    """Emit a structured WARNING log line."""
    get_logger().warning(f"⚠ {message}", extra={"component": component})


def qlog_error(component: str, message: str) -> None:
    """Emit a structured ERROR log line."""
    get_logger().error(f"✖ {message}", extra={"component": component})


# ─────────────────────────────
# URL / JSON helpers
# ─────────────────────────────

def build_base_url(host: str, *, https: bool = True, port: Optional[int] = None) -> str:
    """
    Construct base URL (scheme + host + optional port).

    Example:
        https://10.1.55.50:443
    """
    scheme = "https" if https else "http"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if port:
        return f"{scheme}://{host}:{port}"
    return f"{scheme}://{host}"


def pretty_json(data: Any) -> str:
    try:
        return json.dumps(data, indent=2, sort_keys=True)
    except TypeError:
        return str(data)


# ─────────────────────────────
# Environment helpers
# ─────────────────────────────

def get_env(name: str, default: Optional[str] = None, *, required: bool = False) -> str:
    """
    Fetch an environment variable with optional default and 'required' enforcement.
    """
    value = os.getenv(name, default)
    if value is None and required:
        raise RuntimeError(f"Required environment variable {name!r} is not set")
    return value  # type: ignore[return-value]

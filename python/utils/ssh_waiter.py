"""Shared SSH availability helper."""
from __future__ import annotations

import socket
import time

from axapi.utils import qlog, qlog_success, qlog_error


def wait_for_ssh(
    host: str,
    port: int = 22,
    timeout: int = 120,
    component: str = "SSH-WAIT",
) -> bool:
    """Wait for SSH port to become available on a target host.

    Args:
        host: Target IP address or hostname.
        port: SSH port (default 22).
        timeout: Max wait time in seconds.
        component: qlog component label for log messages.

    Returns:
        True if SSH is reachable, False on timeout.
    """
    qlog(component, f"Waiting for SSH on {host}:{port} (timeout={timeout}s)...")
    start = time.time()

    while time.time() - start < timeout:
        try:
            with socket.create_connection((host, port), timeout=5):
                qlog_success(component, f"SSH is ready on {host}:{port}")
                return True
        except (socket.timeout, socket.error, ConnectionRefusedError, OSError):
            time.sleep(5)

    qlog_error(component, f"SSH not available on {host}:{port} after {timeout}s")
    return False

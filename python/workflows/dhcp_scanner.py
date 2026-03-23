#!/usr/bin/env python3
"""
Perimeter vThunder DHCP Discovery

Responsibilities:
- Given a mgmt MAC address and a DHCP IP range, discover the DHCP address
  currently used by the vThunder mgmt interface.
- Strategy:
    1. Ping-sweep the range to populate ARP/neighbor table.
    2. Parse `ip neigh` (or `arp -an` fallback) to map IP→MAC.
    3. Match on normalized MAC.
    4. Retry until timeout.

Inputs:
- mac_address      (e.g. "DE:AD:BE:EF:12:34")
- ip_prefix        (e.g. "10.1.55")
- start_host       (e.g. 175)
- end_host         (e.g. 245)

Return:
- str DHCP IP (e.g. "10.1.55.210") on success
- None on failure (caller handles error/logging)

This is the Python replacement for the DHCP detection phase that used
to live in create_vthunder.sh + vth_detect_acos_ip.sh.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Optional

from axapi.utils import qlog, qlog_warning, qlog_error, qlog_success  # type: ignore

COMPONENT = "VTH-DHCP"


def _normalize_mac(mac: str) -> str:
    """Normalize MAC to lowercase colon-separated."""
    mac = mac.strip().lower()
    mac = mac.replace("-", ":")
    # Compress multiple separators if someone was creative
    parts = [p for p in mac.split(":") if p]
    return ":".join(parts)


def _run_cmd(cmd: list[str]) -> str:
    """Run a command and return stdout as text, or empty string on error."""
    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        return res.stdout or ""
    except Exception:
        return ""


def _ping_sweep(prefix: str, start: int, end: int, interval_ms: int = 50) -> None:
    """
    Light ping sweep to populate ARP/neighbor tables.

    We do NOT care about response times; we just want the kernel to
    resolve MACs for those IPs.
    """
    for host in range(start, end + 1):
        ip = f"{prefix}.{host}"
        # One small ping, no spam
        _ = _run_cmd(["ping", "-c", "1", "-W", "1", ip])
        # tiny delay to avoid flooding
        time.sleep(interval_ms / 1000.0)


def _scan_ip_neigh_for_mac(target_mac: str) -> Optional[str]:
    """
    Look for target_mac in `ip neigh`. Returns IP if found.
    Falls back to `arp -an` if ip neigh returns nothing useful.
    """
    target_mac = _normalize_mac(target_mac)

    # First try `ip neigh`
    neigh_out = _run_cmd(["ip", "neigh", "show"])
    if neigh_out:
        for line in neigh_out.splitlines():
            line = line.strip()
            if not line:
                continue
            # Typical: "10.1.55.210 dev vmbr0 lladdr de:ad:be:ef:12:34 REACHABLE"
            parts = line.split()
            if len(parts) < 5:
                continue

            ip = parts[0]
            # Defensive scan for something looking like a MAC
            mac = None
            for token in parts:
                if ":" in token and len(token.split(":")) == 6:
                    mac = token
                    break

            if mac and _normalize_mac(mac) == target_mac:
                return ip

    # Fallback: `arp -an`
    arp_out = _run_cmd(["arp", "-an"])
    if arp_out:
        for line in arp_out.splitlines():
            line = line.strip()
            if not line:
                continue
            # Typical: "? (10.1.55.210) at de:ad:be:ef:12:34 [ether] on vmbr0"
            if "(" not in line or ")" not in line:
                continue
            try:
                ip = line.split("(")[1].split(")")[0]
            except Exception:
                continue

            mac = None
            for token in line.split():
                if ":" in token and len(token.split(":")) == 6:
                    mac = token
                    break

            if mac and _normalize_mac(mac) == target_mac:
                return ip

    return None


def discover_dhcp_ip(
    mac: str = None,
    mac_address: str = None,
    prefix: str = "",
    start: int = 0,
    end: int = 0,
    timeout_seconds: int = 300,
    probe_interval: float = 1.0,
):
    """Discover a DHCP-assigned IP by MAC address via ping sweep + ARP/neighbor lookup."""
    if mac is None:
        mac = mac_address

    target = _normalize_mac(mac)
    qlog(COMPONENT, f"Starting DHCP discovery for MAC {target} in {prefix}.{start}-{end}")

    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        _ping_sweep(prefix, start, end)

        found = _scan_ip_neigh_for_mac(target)
        if found:
            qlog_success(COMPONENT, f"Matched MAC={target} → DHCP IP={found}")
            return found

        qlog(COMPONENT, f"MAC {target} not found yet, retrying in {probe_interval}s...")
        time.sleep(probe_interval)

    qlog_error(COMPONENT, f"Failed to detect DHCP IP for MAC {target} within {timeout_seconds}s timeout.")
    return None

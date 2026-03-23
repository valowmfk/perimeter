"""Shared network/IP utilities for Perimeter Automation Platform."""

from typing import Tuple


def normalize_ip_cidr(ip_raw: str) -> str:
    """Add CIDR notation if not present, using subnet config.

    Returns IP with CIDR (e.g., '10.1.55.50/24').
    """
    if "/" in ip_raw:
        return ip_raw
    from config import subnet_for_ip
    subnet_info = subnet_for_ip(ip_raw)
    cidr = subnet_info["cidr"] if subnet_info else "24"
    return f"{ip_raw}/{cidr}"


def normalize_static_ip(ip_raw: str) -> Tuple[str, str]:
    """Normalize IP to (ip_without_cidr, ip_with_cidr) tuple.

    Returns e.g., ('10.1.55.50', '10.1.55.50/24').
    """
    if "/" in ip_raw:
        ip_cidr = ip_raw
        ip_no_cidr = ip_raw.split("/", 1)[0]
    else:
        ip_no_cidr = ip_raw
        ip_cidr = normalize_ip_cidr(ip_raw)
    return ip_no_cidr, ip_cidr

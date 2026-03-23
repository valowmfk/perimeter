"""
Netbox IPAM management module for Perimeter automation.

Creates and deletes IP address entries in Netbox during VM provisioning
and destruction, keeping IPAM in sync with DNS records.
"""

from __future__ import annotations

import os

import requests

from axapi.utils import qlog, qlog_success, qlog_warning, qlog_error

COMPONENT = "NETBOX-IPAM"

NETBOX_URL = os.getenv("NETBOX_URL", "https://netbox.home.klouda.co")
NETBOX_API_TOKEN = os.getenv("NETBOX_API_TOKEN", "")


def _headers() -> dict:
    return {
        "Authorization": f"Token {NETBOX_API_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def netbox_create_ip(ip: str, hostname: str) -> bool:
    """
    Create an IP address entry in Netbox IPAM.

    Args:
        ip: IP address without CIDR (e.g. "10.1.55.100")
        hostname: Short hostname (e.g. "web-01")

    Returns:
        True on success or if entry already exists, False on error.
    """
    if not NETBOX_API_TOKEN:
        qlog_warning(COMPONENT, "NETBOX_API_TOKEN not set — skipping IPAM create")
        return True  # Non-fatal; don't block provisioning

    from config import subnet_for_ip
    subnet_info = subnet_for_ip(ip)
    cidr = subnet_info["cidr"] if subnet_info else "24"
    address = f"{ip}/{cidr}"
    payload = {
        "address": address,
        "status": "active",
        "dns_name": hostname,
        "description": hostname,
    }

    qlog(COMPONENT, f"Creating Netbox IP: {address} ({hostname})")

    try:
        r = requests.post(
            f"{NETBOX_URL}/api/ipam/ip-addresses/",
            headers=_headers(),
            json=payload,
            verify=True,
            timeout=10,
        )

        if r.status_code == 201:
            qlog_success(COMPONENT, f"Netbox IP created: {address} ({hostname})")
            return True

        # Duplicate address — update existing entry instead
        if r.status_code == 400:
            body = r.json() if r.content else {}
            errors = body.get("address", [])
            if any("already exists" in str(e).lower() for e in errors):
                qlog(COMPONENT, f"IP {address} already exists in Netbox — updating")
                return _netbox_update_existing(ip, hostname)

        qlog_error(COMPONENT, f"Netbox create failed ({r.status_code}): {r.text}")
        return False

    except Exception as e:
        qlog_error(COMPONENT, f"Netbox create error: {e}")
        return False


def _netbox_update_existing(ip: str, hostname: str) -> bool:
    """Update an existing Netbox IP entry with the new hostname."""
    entry_id = _find_ip_id(ip)
    if not entry_id:
        qlog_warning(COMPONENT, f"Could not find existing Netbox entry for {ip} to update")
        return True  # Non-fatal

    try:
        r = requests.patch(
            f"{NETBOX_URL}/api/ipam/ip-addresses/{entry_id}/",
            headers=_headers(),
            json={"dns_name": hostname, "description": hostname, "status": "active"},
            verify=True,
            timeout=10,
        )
        if r.status_code == 200:
            qlog_success(COMPONENT, f"Netbox IP updated: {ip}/24 ({hostname})")
            return True

        qlog_error(COMPONENT, f"Netbox update failed ({r.status_code}): {r.text}")
        return False

    except Exception as e:
        qlog_error(COMPONENT, f"Netbox update error: {e}")
        return False


def _find_ip_id(ip: str) -> int | None:
    """Find the Netbox entry ID for a given IP address."""
    try:
        r = requests.get(
            f"{NETBOX_URL}/api/ipam/ip-addresses/",
            headers=_headers(),
            params={"address": f"{ip}/24"},
            verify=True,
            timeout=10,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if results:
            return results[0]["id"]
        return None
    except Exception as e:
        qlog_warning(COMPONENT, f"Netbox lookup error for {ip}: {e}")
        return None


def netbox_delete_ip(ip: str) -> bool:
    """
    Delete an IP address entry from Netbox IPAM.

    Args:
        ip: IP address without CIDR (e.g. "10.1.55.100")

    Returns:
        True on success or if entry not found (idempotent), False on error.
    """
    if not NETBOX_API_TOKEN:
        qlog_warning(COMPONENT, "NETBOX_API_TOKEN not set — skipping IPAM delete")
        return True

    qlog(COMPONENT, f"Deleting Netbox IP: {ip}/24")

    entry_id = _find_ip_id(ip)
    if not entry_id:
        qlog_success(COMPONENT, f"Netbox IP {ip}/24 not found (already removed)")
        return True

    try:
        r = requests.delete(
            f"{NETBOX_URL}/api/ipam/ip-addresses/{entry_id}/",
            headers=_headers(),
            verify=True,
            timeout=10,
        )

        if r.status_code in (200, 204):
            qlog_success(COMPONENT, f"Netbox IP deleted: {ip}/24")
            return True

        if r.status_code == 404:
            qlog_success(COMPONENT, f"Netbox IP {ip}/24 already removed")
            return True

        qlog_error(COMPONENT, f"Netbox delete failed ({r.status_code}): {r.text}")
        return False

    except Exception as e:
        qlog_error(COMPONENT, f"Netbox delete error: {e}")
        return False

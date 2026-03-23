#!/usr/bin/env python3
"""
Pi-hole DNS management module for Perimeter automation.

Uses the Pi-hole v6 REST API (FTL) to manage local DNS host records.
Nebula-sync on DNS Master replicates changes to production pi-holes.
"""

from __future__ import annotations

import os
import subprocess
import urllib.parse
from pathlib import Path

import requests

from axapi.utils import qlog, qlog_success, qlog_warning, qlog_error

COMPONENT = "DNS"

# ────────────────────────────────────────────
# Pi-hole Configuration
# ────────────────────────────────────────────
from config import cfg
DNS_DOMAIN = cfg.DNS_DOMAIN

PIHOLE_API_URL = os.getenv("PIHOLE_API_URL", "http://10.1.55.9")
PIHOLE_API_PASSWORD = os.getenv("PIHOLE_API_PASSWORD", "")

SSH_KEY = os.getenv("PERIMETER_SSH_KEY", str(Path.home() / ".ssh" / "ansible_qbranch"))

NEBULA_SYNC = {
    "host": os.getenv("NEBULA_SYNC_HOST", ""),
    "container": os.getenv("NEBULA_SYNC_CONTAINER", "nebula-sync"),
}


# ────────────────────────────────────────────
# Pi-hole v6 API session management
# ────────────────────────────────────────────

def _pihole_auth() -> dict | None:
    """
    Authenticate with Pi-hole v6 API via POST /api/auth.
    Returns {"sid": ..., "csrf": ...} or None on failure.
    """
    try:
        r = requests.post(
            f"{PIHOLE_API_URL}/api/auth",
            json={"password": PIHOLE_API_PASSWORD},
            timeout=10,
        )
        r.raise_for_status()
        session = r.json().get("session", {})
        sid = session.get("sid", "")
        csrf = session.get("csrf", "")
        if sid and csrf:
            return {"sid": sid, "csrf": csrf}
        qlog_error(COMPONENT, "Pi-hole auth: no session returned")
        return None
    except Exception as e:
        qlog_error(COMPONENT, f"Pi-hole auth failed: {e}")
        return None


def _pihole_logout(auth: dict) -> None:
    """Best-effort logout to clean up the API session."""
    try:
        requests.delete(
            f"{PIHOLE_API_URL}/api/auth",
            headers={"X-FTL-SID": auth["sid"], "X-FTL-CSRF": auth["csrf"]},
            timeout=5,
        )
    except Exception:
        pass


# ────────────────────────────────────────────
# DNS record operations via Pi-hole v6 API
# ────────────────────────────────────────────

def _pihole_add_host(ip: str, fqdn: str) -> bool:
    """Add a DNS host record via PUT /api/config/dns/hosts/{IP FQDN}."""
    if not PIHOLE_API_PASSWORD:
        qlog_error(COMPONENT, "PIHOLE_API_PASSWORD not set")
        return False

    auth = _pihole_auth()
    if not auth:
        return False

    try:
        # Pi-hole v6 API: IP and FQDN in URL path, separated by %20
        entry = urllib.parse.quote(f"{ip} {fqdn}")
        r = requests.put(
            f"{PIHOLE_API_URL}/api/config/dns/hosts/{entry}",
            headers={"X-FTL-SID": auth["sid"], "X-FTL-CSRF": auth["csrf"]},
            timeout=10,
        )

        if r.status_code in (200, 201):
            qlog_success(COMPONENT, f"DNS record added: {fqdn} → {ip}")
            return True

        # Check for "already present" — idempotent success
        body = r.json() if r.content else {}
        err = body.get("error", {})
        msg = err.get("message", "") if isinstance(err, dict) else ""
        hint = err.get("hint", "") if isinstance(err, dict) else ""
        combined = f"{msg} {hint}".lower()
        if "already" in combined:
            qlog_success(COMPONENT, f"DNS record already exists: {fqdn} → {ip}")
            return True

        qlog_error(COMPONENT, f"Pi-hole add failed ({r.status_code}): {r.text}")
        return False

    except Exception as e:
        qlog_error(COMPONENT, f"Pi-hole add error: {e}")
        return False
    finally:
        _pihole_logout(auth)


def _pihole_remove_host(ip: str, fqdn: str) -> bool:
    """Remove a DNS host record via DELETE /api/config/dns/hosts/{IP FQDN}."""
    if not PIHOLE_API_PASSWORD:
        qlog_error(COMPONENT, "PIHOLE_API_PASSWORD not set")
        return False

    auth = _pihole_auth()
    if not auth:
        return False

    try:
        entry = urllib.parse.quote(f"{ip} {fqdn}")
        r = requests.delete(
            f"{PIHOLE_API_URL}/api/config/dns/hosts/{entry}",
            headers={"X-FTL-SID": auth["sid"], "X-FTL-CSRF": auth["csrf"]},
            timeout=10,
        )

        if r.status_code in (200, 204):
            qlog_success(COMPONENT, f"DNS record removed: {fqdn}")
            return True

        # Not found is still success for idempotent removal
        if r.status_code == 404:
            qlog_success(COMPONENT, f"DNS record not found (already removed): {fqdn}")
            return True

        qlog_error(COMPONENT, f"Pi-hole remove failed ({r.status_code}): {r.text}")
        return False

    except Exception as e:
        qlog_error(COMPONENT, f"Pi-hole remove error: {e}")
        return False
    finally:
        _pihole_logout(auth)


def _pihole_find_record_by_fqdn(fqdn: str) -> list[tuple[str, str]]:
    """
    Look up all records matching a given FQDN.
    Returns list of (ip, fqdn) tuples found.
    """
    auth = _pihole_auth()
    if not auth:
        return []

    try:
        r = requests.get(
            f"{PIHOLE_API_URL}/api/config/dns/hosts",
            headers={"X-FTL-SID": auth["sid"]},
            timeout=10,
        )
        r.raise_for_status()
        hosts = r.json().get("config", {}).get("dns", {}).get("hosts", [])
        matches = []
        for entry in hosts:
            parts = entry.split(None, 1)
            if len(parts) == 2 and parts[1] == fqdn:
                matches.append((parts[0], parts[1]))
        return matches
    except Exception as e:
        qlog_warning(COMPONENT, f"Pi-hole lookup error: {e}")
        return []
    finally:
        _pihole_logout(auth)


# ────────────────────────────────────────────
# CNAME record operations via Pi-hole v6 API
# ────────────────────────────────────────────

def _pihole_add_cname(alias: str, target: str) -> bool:
    """Add a CNAME record via PUT /api/config/dns/cnameRecords/{alias,target}."""
    if not PIHOLE_API_PASSWORD:
        qlog_error(COMPONENT, "PIHOLE_API_PASSWORD not set")
        return False

    auth = _pihole_auth()
    if not auth:
        return False

    try:
        entry = urllib.parse.quote(f"{alias},{target}")
        r = requests.put(
            f"{PIHOLE_API_URL}/api/config/dns/cnameRecords/{entry}",
            headers={"X-FTL-SID": auth["sid"], "X-FTL-CSRF": auth["csrf"]},
            timeout=10,
        )

        if r.status_code in (200, 201):
            qlog_success(COMPONENT, f"CNAME record added: {alias} → {target}")
            return True

        body = r.json() if r.content else {}
        err = body.get("error", {})
        msg = err.get("message", "") if isinstance(err, dict) else ""
        hint = err.get("hint", "") if isinstance(err, dict) else ""
        combined = f"{msg} {hint}".lower()
        if "already" in combined:
            qlog_success(COMPONENT, f"CNAME record already exists: {alias} → {target}")
            return True

        qlog_error(COMPONENT, f"Pi-hole CNAME add failed ({r.status_code}): {r.text}")
        return False

    except Exception as e:
        qlog_error(COMPONENT, f"Pi-hole CNAME add error: {e}")
        return False
    finally:
        _pihole_logout(auth)


def _pihole_remove_cname(alias: str, target: str) -> bool:
    """Remove a CNAME record via DELETE /api/config/dns/cnameRecords/{alias,target}."""
    if not PIHOLE_API_PASSWORD:
        qlog_error(COMPONENT, "PIHOLE_API_PASSWORD not set")
        return False

    auth = _pihole_auth()
    if not auth:
        return False

    try:
        entry = urllib.parse.quote(f"{alias},{target}")
        r = requests.delete(
            f"{PIHOLE_API_URL}/api/config/dns/cnameRecords/{entry}",
            headers={"X-FTL-SID": auth["sid"], "X-FTL-CSRF": auth["csrf"]},
            timeout=10,
        )

        if r.status_code in (200, 204):
            qlog_success(COMPONENT, f"CNAME record removed: {alias}")
            return True

        if r.status_code == 404:
            qlog_success(COMPONENT, f"CNAME record not found (already removed): {alias}")
            return True

        qlog_error(COMPONENT, f"Pi-hole CNAME remove failed ({r.status_code}): {r.text}")
        return False

    except Exception as e:
        qlog_error(COMPONENT, f"Pi-hole CNAME remove error: {e}")
        return False
    finally:
        _pihole_logout(auth)


def _pihole_find_cname_by_alias(alias: str) -> list[tuple[str, str]]:
    """
    Look up all CNAME records matching a given alias.
    Returns list of (alias, target) tuples found.
    """
    auth = _pihole_auth()
    if not auth:
        return []

    try:
        r = requests.get(
            f"{PIHOLE_API_URL}/api/config/dns/cnameRecords",
            headers={"X-FTL-SID": auth["sid"]},
            timeout=10,
        )
        r.raise_for_status()
        records = r.json().get("config", {}).get("dns", {}).get("cnameRecords", [])
        matches = []
        for entry in records:
            parts = entry.split(",", 1)
            if len(parts) == 2 and parts[0] == alias:
                matches.append((parts[0], parts[1]))
        return matches
    except Exception as e:
        qlog_warning(COMPONENT, f"Pi-hole CNAME lookup error: {e}")
        return []
    finally:
        _pihole_logout(auth)


# ────────────────────────────────────────────
# Nebula-sync trigger (still via SSH)
# ────────────────────────────────────────────

def _trigger_nebula_sync() -> bool:
    """Restart nebula-sync container to trigger immediate replication.
    Skips silently if NEBULA_SYNC_HOST is not configured."""
    host = NEBULA_SYNC["host"]
    container = NEBULA_SYNC["container"]

    if not host:
        return True  # No sync configured — not an error

    try:
        subprocess.run(
            [
                "ssh",
                "-i", SSH_KEY,
                "-o", "StrictHostKeyChecking=accept-new",
                host,
                f"docker restart {container}"
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        qlog_success(COMPONENT, f"Nebula-sync triggered on {host}")
        return True

    except subprocess.CalledProcessError as e:
        qlog_warning(COMPONENT, f"Nebula-sync trigger failed: {e}")
        return False
    except Exception as e:
        qlog_warning(COMPONENT, f"Nebula-sync error: {e}")
        return False


# ────────────────────────────────────────────
# Public API (unchanged signatures)
# ────────────────────────────────────────────

def dns_add_record(ip: str, hostname: str, domain: str = DNS_DOMAIN) -> bool:
    """
    Add DNS record to Pi-hole master via v6 API.
    Nebula-sync replicates to production pi-holes.
    """
    # Strip domain suffix if user included it
    domain_suffix = f".{domain}"
    if hostname.endswith(domain_suffix):
        hostname = hostname[:-len(domain_suffix)]

    fqdn = f"{hostname}.{domain}"

    qlog(COMPONENT, f"DNS ADD: {ip} {fqdn}")

    # Remove any stale record for this FQDN first (idempotent update)
    existing = _pihole_find_record_by_fqdn(fqdn)
    for old_ip, old_fqdn in existing:
        if old_ip != ip:
            qlog(COMPONENT, f"Removing stale record: {old_fqdn} → {old_ip}")
            _pihole_remove_host(old_ip, old_fqdn)

    if _pihole_add_host(ip, fqdn):
        qlog_success(COMPONENT, f"DNS record added to master: {fqdn} → {ip}")
        _trigger_nebula_sync()
        return True
    else:
        qlog_error(COMPONENT, "DNS record failed on master")
        return False


def dns_remove_record(ip: str, hostname: str, domain: str = DNS_DOMAIN) -> bool:
    """
    Remove DNS record from Pi-hole master via v6 API.
    Nebula-sync replicates to production pi-holes.
    """
    # Strip domain suffix if user included it
    domain_suffix = f".{domain}"
    if hostname.endswith(domain_suffix):
        hostname = hostname[:-len(domain_suffix)]

    fqdn = f"{hostname}.{domain}"

    qlog(COMPONENT, f"DNS REMOVE: {ip} {fqdn}")

    if _pihole_remove_host(ip, fqdn):
        qlog_success(COMPONENT, f"DNS record removed from master: {fqdn}")
        _trigger_nebula_sync()
        return True
    else:
        qlog_error(COMPONENT, "DNS record removal failed on master")
        return False


def dns_add_cname(alias: str, target: str) -> bool:
    """
    Add CNAME record to Pi-hole master via v6 API.
    Alias must be a fully qualified domain name.
    Nebula-sync replicates to production pi-holes.
    """
    qlog(COMPONENT, f"CNAME ADD: {alias} → {target}")

    # Remove any stale CNAME for this alias first
    existing = _pihole_find_cname_by_alias(alias)
    for old_alias, old_target in existing:
        if old_target != target:
            qlog(COMPONENT, f"Removing stale CNAME: {old_alias} → {old_target}")
            _pihole_remove_cname(old_alias, old_target)

    if _pihole_add_cname(alias, target):
        qlog_success(COMPONENT, f"CNAME record added to master: {alias} → {target}")
        _trigger_nebula_sync()
        return True
    else:
        qlog_error(COMPONENT, "CNAME record failed on master")
        return False


def dns_remove_cname(alias: str, target: str) -> bool:
    """
    Remove CNAME record from Pi-hole master via v6 API.
    Alias must be a fully qualified domain name.
    Nebula-sync replicates to production pi-holes.
    """
    qlog(COMPONENT, f"CNAME REMOVE: {alias} → {target}")

    if _pihole_remove_cname(alias, target):
        qlog_success(COMPONENT, f"CNAME record removed from master: {alias}")
        _trigger_nebula_sync()
        return True
    else:
        qlog_error(COMPONENT, "CNAME record removal failed on master")
        return False


# ────────────────────────────────────────────
# CLI mode (optional)
# ────────────────────────────────────────────
def main() -> int:
    """CLI entrypoint for standalone usage."""
    import sys

    if len(sys.argv) < 4:
        qlog_error(COMPONENT, "Usage: dns_manager.py add <IP> <HOSTNAME>")
        qlog_error(COMPONENT, "       dns_manager.py remove <IP> <HOSTNAME>")
        return 1

    action = sys.argv[1]
    ip = sys.argv[2]
    hostname = sys.argv[3]

    if action == "add":
        success = dns_add_record(ip, hostname)
    elif action == "remove":
        success = dns_remove_record(ip, hostname)
    else:
        qlog_error(COMPONENT, f"Invalid action: {action}. Use 'add' or 'remove'.")
        return 1

    return 0 if success else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())

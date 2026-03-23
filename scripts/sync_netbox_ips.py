#!/usr/bin/env python3
"""Scan 10.1.55.0/24, resolve DNS, sync results to Netbox"""

import subprocess
import sys
import requests
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from python.utils.sops_env import load_env

SUBNET = "10.1.55.0/24"

env = load_env()

NETBOX_URL = env.get("NETBOX_URL", "")
NETBOX_TOKEN = env.get("NETBOX_API_TOKEN", "")

if not NETBOX_URL or not NETBOX_TOKEN:
    print("ERROR: NETBOX_URL or NETBOX_API_TOKEN not found in environment")
    sys.exit(1)

HEADERS = {
    "Authorization": f"Token {NETBOX_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

def scan_subnet(subnet):
    """Ping-swee a subnet with namp and return live IPs"""
    print(f"[SCAN] Scanning {subnet} ...")

    result = subprocess.run(
        ["nmap", "-sn", subnet],
        capture_output=True,
        text=True,
    )

    if result.returncode !=0:
        print(f"ERROR: nmap failed: \n{result.stderr}")
        sys.exit(1)

    ips = []
    for line in result.stdout.splitlines():
        if "Nmap scan report for" in line:
            token = line.split()[-1]
            ip = token.strip("()")
            ips.append(ip)

    print(f"[SCAN Found] {len(ips)} live hosts")
    return ips

def resolve_dns(ip):
    """Reverse lookup wiht dig, return hostname or empty string"""
    result = subprocess.run(
        ["dig", "-x", ip, "+short"],
        capture_output=True,
        text=True,
        timeout=5,
    )

    hostname = result.stdout.strip().rstrip(".")

    return hostname

def delete_existing_ips(subnet):
    """Fetch all IPs from Netbox, then delete each one"""
    print(f"[NETBOX] Fetching existing IPs in {subnet} ...")

    url = f"{NETBOX_URL}/api/ipam/ip-addresses/"
    params = {"parent": subnet, "limit": 300}
    r = requests.get(url, headers=HEADERS, params=params, verify=True, timeout=10)
    r.raise_for_status()

    results = r.json().get("results", [])
    print(f"[NETBOX] Found {len(results)} existing IPs to delete")

    for entry in results:
        entry_id = entry["id"]
        address = entry.get("address", "?")
        del_url = f"{NETBOX_URL}/api/ipam/ip-addresses/{entry_id}/"
        dr = requests.delete(del_url, headers=HEADERS, verify=True, timeout=10)

        if dr.status_code == 204:
            print(f" Deleted {address}")
        else:
            print(f" WARNING: Delete {address} returned {dr.status_code}")

    print(f"[NETBOX] Cleanup complete")

def create_ip(ip, hostname):
    address = f"{ip}/24"

    payload = {
        "address": address,
        "status": "active",
        "dns_name": hostname,
        "description": hostname if hostname else "No DNS"
    }

    url = f"{NETBOX_URL}/api/ipam/ip-addresses/"
    r = requests.post(url, headers=HEADERS, json=payload, verify=True, timeout=10)

    if r.status_code ==201:
        label = hostname if hostname else "No DNS"
        print(f" Created {address:18s} {label}")
    else:
        print(f" WARNING: Create {address} returned {r.status_code}: {r.text[:100]}")

def main():
    print("=" * 60)
    print("  Netbox IP Sync — 10.1.55.0/24")
    print("=" * 60)

    live_ips = scan_subnet(SUBNET)

    print(f"\n[DNS] Resolving hostnames ...")
    discoveries = []
    for ip in sorted(live_ips):
        hostname = resolve_dns(ip)
        tag = hostname if hostname else "(no DNS)"
        print(f"  {ip:18s}  {tag}")
        discoveries.append((ip, hostname))

    print()
    delete_existing_ips(SUBNET)

    print(f"\n[NETBOX] Creating {len(discoveries)} IP entries ...")
    for ip, hostname in discoveries:
        create_ip(ip, hostname)

    print(f"\n{'=' * 60}")
    print(f"  Done! {len(discoveries)} IPs synced to Netbox")
    print(f"{'=' * 60}")

if __name__ == "__main__":
    main()

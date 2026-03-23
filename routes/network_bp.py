"""DNS and Netbox IPAM routes."""

import time

import requests
from flask import Blueprint, jsonify, request

from config import cfg
from .audit import audit_log
from .shared import (
    NETBOX_CACHE_TTL,
    NETBOX_URL,
    _netbox_cache,
    api_error,
    netbox_headers,
)
from helpers.dns_manager import dns_add_record, dns_add_cname

network_bp = Blueprint("network", __name__)


@network_bp.route("/api/dns/create", methods=["POST"])
def api_dns_create():
    data = request.get_json()
    record_type = data.get("record_type", "").upper()
    hostname = data.get("hostname", "").strip()
    if not hostname:
        return api_error("Hostname is required")

    if record_type == "A":
        ip = data.get("ip", "").strip()
        if not ip:
            return api_error("IP address is required for A records")
        import ipaddress
        try:
            ipaddress.IPv4Address(ip)
        except ValueError:
            return api_error(f"Invalid IPv4 address: {ip}")
        audit_log("dns_create", hostname, f"type=A ip={ip}")
        success = dns_add_record(ip, hostname)
        if success:
            return jsonify({"status": "ok", "record_type": "A", "hostname": hostname, "ip": ip})
        return api_error("Failed to create A record in Pi-hole", 500)

    elif record_type == "CNAME":
        target = data.get("target", "").strip()
        if not target:
            return api_error("Target is required for CNAME records")
        audit_log("dns_create", hostname, f"type=CNAME target={target}")
        success = dns_add_cname(hostname, target)
        if success:
            return jsonify({"status": "ok", "record_type": "CNAME", "hostname": hostname, "target": target})
        return api_error("Failed to create CNAME record in Pi-hole", 500)

    return api_error("Invalid record_type. Must be 'A' or 'CNAME'")


@network_bp.route("/api/netbox/ipam")
def api_netbox_ipam():
    subnet = request.args.get("subnet", cfg.NETBOX_SUBNET)
    cache_key = f"ipam_{subnet}"
    now = time.time()
    if _netbox_cache.get(cache_key) and (now - _netbox_cache.get(f"{cache_key}_ts", 0)) < NETBOX_CACHE_TTL:
        return jsonify(_netbox_cache[cache_key])

    headers = netbox_headers()
    if not headers:
        return api_error("Netbox API token not configured", 500)

    try:
        url = f"{NETBOX_URL}/api/ipam/ip-addresses/"
        params = {"parent": subnet, "limit": 300}
        r = requests.get(url, headers=headers, params=params, verify=False, timeout=10)
        r.raise_for_status()
        raw = r.json()
    except requests.exceptions.Timeout:
        return api_error("Netbox request timed out", 504)
    except requests.exceptions.ConnectionError:
        return api_error("Cannot connect to Netbox", 502)
    except Exception as e:
        return api_error(f"Netbox API error: {str(e)}", 500)

    ips = []
    for entry in raw.get("results", []):
        address_raw = entry.get("address", "")
        address = address_raw.split("/")[0] if "/" in address_raw else address_raw
        dns_name = entry.get("dns_name", "") or ""
        description = entry.get("description", "") or ""
        status = entry.get("status", {})
        status_value = status.get("value", "") if isinstance(status, dict) else str(status)
        status_label = status.get("label", status_value) if isinstance(status, dict) else str(status)
        device_name = ""
        assigned = entry.get("assigned_object")
        if assigned and isinstance(assigned, dict):
            device = assigned.get("device")
            if device and isinstance(device, dict):
                device_name = device.get("name", "")
        hostname = dns_name or description or device_name
        ips.append({
            "address": address, "hostname": hostname, "dns_name": dns_name,
            "description": description, "device": device_name,
            "status": status_value, "status_label": status_label,
        })

    def ip_sort_key(item):
        try:
            return tuple(int(o) for o in item["address"].split("."))
        except (ValueError, AttributeError):
            return (999, 999, 999, 999)

    ips.sort(key=ip_sort_key)

    result = {"subnet": subnet, "count": len(ips), "ips": ips}
    _netbox_cache[cache_key] = result
    _netbox_cache[f"{cache_key}_ts"] = now
    return jsonify(result)

#!/usr/bin/env python3
"""
Upload SSL certificate and key to A10 vThunder via AXAPI.

Usage:
    upload_cert_to_vthunder.py --host <host> --username <user> --password <pass> \
        --partition <partition> --cert-name <name> --cert-file <path> --key-file <path>
"""
import argparse
import json
import os
import sys

import requests

# Ensure python/ is on sys.path for axapi imports
_PYTHON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PYTHON_DIR not in sys.path:
    sys.path.insert(0, _PYTHON_DIR)

from axapi.utils import qlog, qlog_success, qlog_warning, qlog_error

COMPONENT = "VTH-CERT"

# Disable SSL warnings
requests.packages.urllib3.disable_warnings()


def authenticate(host, port, username, password):
    """Authenticate to vThunder and return auth token."""
    url = f"https://{host}:{port}/axapi/v3/auth"
    payload = {"credentials": {"username": username, "password": password}}
    resp = requests.post(url, json=payload, verify=False)
    resp.raise_for_status()
    data = resp.json()
    # Normal session_id (password already rotated)
    token = data.get("authresponse", {}).get("signature")
    if not token:
        # First-login signature token
        token = data.get("auth", {}).get("session_id")
    if not token:
        raise RuntimeError(f"No auth token found in response: {list(data.keys())}")
    return token


def switch_partition(host, port, token, partition):
    """Switch to specified partition."""
    url = f"https://{host}:{port}/axapi/v3/active-partition/{partition}"
    payload = {"active-partition": {"curr_part_name": partition}}
    resp = requests.post(url, json=payload, headers={"Authorization": f"A10 {token}"}, verify=False)
    resp.raise_for_status()
    return resp.json()


def upload_certificate(host, port, token, cert_name, cert_file):
    """Upload SSL certificate."""
    url = f"https://{host}:{port}/axapi/v3/file/ssl-cert"

    with open(cert_file, 'rb') as f:
        cert_content = f.read()

    json_payload = {
        "ssl-cert": {
            "file": cert_name,
            "action": "import",
            "file-handle": f"{cert_name}.pem",
            "certificate-type": "pem"
        }
    }

    files = {
        'json': ('blob', json.dumps(json_payload), 'application/json'),
        'file': (f"{cert_name}.pem", cert_content, 'application/octet-stream')
    }

    resp = requests.post(url, files=files, headers={"Authorization": f"A10 {token}"}, verify=False)
    resp.raise_for_status()
    return resp.json()


def upload_key(host, port, token, cert_name, key_file):
    """Upload SSL key."""
    url = f"https://{host}:{port}/axapi/v3/file/ssl-key"

    with open(key_file, 'rb') as f:
        key_content = f.read()

    json_payload = {
        "ssl-key": {
            "file": cert_name,
            "action": "import",
            "file-handle": f"{cert_name}.key"
        }
    }

    files = {
        'json': ('blob', json.dumps(json_payload), 'application/json'),
        'file': (f"{cert_name}.key", key_content, 'application/octet-stream')
    }

    resp = requests.post(url, files=files, headers={"Authorization": f"A10 {token}"}, verify=False)
    resp.raise_for_status()
    return resp.json()


def delete_certificate(host, port, token, cert_name):
    """Delete SSL certificate from vThunder (cleanup on key upload failure)."""
    url = f"https://{host}:{port}/axapi/v3/file/ssl-cert/{cert_name}"
    resp = requests.delete(url, headers={
        "Authorization": f"A10 {token}",
        "Content-Type": "application/json",
    }, verify=False)
    resp.raise_for_status()
    return resp.json()


def main():
    parser = argparse.ArgumentParser(description='Upload SSL cert/key to A10 vThunder')
    parser.add_argument('--host', required=True, help='vThunder host/IP')
    parser.add_argument('--port', type=int, default=443, help='vThunder AXAPI port')
    parser.add_argument('--username', required=True, help='vThunder username')
    parser.add_argument('--password', required=True, help='vThunder password')
    parser.add_argument('--partition', required=True, help='Target partition')
    parser.add_argument('--cert-name', required=True, help='Certificate name on vThunder')
    parser.add_argument('--cert-file', required=True, help='Path to certificate PEM file')
    parser.add_argument('--key-file', required=True, help='Path to private key PEM file')

    args = parser.parse_args()

    try:
        # Authenticate
        qlog(COMPONENT, f"Authenticating to {args.host}...")
        token = authenticate(args.host, args.port, args.username, args.password)
        qlog_success(COMPONENT, "Authentication successful")

        # Switch partition
        qlog(COMPONENT, f"Switching to partition: {args.partition}")
        switch_partition(args.host, args.port, token, args.partition)
        qlog_success(COMPONENT, "Partition switch successful")

        # Upload certificate
        qlog(COMPONENT, f"Uploading certificate: {args.cert_name}")
        cert_result = upload_certificate(args.host, args.port, token, args.cert_name, args.cert_file)
        qlog_success(COMPONENT, f"Certificate upload: {cert_result}")

        # Upload key — if this fails, clean up the orphaned cert
        qlog(COMPONENT, f"Uploading key: {args.cert_name}")
        try:
            key_result = upload_key(args.host, args.port, token, args.cert_name, args.key_file)
        except Exception as key_err:
            qlog_error(COMPONENT, f"Key upload failed: {key_err}")
            qlog_warning(COMPONENT, f"Cleaning up orphaned certificate: {args.cert_name}")
            try:
                delete_certificate(args.host, args.port, token, args.cert_name)
                qlog_success(COMPONENT, f"Orphaned certificate {args.cert_name} deleted")
            except Exception as cleanup_err:
                qlog_error(COMPONENT, f"Failed to clean up certificate: {cleanup_err}")
            raise key_err
        qlog_success(COMPONENT, f"Key upload: {key_result}")

        qlog_success(COMPONENT, "Certificate and key uploaded.")

        # Output JSON for Ansible to parse
        result = {
            "changed": True,
            "cert_name": args.cert_name,
            "cert_upload": cert_result,
            "key_upload": key_result
        }
        qlog(COMPONENT, f"ANSIBLE_RESULT: {json.dumps(result)}")

    except requests.exceptions.HTTPError as e:
        qlog_error(COMPONENT, f"HTTP Error: {e}")
        qlog_error(COMPONENT, f"Response: {e.response.text}")
        sys.exit(1)
    except Exception as e:
        qlog_error(COMPONENT, f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()

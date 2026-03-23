#!/usr/bin/env python3
"""
One-off script: Push RSA SSH pubkey to all reachable vThunder hosts via aXAPI.

Usage:
    python3 scripts/push_vthunder_sshkey.py
    python3 scripts/push_vthunder_sshkey.py --hosts vth-auto.home.klouda.co
    python3 scripts/push_vthunder_sshkey.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
import threading
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

# Add project root to path so we can import axapi
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

from axapi.client import AxapiClient, AxapiError
from utils.sops_env import load_env

# --- Config ---
_env = load_env()
PUBKEY_PATH = Path.home() / ".ssh" / "ansible_vthunder.pub"
QBRANCH_IP = _env.get("QBRANCH_IP", "10.1.55.15")
KEY_SERVER_PORT = 8099
ADMIN_USER = _env.get("VTH_DEFAULT_ADMIN_USER", "admin")
ADMIN_PASS = _env.get("VTH_ADMIN_PASS", "")

ALL_VTHUNDERS = [
    "vth-ssli.home.klouda.co",
    "vth-waf-dmz.home.klouda.co",
    "vth-socal1.home.klouda.co",
    "vth-socal2.home.klouda.co",
    "vth-den.home.klouda.co",
    "vth-auto.home.klouda.co",
]


class _QuietHTTPHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


def start_key_server(pubkey_path: Path, port: int) -> HTTPServer:
    handler = partial(_QuietHTTPHandler, directory=str(pubkey_path.parent))
    server = HTTPServer(("0.0.0.0", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def push_key_to_host(host: str, file_url: str, dry_run: bool = False) -> bool:
    print(f"  [{host}] ", end="", flush=True)

    if dry_run:
        print("SKIP (dry-run)")
        return True

    try:
        with AxapiClient(host=host, username=ADMIN_USER, password=ADMIN_PASS) as client:
            client.login()

            payload = {
                "ssh-pubkey": {
                    "import": 1,
                    "file-url": file_url,
                }
            }
            client.request(
                "POST",
                f"/axapi/v3/admin/{ADMIN_USER}/ssh-pubkey",
                json_body=payload,
                timeout=15,
            )
            print("KEY IMPORTED ", end="", flush=True)

            try:
                client.write_memory()
                print("+ SAVED")
            except AxapiError:
                print("(write-memory failed - signature token mode, key may not persist across reboot)")

            return True

    except AxapiError as exc:
        print(f"FAILED: {exc}")
        return False
    except Exception as exc:
        print(f"UNREACHABLE: {exc}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Push RSA SSH pubkey to vThunder hosts via aXAPI")
    parser.add_argument("--hosts", nargs="+", default=ALL_VTHUNDERS, help="Specific hosts to target")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    args = parser.parse_args()

    if not PUBKEY_PATH.exists():
        print(f"ERROR: Pubkey not found at {PUBKEY_PATH}")
        return 1

    file_url = f"http://{QBRANCH_IP}:{KEY_SERVER_PORT}/{PUBKEY_PATH.name}"

    print(f"Pubkey:     {PUBKEY_PATH}")
    print(f"Key URL:    {file_url}")
    print(f"Targets:    {len(args.hosts)} host(s)")
    print(f"Dry run:    {args.dry_run}")
    print()

    server = start_key_server(PUBKEY_PATH, KEY_SERVER_PORT)

    try:
        ok = 0
        fail = 0
        for host in args.hosts:
            if push_key_to_host(host, file_url, dry_run=args.dry_run):
                ok += 1
            else:
                fail += 1

        print(f"\nResults: {ok} ok, {fail} failed")
        return 0 if fail == 0 else 1

    finally:
        server.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())

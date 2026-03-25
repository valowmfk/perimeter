from __future__ import annotations

import json
import subprocess
import sys
import time
import threading
from dataclasses import dataclass
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests
from requests import RequestException

from axapi.client import AxapiClient, AxapiAuthError, AxapiError
from axapi.utils import qlog, qlog_success, qlog_warning, qlog_error
from utils.qlog import init_correlation_id_from_env
from utils.sops_env import load_env

COMPONENT = "VTH-BOOT"
DEFAULT_ENV_FILE = str(Path(__file__).resolve().parent.parent.parent / "secrets" / "perimeter.enc.env")




# ---------------------------------------------------------------------------
# Dataclass for environment knobs
# ---------------------------------------------------------------------------

@dataclass
class BootstrapEnv:
    default_user: str
    factory_pass: str
    admin_pass: str

    boot_grace: int
    engine_timeout: int
    login_grace: int
    pw_timeout: int
    pw_retry_interval: int
    post_pw_stabilize: int

    dns_primary: Optional[str]
    gateway: Optional[str]
    syslog_ip: Optional[str]
    auto_user: Optional[str]
    auto_pass: Optional[str]
    enable_polling_mode: bool
    reboot_after_bootstrap: bool
    radius_ip: Optional[str]
    radius_key: Optional[str]
    ssh_pubkey_path: Optional[str]
    perimeter_ip: Optional[str]

    @classmethod
    def from_raw(cls, raw: Dict[str, str]) -> "BootstrapEnv":
        return cls(
            default_user=raw.get("VTH_DEFAULT_ADMIN_USER", "admin"),
            factory_pass=raw.get("VTH_FACTORY_PASS", "a10"),
            admin_pass=raw.get("VTH_ADMIN_PASS", ""),
            boot_grace=int(raw.get("VTH_BOOT_GRACE", "180")),
            engine_timeout=int(raw.get("VTH_CM_TIMEOUT", "300")),
            login_grace=int(raw.get("VTH_LOGIN_GRACE", "45")),
            pw_timeout=int(raw.get("PW_TIMEOUT", "600")),
            pw_retry_interval=int(raw.get("PW_RETRY_INTERVAL", "6")),
            post_pw_stabilize=int(raw.get("VTH_POST_PW_STABILIZE", "30")),
            # Support both VTH_DNS_PRIMARY and VTH_DNS_SERVER
            dns_primary=raw.get("VTH_DNS_PRIMARY") or raw.get("VTH_DNS_SERVER") or None,
            gateway=raw.get("VTH_GATEWAY") or None,  # subnet-aware lookup at usage
            # Support both VTH_SYSLOG_IP and VTH_SYSLOG_SERVER
            syslog_ip=raw.get("VTH_SYSLOG_IP") or raw.get("VTH_SYSLOG_SERVER") or None,
            auto_user=raw.get("VTH_AUTOMATION_USER") or None,
            auto_pass=raw.get("VTH_AUTOMATION_PASS") or None,
            enable_polling_mode=raw.get("VTH_SHARED_POLLING_MODE", "1") == "1",
            reboot_after_bootstrap=raw.get("VTH_REBOOT_AFTER_BOOTSTRAP", "0") == "1",
            radius_ip=raw.get("VTH_RADIUS_IP") or None,
            radius_key=raw.get("VTH_RADIUS_KEY") or None,
            ssh_pubkey_path=raw.get("VTH_SSH_PUBKEY_PATH") or None,
            perimeter_ip=raw.get("PERIMETER_IP", raw.get("QBRANCH_IP")) or None,
        )


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def _split_cidr(ip_cidr: str) -> Tuple[str, str]:
    """
    Convert "10.1.55.45/24" → ("10.1.55.45", "255.255.255.0")
    """
    ip, cidr = ip_cidr.split("/", 1)
    cidr_int = int(cidr)
    mask = (0xFFFFFFFF << (32 - cidr_int)) & 0xFFFFFFFF
    netmask = ".".join(str((mask >> shift) & 0xFF) for shift in (24, 16, 8, 0))
    return ip, netmask


def _axapi_version_url(host: str) -> str:
    return f"https://{host}/axapi/v3/version"


def _axapi_auth_url(host: str) -> str:
    return f"https://{host}/axapi/v3/auth"


def _axapi_system_url(host: str) -> str:
    return f"https://{host}/axapi/v3/system"


def _axapi_admin_password_url(host: str) -> str:
    # Password-change endpoint confirmed from working shell script:
    #   /axapi/v3/admin/admin/password
    return f"https://{host}/axapi/v3/admin/admin/password"


def _axapi_mgmt_if_url(host: str) -> str:
    return f"https://{host}/axapi/v3/interface/management"


def _axapi_dns_primary_url(host: str) -> str:
    return f"https://{host}/axapi/v3/ip/dns/primary"


def _axapi_syslog_host_url(host: str) -> str:
    return f"https://{host}/axapi/v3/logging/host/ipv4addr"


def _axapi_admin_collection_url(host: str) -> str:
    return f"https://{host}/axapi/v3/admin"


def _axapi_polling_mode_url(host: str) -> str:
    return f"https://{host}/axapi/v3/system/shared-polling-mode"


def _axapi_write_memory_url(host: str) -> str:
    return f"https://{host}/axapi/v3/write/memory"


def _axapi_reboot_url(host: str) -> str:
    return f"https://{host}/axapi/v3/reboot"


def _axapi_logoff_url(host: str) -> str:
    return f"https://{host}/axapi/v3/logoff"


# ---------------------------------------------------------------------------
# Waiters
# ---------------------------------------------------------------------------

def wait_for_axapi_http(host: str, timeout_seconds: int = 600) -> None:
    """
    Wait until HTTPS /axapi/v3/version answers with any HTTP response.
    """
    url = _axapi_version_url(host)
    deadline = time.time() + timeout_seconds

    qlog(COMPONENT, f"Waiting for AXAPI HTTP stack at {host} (timeout={timeout_seconds}s)…")

    while time.time() < deadline:
        try:
            resp = requests.get(url, verify=False, timeout=6)
            if resp.status_code in (200, 401):
                qlog_success(COMPONENT, "AXAPI HTTP stack is online ({})".format(resp.status_code))
                return
        except RequestException as exc:
            qlog_warning(COMPONENT, f"HTTP connection failed ({exc}); retrying…")

        time.sleep(5)

    raise AxapiError(f"AXAPI HTTP stack at {host} did not come online in time")


def wait_for_axapi_auth(host: str, timeout_seconds: int = 300) -> None:
    """
    Wait until /axapi/v3/auth is issuing tokens for the factory credentials.
    """
    url = _axapi_auth_url(host)
    deadline = time.time() + timeout_seconds

    qlog(COMPONENT, f"Waiting for AXAPI auth readiness at {host} (timeout={timeout_seconds}s)…")

    while time.time() < deadline:
        try:
            resp = requests.post(
                url,
                json={"credentials": {"username": "admin", "password": "a10"}},
                verify=False,
                timeout=6,
            )
            txt = resp.text.strip()
            if not txt:
                time.sleep(5)
                continue

            try:
                data = resp.json()
            except Exception:
                time.sleep(5)
                continue

            sig = data.get("authresponse", {}).get("signature")
            if sig:
                qlog_success(COMPONENT, "AXAPI auth endpoint is issuing tokens.")
                return
        except RequestException as exc:
            qlog_warning(COMPONENT, f"AXAPI auth request failed ({exc}); retrying…")

        time.sleep(5)

    raise AxapiError(f"AXAPI auth endpoint at {host} did not become ready in time")


def wait_for_acos_engine_ready(host: str, timeout_seconds: int = 300, interval: int = 10) -> None:
    """
    Use /auth + /system to detect when the ACOS CM/engine is actually ready.

    Recognises the classic "System is not ready yet" fail pattern and keeps
    polling until /system returns a proper 'system' block.
    """
    auth_url = _axapi_auth_url(host)
    system_url = _axapi_system_url(host)

    # Initial boot wait - brand new VMs need ~120s before ACOS is responsive
    initial_boot_wait = 120
    qlog(COMPONENT, f"ACOS booting at {host}, waiting {initial_boot_wait}s before first connection attempt…")
    time.sleep(initial_boot_wait)

    deadline = time.time() + timeout_seconds
    qlog(COMPONENT, f"Checking ACOS engine readiness at {host} (timeout={timeout_seconds}s)…")

    while time.time() < deadline:
        try:
            auth_resp = requests.post(
                auth_url,
                json={"credentials": {"username": "admin", "password": "a10"}},
                verify=False,
                timeout=5,
            )
            try:
                auth_data = auth_resp.json()
            except Exception:
                time.sleep(interval)
                continue

            token = auth_data.get("authresponse", {}).get("signature")
            if not token:
                time.sleep(interval)
                continue

            sys_resp = requests.get(
                system_url,
                headers={"Authorization": f"A10 {token}"},
                verify=False,
                timeout=5,
            )
            txt = sys_resp.text.strip()
            if not txt:
                qlog_warning(COMPONENT, "ACOS readiness check: empty /system response — retrying…")
                time.sleep(interval)
                continue

            try:
                sys_data = sys_resp.json()
            except Exception:
                qlog_warning(COMPONENT, "ACOS readiness check: non-JSON /system response — retrying…")
                time.sleep(interval)
                continue

            if sys_data.get("response", {}).get("status") == "fail":
                msg = sys_data["response"].get("err", {}).get("msg", "")
                if "System is not ready yet" in msg:
                    qlog(COMPONENT, "ACOS not ready yet — retrying…")
                    time.sleep(interval)
                    continue

            if "system" in sys_data:
                qlog_success(COMPONENT, "ACOS engine reports ready.")
                # Extra safety: short grace period before first login attempt
                qlog(COMPONENT, "ACOS reported ready; waiting additional 30s before login…")
                time.sleep(30)
                return

            qlog(COMPONENT, "ACOS not ready yet — retrying…")
            time.sleep(interval)

        except RequestException:
            qlog_warning(COMPONENT, "ACOS readiness check error — retrying…")
            time.sleep(interval)

    qlog_error(COMPONENT, "ACOS never became ready in time.")
    raise AxapiError("ACOS engine did not become ready in time")


def wait_for_axapi_login_ready(
    host: str,
    username: str,
    password: str,
    *,
    timeout_seconds: int = 600,
) -> AxapiClient:
    """
    Wait for AxapiClient.login() to succeed against `host`.

    Returns a logged-in AxapiClient instance.
    """
    deadline = time.time() + timeout_seconds

    qlog(COMPONENT, f"Waiting for full AXAPI login readiness at {host} (timeout={timeout_seconds}s)…")

    last_error: Optional[str] = None

    while time.time() < deadline:
        client = AxapiClient(host=host, username=username, password=password, verify_ssl=False)
        try:
            client.login()
            qlog_success(COMPONENT, f"Authenticated to {host} (signature token acquired).")
            return client
        except AxapiAuthError as exc:
            last_error = str(exc)
            time.sleep(5)
        except AxapiError as exc:
            last_error = str(exc)
            time.sleep(5)
        except Exception as exc:
            last_error = repr(exc)
            time.sleep(5)

    raise AxapiError(f"AXAPI login to {host} did not become ready in time; last_error={last_error!r}")

# ---------------------------------------------------------------------------
# Phase 1 – Password init
# ---------------------------------------------------------------------------

def phase1_password_init(
    dhcp_ip: str,
    env: BootstrapEnv,
) -> None:
    """
    Phase 1: operate against the DHCP management IP with factory credentials
    to rotate the admin password to env.admin_pass.
    """

    if not env.admin_pass:
        qlog_error(COMPONENT, "VTH_ADMIN_PASS missing in env file — cannot proceed.")
        raise AxapiError("admin password missing")

    qlog(COMPONENT, f"Waiting {env.boot_grace}s to allow ACOS boot to settle before HTTP probing…")
    time.sleep(env.boot_grace)

    # Wait for HTTP + auth + engine readiness using factory credentials.
    wait_for_axapi_http(dhcp_ip)
    wait_for_axapi_auth(dhcp_ip)
    wait_for_acos_engine_ready(dhcp_ip, timeout_seconds=env.engine_timeout)

    # Now wait for full login with factory creds (admin/a10).
    factory_client = wait_for_axapi_login_ready(
        host=dhcp_ip,
        username=env.default_user,
        password=env.factory_pass,
        timeout_seconds=env.login_grace,
    )

    # Password-change loop
    qlog(
        COMPONENT,
        f"Submitting admin password change request… (timeout={env.pw_timeout}s)",
    )

    deadline = time.time() + env.pw_timeout

    while time.time() < deadline:
        try:
            payload = {
                "password": {
                    "password-in-module": env.admin_pass,
                    "encrypted-in-module": "false",
                }
            }

            try:
                data = factory_client.post(
                    "/axapi/v3/admin/admin/password",
                    body=payload,
                    timeout=10,
                )
            except AxapiAuthError as exc:
                qlog_warning(
                    COMPONENT,
                    f"PW_BRANCH: authentication error during password POST: {exc} — retrying…",
                )
                time.sleep(env.pw_retry_interval)
                continue
            except AxapiError as exc:
                qlog_warning(
                    COMPONENT,
                    f"PW_BRANCH: transport error during POST: {exc} — retrying…",
                )
                time.sleep(env.pw_retry_interval)
                continue

            # Look for classic fail shape
            if data.get("response", {}).get("status") == "fail":
                msg = data["response"].get("err", {}).get("msg", "")
                qlog_warning(COMPONENT, f"PW_BRANCH: status=fail msg='{msg}'")

                if "System is not ready yet" in msg:
                    qlog_warning(COMPONENT, "PW_BRANCH: CM not ready — retrying…")
                    time.sleep(env.pw_retry_interval)
                    continue

                qlog_warning(COMPONENT, "PW_BRANCH: failure — retrying…")
                time.sleep(env.pw_retry_interval)
                continue

            # Success shape
            if (
                "password" in data
                and data["password"].get("a10-url")
                == "/axapi/v3/admin/admin/password"
            ):
                qlog_success(COMPONENT, "Password-change request accepted by AXAPI.")
                break

            qlog_warning(COMPONENT, "PW_BRANCH: unexpected response shape — retrying…")
            time.sleep(env.pw_retry_interval)

        except Exception as exc:
            qlog_warning(COMPONENT, f"PW_BRANCH: exception during POST: {exc} — retrying…")
            time.sleep(env.pw_retry_interval)

    else:
        qlog_error(
            COMPONENT,
            f"Timed out waiting for password change after {env.pw_timeout}s.",
        )
        raise AxapiError("password change timed out")

    # Verify new password
    qlog(COMPONENT, "Verifying AXAPI login with new password…")
    new_client = wait_for_axapi_login_ready(
        host=dhcp_ip,
        username=env.default_user,
        password=env.admin_pass,
        timeout_seconds=env.login_grace,
    )
    new_client.logoff()
    qlog_success(COMPONENT, "New password verified successfully.")

    qlog_warning(
        COMPONENT,
        f"Waiting {env.post_pw_stabilize} seconds for AXAPI/APACHE/AAA stabilization after password rotation…",
    )
    time.sleep(env.post_pw_stabilize)


# ---------------------------------------------------------------------------
# Phase 1.5 + 2 – Static mgmt + hostname/DNS/syslog/user/polling/write-mem
# ---------------------------------------------------------------------------

def _configure_mgmt_interface(
    dhcp_ip: str,
    static_ip: str,
    netmask: str,
    gateway: str,
    env: BootstrapEnv,
) -> None:
    if not static_ip or not gateway:
        qlog_warning(
            COMPONENT,
            "Static IP or gateway missing; skipping management IP configuration.",
        )
        return

    payload = {
        "management": {
            "ip": {
                "dhcp": 0,
                "ipv4-address": static_ip,
                "ipv4-netmask": netmask,
                "default-gateway": gateway,
                "control-apps-use-mgmt-port": 1,
            },
        }
    }

    qlog(
        COMPONENT,
        f"Configuring mgmt interface static IP={static_ip}/{netmask}, gw={gateway} on {dhcp_ip}…",
    )

    client = AxapiClient(host=dhcp_ip, username=env.default_user, password=env.admin_pass, verify_ssl=False)
    try:
        client.login()
        qlog(COMPONENT, f"Authenticated to {dhcp_ip} with new password for mgmt IP config.")

        client.request("POST", "/axapi/v3/interface/management", json_body=payload, timeout=10)
        qlog_success(COMPONENT, f"Management IP config pushed to {dhcp_ip}.")
    except AxapiError as exc:
        qlog_warning(
            COMPONENT,
            f"Management IP push hit an error talking to {dhcp_ip}: {exc}. "
            f"This is often expected when ACOS flips the mgmt IP to {static_ip}. "
            "We will verify by logging in to the STATIC IP next.",
        )
    except RequestException as exc:
        qlog_warning(
            COMPONENT,
            f"Management IP push hit a transport error talking to {dhcp_ip}; "
            f"this is often expected when ACOS flips the mgmt IP to {static_ip}. "
            "We will verify by logging in to the STATIC IP next.",
        )
    finally:
        try:
            client.logoff()
        except Exception:
            pass


def _set_hostname_with_client(
    client: AxapiClient,
    hostname: str,
) -> None:
    """Set hostname using an existing authenticated client."""
    if not hostname:
        qlog_warning(COMPONENT, "No hostname provided; skipping hostname configuration.")
        return

    qlog(COMPONENT, f"Setting hostname to {hostname}…")
    try:
        payload = {"hostname": {"value": hostname}}
        client.request("POST", "/axapi/v3/hostname", json_body=payload, timeout=10)
        qlog_success(COMPONENT, f"Hostname set to {hostname}.")
    except AxapiError as exc:
        qlog_warning(COMPONENT, f"Failed to set hostname: {exc}")


def _ensure_dns_primary_with_client(
    client: AxapiClient,
    env: BootstrapEnv,
) -> None:
    """Configure DNS primary using an existing authenticated client."""
    if not env.dns_primary:
        qlog_warning(COMPONENT, "VTH_DNS_PRIMARY not set; skipping DNS primary configuration.")
        return

    try:
        payload = {"primary": {"ip-v4-addr": env.dns_primary}}
        client.request("POST", "/axapi/v3/ip/dns/primary", json_body=payload, timeout=10)
        qlog_success(COMPONENT, f"DNS primary updated → {env.dns_primary}.")
    except AxapiError as exc:
        qlog_warning(COMPONENT, f"Failed to configure DNS primary: {exc}")


def _ensure_syslog_host_with_client(
    client: AxapiClient,
    env: BootstrapEnv,
) -> None:
    """Configure syslog host using an existing authenticated client."""
    if not env.syslog_ip:
        qlog_warning(COMPONENT, "VTH_SYSLOG_IP not set; skipping syslog host configuration.")
        return

    try:
        payload = {
            "ipv4addr": {
                "host-ipv4": env.syslog_ip,
                "port": 31515,
            }
        }
        client.request("POST", "/axapi/v3/logging/host/ipv4addr", json_body=payload, timeout=10)
        qlog_success(COMPONENT, f"Syslog host {env.syslog_ip} ensured.")
    except AxapiError as exc:
        qlog_warning(COMPONENT, f"Failed to configure syslog host: {exc}")


def _ensure_radius_server_with_client(
    client: AxapiClient,
    env: BootstrapEnv,
) -> None:
    """Configure RADIUS server using an existing authenticated client."""
    if not env.radius_ip or not env.radius_key:
        qlog_warning(COMPONENT, "RADIUS IP or key not set; skipping RADIUS server configuration.")
        return

    try:
        qlog(COMPONENT, f"Configuring RADIUS server {env.radius_ip}…")

        payload = {
            "radius-server": {
                "host": {
                    "ipv4-list": [
                        {
                            "ipv4-addr": env.radius_ip,
                            "secret": {
                                "secret-value": env.radius_key
                            }
                        }
                    ]
                }
            }
        }

        client.request("POST", "/axapi/v3/radius-server", json_body=payload, timeout=10)
        qlog_success(COMPONENT, f"RADIUS server {env.radius_ip} configured.")
    except AxapiError as exc:
        qlog_warning(COMPONENT, f"Failed to configure RADIUS server: {exc}")


def _ensure_aaa_authentication_with_client(
    client: AxapiClient,
    env: BootstrapEnv,
) -> None:
    """Configure AAA authentication to use RADIUS+local using an existing authenticated client."""
    if not env.radius_ip or not env.radius_key:
        qlog_warning(COMPONENT, "RADIUS not configured; skipping AAA authentication setup.")
        return

    try:
        qlog(COMPONENT, "Configuring AAA authentication (RADIUS, local)…")

        payload = {
            "authentication": {
                "type-cfg": {
                    "type": "radius,local"
                }
            }
        }

        client.request("POST", "/axapi/v3/authentication", json_body=payload, timeout=10)
        qlog_success(COMPONENT, "AAA authentication configured (RADIUS, local).")
    except AxapiError as exc:
        qlog_warning(COMPONENT, f"Failed to configure AAA authentication: {exc}")


def _ensure_polling_mode_with_client(
    client: AxapiClient,
    env: BootstrapEnv,
) -> None:
    """Enable shared poll-mode using an existing authenticated client."""
    if not env.enable_polling_mode:
        qlog(COMPONENT, "Shared polling-mode disabled by env; skipping.")
        return

    try:
        payload = {"shared-poll-mode": {"enable": 1}}
        client.request("POST", "/axapi/v3/system/shared-poll-mode", json_body=payload, timeout=10)
        qlog_success(COMPONENT, "Shared poll-mode enabled.")
    except AxapiError as exc:
        qlog_warning(COMPONENT, f"Failed to enable shared poll-mode: {exc}")


def _write_memory_with_client(
    client: AxapiClient,
) -> None:
    """Write memory using an existing authenticated client."""
    qlog_warning(COMPONENT, "Issuing 'write memory' to device")
    payload = {"memory": {"partition": "shared"}}
    data = client.request("POST", "/axapi/v3/write/memory", json_body=payload, timeout=30)

    if data.get("response", {}).get("status") == "fail":
        msg = data["response"].get("err", {}).get("msg", "")
        code = data["response"].get("err", {}).get("code", "")
        raise AxapiError(f"write/memory failed: {msg} | code={code}")

    qlog_success(COMPONENT, "write memory completed successfully.")


def _reboot_if_requested_with_client(
    client: AxapiClient,
    env: BootstrapEnv,
    force_reboot: bool = False,
) -> None:
    """
    Reboot using an existing authenticated client.

    Args:
        force_reboot: If True, reboot regardless of env flag (e.g., polling mode requires reboot)
    """
    should_reboot = force_reboot or env.reboot_after_bootstrap

    if not should_reboot:
        qlog(COMPONENT, "Reboot not required; skipping.")
        return

    if force_reboot and not env.reboot_after_bootstrap:
        qlog_warning(COMPONENT, "Forced reboot (required for shared poll-mode to take effect)…")
    else:
        qlog_warning(COMPONENT, "Issuing reboot after bootstrap…")

    try:
        client.reboot(warm=True)
        qlog_success(COMPONENT, "Reboot request submitted.")
    except AxapiError as exc:
        qlog_warning(COMPONENT, f"Reboot request failed: {exc}")


# ---------------------------------------------------------------------------
# SSH pubkey import helpers
# ---------------------------------------------------------------------------

class _QuietHTTPHandler(SimpleHTTPRequestHandler):
    """HTTP handler that suppresses log output."""
    def log_message(self, format, *args):
        pass  # Silence request logging


def _start_key_server(pubkey_path: str, port: int = 8099) -> tuple[HTTPServer, threading.Thread]:
    """
    Start a temporary HTTP server to serve the SSH public key file.
    Returns (server, thread) so the caller can shut it down.
    """
    key_dir = str(Path(pubkey_path).parent)
    handler = partial(_QuietHTTPHandler, directory=key_dir)
    server = HTTPServer(("0.0.0.0", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _import_ssh_pubkey_with_client(
    client: AxapiClient,
    env: BootstrapEnv,
    key_server_port: int = 8099,
) -> None:
    """Import SSH public key via aXAPI using a temporary HTTP server."""
    if not env.ssh_pubkey_path or not env.perimeter_ip:
        qlog_warning(
            COMPONENT,
            "VTH_SSH_PUBKEY_PATH or PERIMETER_IP not set; skipping SSH pubkey import.",
        )
        return

    pubkey_file = Path(env.ssh_pubkey_path)
    if not pubkey_file.exists():
        qlog_warning(COMPONENT, f"SSH pubkey not found at {env.ssh_pubkey_path}; skipping.")
        return

    file_url = f"http://{env.perimeter_ip}:{key_server_port}/{pubkey_file.name}"

    qlog(COMPONENT, f"Importing SSH pubkey via aXAPI: {file_url}")
    try:
        payload = {
            "ssh-pubkey": {
                "import": 1,
                "file-url": file_url,
            }
        }
        client.request(
            "POST",
            f"/axapi/v3/admin/{env.default_user}/ssh-pubkey",
            json_body=payload,
            timeout=15,
        )
        qlog_success(COMPONENT, "SSH pubkey imported successfully.")
    except AxapiError as exc:
        qlog_warning(COMPONENT, f"SSH pubkey import failed: {exc}")


def phase15_and_2_static_bootstrap(
    hostname: str,
    dhcp_ip: str,
    static_ip_cidr: str,
    env: BootstrapEnv,
) -> None:
    """
    Phase 1.5 + 2:

      • Configure static management IP on the DHCP IP
      • Re-login on STATIC IP
      • Configure hostname, DNS, syslog, automation user, polling-mode
      • write memory
      • optional reboot
    """
    static_ip = ""
    netmask = ""
    if static_ip_cidr:
        static_ip, netmask = _split_cidr(static_ip_cidr)

    if env.gateway:
        gateway = env.gateway
    else:
        from config import subnet_for_ip
        subnet_info = subnet_for_ip(static_ip) if static_ip else None
        gateway = subnet_info["gateway"] if subnet_info else None

    if not gateway:
        qlog_error(COMPONENT, "No gateway found — set PERIMETER_SUBNETS or pass gateway in bootstrap env")
        return 1

    if static_ip and gateway:
        _configure_mgmt_interface(
            dhcp_ip=dhcp_ip,
            static_ip=static_ip,
            netmask=netmask or "255.255.255.0",
            gateway=gateway,
            env=env,
        )
    else:
        qlog_warning(
            COMPONENT,
            "Static IP or gateway missing; skipping management IP configuration; "
            "Phase 2 will operate on DHCP IP only.",
        )
        # In that degenerate case we just keep using DHCP IP as "static"
        static_ip = dhcp_ip

    # AUTH #3: Single session for ALL Phase 2 operations
    qlog(COMPONENT, f"Phase 2: Authenticating to {static_ip} for final configuration…")
    client = AxapiClient(host=static_ip, username=env.default_user, password=env.admin_pass, verify_ssl=False)

    try:
        client.login()
        qlog_success(COMPONENT, f"Authenticated to {static_ip} for Phase 2.")

        # All Phase 2 operations in ONE session
        _set_hostname_with_client(client, hostname)
        _ensure_dns_primary_with_client(client, env)
        _ensure_syslog_host_with_client(client, env)
        _ensure_radius_server_with_client(client, env)
        _ensure_aaa_authentication_with_client(client, env)
        _ensure_polling_mode_with_client(client, env)
        _import_ssh_pubkey_with_client(client, env)
        _write_memory_with_client(client)

        # Reboot: forced if polling mode enabled, otherwise based on env flag
        force_reboot = env.enable_polling_mode
        _reboot_if_requested_with_client(client, env, force_reboot=force_reboot)

    except AxapiError as exc:
        qlog_error(COMPONENT, f"Phase 2 configuration failed: {exc}")
        raise
    finally:
        try:
            client.logoff()
            qlog(COMPONENT, "Phase 2 session closed.")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Public orchestrator
# ---------------------------------------------------------------------------

def run_vthunder_bootstrap(
    hostname: str,
    static_ip_cidr: str,
    dhcp_ip: str,
    acos_version: str,
    env_file: str = DEFAULT_ENV_FILE,
) -> int:
    """
    High-level entrypoint used by provision_vthunder.py.

    All bootstrapping logic for vThunder lives here.
    """
    qlog(
        COMPONENT,
        f"Starting unified vThunder bootstrap for {hostname}: "
        f"DHCP={dhcp_ip}, STATIC={static_ip_cidr}, ACOS={acos_version}, env={env_file}",
    )

    raw_env = load_env(env_file)
    if not raw_env:
        qlog_error(COMPONENT, f"Failed to load environment from {env_file} — is SOPS configured?")
        return 1

    # Fail fast on missing critical env vars
    missing = [v for v in ("VTH_ADMIN_PASS",) if not raw_env.get(v)]
    if missing:
        qlog_error(COMPONENT, f"Missing required env vars: {', '.join(missing)}")
        return 1

    env = BootstrapEnv.from_raw(raw_env)

    # Start temporary HTTP server for SSH pubkey import (if configured)
    key_server = None
    key_server_port = 8099
    if env.ssh_pubkey_path and env.perimeter_ip:
        try:
            key_server, _ = _start_key_server(env.ssh_pubkey_path, port=key_server_port)
            qlog(COMPONENT, f"SSH pubkey server started on port {key_server_port}")
        except OSError as exc:
            qlog_warning(COMPONENT, f"Could not start key server: {exc}; SSH pubkey import will be skipped.")

    try:
        # Phase 1 – password init on DHCP IP
        phase1_password_init(dhcp_ip=dhcp_ip, env=env)

        # Phase 1.5 + 2 – static mgmt + services
        phase15_and_2_static_bootstrap(
            hostname=hostname,
            dhcp_ip=dhcp_ip,
            static_ip_cidr=static_ip_cidr,
            env=env,
        )

        qlog_success(COMPONENT, f"Unified bootstrap complete for {hostname}.")
        return 0

    except AxapiError as exc:
        qlog_error(COMPONENT, f"Unified vThunder bootstrap FAILED: {exc}")
        return 1
    except Exception as exc:
        qlog_error(COMPONENT, f"Unexpected exception in vThunder bootstrap: {exc!r}")
        return 1
    finally:
        if key_server:
            key_server.shutdown()
            qlog(COMPONENT, "SSH pubkey server stopped.")


# ---------------------------------------------------------------------------
# CLI wrapper
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    init_correlation_id_from_env()
    argv = argv or sys.argv[1:]

    # HOSTNAME STATIC_IP_CIDR DHCP_IP ACOS_VERSION [ENV_FILE]
    if len(argv) < 4:
        qlog_error(
            COMPONENT,
            "Usage: bootstrap_vthunder.py HOSTNAME STATIC_IP_CIDR DHCP_IP ACOS_VERSION [ENV_FILE]",
        )
        return 1

    hostname = argv[0]
    static_ip_cidr = argv[1]
    dhcp_ip = argv[2]
    acos_version = argv[3]
    env_file = argv[4] if len(argv) >= 5 else DEFAULT_ENV_FILE

    return run_vthunder_bootstrap(
        hostname=hostname,
        static_ip_cidr=static_ip_cidr,
        dhcp_ip=dhcp_ip,
        acos_version=acos_version,
        env_file=env_file,
    )


if __name__ == "__main__":
    raise SystemExit(main())

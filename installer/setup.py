#!/usr/bin/env python3
"""Perimeter Automation Platform — Interactive Setup.

Collects user configuration, creates encrypted secrets, generates systemd
service file, initializes Terraform workspaces, and starts the service.
"""

import argparse
import getpass
import grp
import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

VERSION = "3.0"

# Ensure common binary paths are in PATH (pip, sops, age install to these)
_extra_paths = ["/usr/local/bin", os.path.expanduser("~/.local/bin")]
for _p in _extra_paths:
    if _p not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"{_p}:{os.environ.get('PATH', '')}"

# ── Colors ────────────────────────────────────────────────
CYAN = "\033[0;36m"
GREEN = "\033[0;32m"
RED = "\033[0;31m"
YELLOW = "\033[1;33m"
BOLD = "\033[1m"
DIM = "\033[2m"
NC = "\033[0m"


def ok(msg: str) -> None:
    print(f"  {GREEN}✓{NC} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}✖{NC} {msg}")


def info(msg: str) -> None:
    print(f"  {CYAN}→{NC} {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}⚠{NC} {msg}")


def header(title: str) -> None:
    print()
    print(f"{BOLD}{CYAN}{title}{NC}")
    print(f"{CYAN}{'─' * len(title)}{NC}")


def prompt(question: str, default: str = "") -> str:
    """Prompt with optional default value."""
    if default:
        answer = input(f"  {question} [{default}]: ").strip()
        return answer if answer else default
    return input(f"  {question}: ").strip()


def prompt_password(question: str) -> str:
    """Prompt for password (hidden input)."""
    return getpass.getpass(f"  {question}: ")


def prompt_yes_no(question: str, default: bool = True) -> bool:
    """Yes/no prompt with default."""
    suffix = "[Y/n]" if default else "[y/N]"
    answer = input(f"  {question} {suffix}: ").strip().lower()
    if not answer:
        return default
    return answer.startswith("y")


def prompt_choice(question: str, options: List[str], default: int = 1) -> int:
    """Multiple choice prompt. Returns 1-based index."""
    print(f"  {question}")
    for i, opt in enumerate(options, 1):
        marker = "→" if i == default else " "
        print(f"    {marker} [{i}] {opt}")
    while True:
        answer = input(f"  Choice [{default}]: ").strip()
        if not answer:
            return default
        try:
            choice = int(answer)
            if 1 <= choice <= len(options):
                return choice
        except ValueError:
            pass
        print(f"    Please enter 1-{len(options)}")


def run_cmd(cmd: List[str], check: bool = True, capture: bool = True, **kwargs) -> subprocess.CompletedProcess:
    """Run a command with optional output capture."""
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=check,
        **kwargs,
    )


def prompt_with_verify(label: str, default: str, verify_fn, fail_msg: str = "Unreachable") -> str:
    """Prompt for a value and verify it, retrying on failure."""
    while True:
        value = prompt(label, default)
        if verify_fn(value):
            ok(f"Verified: {value}")
            return value
        warn(f"{fail_msg}: {value}")
        if not prompt_yes_no("Try again?"):
            warn("Continuing with unverified value")
            return value


def verify_url(url: str, headers: Optional[Dict] = None, verify_ssl: bool = False) -> bool:
    """Test if a URL is reachable."""
    try:
        import urllib.request
        import ssl
        ctx = ssl.create_default_context()
        if not verify_ssl:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url)
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        urllib.request.urlopen(req, timeout=10, context=ctx)
        return True
    except Exception:
        return False


# ═════════════════════════════════════════════════════════
# SCREEN 1: Welcome
# ═════════════════════════════════════════════════════════

def screen_welcome(install_dir: str) -> Dict[str, str]:
    """Display welcome screen and verify prerequisites."""
    print()
    print(f"{BOLD}{CYAN}╔════════════════════════════════════════════════╗{NC}")
    print(f"{BOLD}{CYAN}║      PERIMETER v{VERSION} — SETUP WIZARD          ║{NC}")
    print(f"{BOLD}{CYAN}║       Automation Platform for Labs             ║{NC}")
    print(f"{BOLD}{CYAN}╚════════════════════════════════════════════════╝{NC}")
    print()

    prereqs = {}
    all_ok = True

    # OS
    os_name = "Unknown"
    if os.path.exists("/etc/os-release"):
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    os_name = line.split("=", 1)[1].strip().strip('"')
    prereqs["os"] = os_name

    # Python
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    prereqs["python"] = py_ver

    # Terraform
    try:
        result = run_cmd(["terraform", "version", "-json"], check=False)
        tf_ver = json.loads(result.stdout)["terraform_version"] if result.returncode == 0 else "not found"
    except Exception:
        tf_ver = "not found"
        all_ok = False
    prereqs["terraform"] = tf_ver

    # Ansible
    try:
        result = run_cmd(["ansible", "--version"], check=False)
        ans_ver = result.stdout.split("\n")[0] if result.returncode == 0 else "not found"
    except Exception:
        ans_ver = "not found"
        all_ok = False
    prereqs["ansible"] = ans_ver

    # SOPS
    try:
        result = run_cmd(["sops", "--version", "--disable-version-check"], check=False)
        sops_ver = result.stdout.strip().split("\n")[0] if result.returncode == 0 else "not found"
    except Exception:
        sops_ver = "not found"
        all_ok = False
    prereqs["sops"] = sops_ver

    # Age
    try:
        result = run_cmd(["age", "--version"], check=False)
        age_ver = result.stdout.strip() if result.returncode == 0 else "not found"
    except Exception:
        age_ver = "not found"
        all_ok = False
    prereqs["age"] = age_ver

    # Docker (optional)
    try:
        result = run_cmd(["docker", "--version"], check=False)
        docker_ver = result.stdout.strip() if result.returncode == 0 else "not installed"
    except Exception:
        docker_ver = "not installed"
    prereqs["docker"] = docker_ver

    print(f"  Detected OS:   {prereqs['os']}")
    print(f"  Python:        {prereqs['python']}")
    print(f"  Terraform:     {prereqs['terraform']}")
    print(f"  Ansible:       {prereqs['ansible']}")
    print(f"  SOPS:          {prereqs['sops']}")
    print(f"  Age:           {prereqs['age']}")
    print(f"  Docker:        {prereqs['docker']}")
    print(f"  Install Dir:   {install_dir}")
    print()

    if all_ok:
        ok("All prerequisites satisfied")
    else:
        fail("Missing prerequisites — run install.sh first")
        sys.exit(1)

    print()
    input("  Press Enter to continue...")
    return prereqs


# ═════════════════════════════════════════════════════════
# SCREEN 2: Service User
# ═════════════════════════════════════════════════════════

def screen_service_user() -> Dict[str, Any]:
    """Select or create the service user."""
    header("Service User Configuration")

    current_user = os.getenv("SUDO_USER", os.getenv("USER", "root"))

    choice = prompt_choice(
        "Perimeter runs as a systemd service. Choose the service account:",
        [
            "Create dedicated 'perimeter' user (recommended)",
            f"Use current user: {current_user}",
            "Specify different user",
        ],
    )

    if choice == 1:
        username = "perimeter"
        # Create user if doesn't exist
        try:
            pwd.getpwnam(username)
            ok(f"User '{username}' already exists")
        except KeyError:
            info(f"Creating user '{username}'...")
            run_cmd(["useradd", "-r", "-m", "-s", "/bin/bash", username])
            ok(f"User '{username}' created")
    elif choice == 2:
        username = current_user
        ok(f"Using existing user: {username}")
    else:
        username = prompt("Username")
        try:
            pwd.getpwnam(username)
            ok(f"User '{username}' exists")
        except KeyError:
            fail(f"User '{username}' does not exist. Create it first.")
            sys.exit(1)

    user_info = pwd.getpwnam(username)
    home_dir = user_info.pw_dir

    return {
        "username": username,
        "home_dir": home_dir,
        "uid": user_info.pw_uid,
        "gid": user_info.pw_gid,
    }


# ═════════════════════════════════════════════════════════
# SCREEN 3: Feature Selection
# ═════════════════════════════════════════════════════════

def screen_features() -> Dict[str, bool]:
    """Interactive feature selection."""
    header("Feature Selection")
    print("  Select which modules to enable. Toggle with number keys.")
    print("  You can change these later via environment variables.")
    print()

    features = {
        "ansible": {"label": "Ansible Orchestration — run playbooks from the UI", "enabled": True},
        "certificates": {"label": "Certificate Management — Let's Encrypt via Certbot", "enabled": False},
        "dns": {"label": "DNS Management — Pi-hole integration", "enabled": True},
        "ipam": {"label": "IPAM — Netbox IP address tracking", "enabled": False},
        "vthunder": {"label": "vThunder SLB Manager — A10 load balancer management", "enabled": False},
        "audit": {"label": "Audit Logging — send events to Loki/Grafana", "enabled": False},
    }

    keys = list(features.keys())

    while True:
        print(f"  {BOLD}{'─' * 60}{NC}")
        print(f"  {GREEN}[x]{NC} VM Provisioning {DIM}(core — always enabled){NC}")
        for i, key in enumerate(keys, 1):
            f = features[key]
            marker = f"{GREEN}[x]{NC}" if f["enabled"] else f"{DIM}[ ]{NC}"
            print(f"  {marker} {BOLD}[{i}]{NC} {f['label']}")
        print(f"  {BOLD}{'─' * 60}{NC}")
        print()

        answer = input("  Toggle a feature (1-6), or Enter to continue: ").strip()
        if not answer:
            break
        try:
            idx = int(answer) - 1
            if 0 <= idx < len(keys):
                key = keys[idx]
                features[key]["enabled"] = not features[key]["enabled"]
        except ValueError:
            pass

    # Check Docker if certificates enabled
    if features["certificates"]["enabled"]:
        try:
            run_cmd(["docker", "--version"], check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            warn("Docker is required for Certificate Management but is not installed.")
            if not prompt_yes_no("Continue without Docker?", default=False):
                features["certificates"]["enabled"] = False

    result = {k: v["enabled"] for k, v in features.items()}
    print()
    enabled = [k for k, v in result.items() if v]
    disabled = [k for k, v in result.items() if not v]
    ok(f"Enabled: VM Provisioning" + (f", {', '.join(enabled)}" if enabled else ""))
    if disabled:
        info(f"Disabled: {', '.join(disabled)}")

    return result


# ═════════════════════════════════════════════════════════
# SCREEN 4: Proxmox Configuration
# ═════════════════════════════════════════════════════════

def screen_proxmox() -> Dict[str, str]:
    """Collect Proxmox configuration and auto-create API token."""
    header("Proxmox Configuration")

    pm_url = prompt("Proxmox API URL", "https://proxmox.local:8006/api2/json")

    # Validate URL format
    if "/api2/json" not in pm_url:
        warn("URL should contain '/api2/json' — e.g., https://proxmox:8006/api2/json")
        pm_url = prompt("Proxmox API URL (corrected)", pm_url)

    # Verify connectivity (non-blocking — self-signed certs may cause false negatives)
    base_url = pm_url.rstrip("/")
    if verify_url(f"{base_url}/version"):
        ok("Proxmox API reachable")
    else:
        warn("Cannot verify Proxmox API (self-signed cert?) — continuing anyway")

    print()
    info("Perimeter needs an API token for Proxmox. We can create one automatically.")
    auto_create = prompt_yes_no("Auto-create API token using root credentials?")

    if auto_create:
        root_pass = prompt_password("Proxmox root@pam password (used once, not stored)")

        import urllib.request
        import ssl
        import json as _json

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        try:
            # Authenticate as root
            auth_data = f"username=root@pam&password={root_pass}".encode()
            req = urllib.request.Request(f"{base_url}/access/ticket", data=auth_data, method="POST")
            resp = urllib.request.urlopen(req, timeout=10, context=ctx)
            auth = _json.loads(resp.read())["data"]
            ticket = auth["ticket"]
            csrf = auth["CSRFPreventionToken"]

            headers_dict = {
                "Cookie": f"PVEAuthCookie={ticket}",
                "CSRFPreventionToken": csrf,
            }

            def pve_request(method, path, data=None):
                body = data.encode() if data else None
                r = urllib.request.Request(f"{base_url}{path}", data=body, method=method, headers=headers_dict)
                r.add_header("Content-Type", "application/x-www-form-urlencoded")
                try:
                    return urllib.request.urlopen(r, timeout=10, context=ctx)
                except urllib.error.HTTPError as e:
                    if e.code == 500 and "already exists" in e.read().decode():
                        return None
                    raise

            # Create user
            try:
                pve_request("POST", "/access/users", "userid=perimeter@pve&comment=Perimeter+Automation")
                ok("User created: perimeter@pve")
            except Exception:
                ok("User perimeter@pve already exists")

            # Create role with guest agent permissions
            privs = ",".join([
                "VM.Allocate", "VM.Clone", "VM.Config.CDROM", "VM.Config.CPU",
                "VM.Config.Cloudinit", "VM.Config.Disk", "VM.Config.HWType",
                "VM.Config.Memory", "VM.Config.Network", "VM.Config.Options",
                "VM.Audit", "VM.Console", "VM.PowerMgmt",
                "VM.GuestAgent.Audit", "VM.GuestAgent.Unrestricted",
                "Datastore.AllocateSpace", "Datastore.Audit", "SDN.Use",
            ])
            try:
                pve_request("POST", "/access/roles", f"roleid=PerimeterAdmin&privs={privs}")
                ok("Role created: PerimeterAdmin")
            except Exception:
                ok("Role PerimeterAdmin already exists")

            # Create API token
            resp = pve_request("POST", "/access/users/perimeter@pve/token/provider", "privsep=0")
            if resp:
                token_data = _json.loads(resp.read())["data"]
                token_id = token_data["full-tokenid"]
                token_secret = token_data["value"]
                ok(f"Token created: {token_id}")
            else:
                fail("Token already exists — provide credentials manually")
                token_id = prompt("API Token ID", "perimeter@pve!provider")
                token_secret = prompt_password("API Token Secret")

            # Assign role
            pve_request("PUT", "/access/acl", f"path=/&tokens=perimeter@pve!provider&roles=PerimeterAdmin")
            ok("ACL assigned: / → PerimeterAdmin")

        except Exception as e:
            fail(f"Token auto-creation failed: {e}")
            info("Please provide token credentials manually:")
            token_id = prompt("API Token ID", "perimeter@pve!provider")
            token_secret = prompt_password("API Token Secret")
    else:
        token_id = prompt("API Token ID", "terraform@pve!provider")
        token_secret = prompt_password("API Token Secret")

    return {
        "pm_api_url": pm_url,
        "pm_api_token_id": token_id,
        "pm_api_token_secret": token_secret,
    }


# ═════════════════════════════════════════════════════════
# SCREEN 5: Network Configuration
# ═════════════════════════════════════════════════════════

def screen_network() -> Dict[str, Any]:
    """Collect subnet and DNS configuration."""
    header("Network Configuration")

    subnets = []
    dns_domain = prompt("DNS Domain", "home.local")

    def verify_ip_reachable(ip: str) -> bool:
        """Quick ping check for an IP."""
        try:
            result = run_cmd(["ping", "-c", "1", "-W", "2", ip], check=False)
            return result.returncode == 0
        except Exception:
            return False

    while True:
        print()
        subnet = prompt("Subnet (CIDR)", "10.0.0.0/24" if not subnets else "")
        if not subnet:
            break
        gateway = prompt_with_verify(
            "  Gateway", "",
            verify_ip_reachable,
            fail_msg="Gateway unreachable",
        )
        dns1 = prompt_with_verify(
            "  DNS Server 1", "",
            verify_ip_reachable,
            fail_msg="DNS server unreachable",
        )
        dns2 = prompt("  DNS Server 2 (optional)")
        if dns2 and not verify_ip_reachable(dns2):
            warn(f"DNS server 2 unreachable: {dns2}")
            if prompt_yes_no("Re-enter?"):
                dns2 = prompt("  DNS Server 2")
        dns_servers = [dns1]
        if dns2:
            dns_servers.append(dns2)

        subnets.append({
            "network": subnet,
            "gateway": gateway,
            "dns": dns_servers,
        })
        ok(f"Subnet added: {subnet} (gw: {gateway})")

        if not prompt_yes_no("Add another subnet?", default=False):
            break

    if not subnets:
        warn("No subnets configured — using default 10.0.0.0/24")
        subnets.append({
            "network": "10.0.0.0/24",
            "gateway": "10.0.0.1",
            "dns": ["10.0.0.1"],
        })

    return {
        "subnets": subnets,
        "dns_domain": dns_domain,
    }


# ═════════════════════════════════════════════════════════
# SCREEN 6: SSH Keys
# ═════════════════════════════════════════════════════════

def screen_ssh_keys(user_config: Dict) -> Dict[str, Any]:
    """Generate or accept SSH keys."""
    header("SSH Key Configuration")

    home_dir = user_config["home_dir"]
    username = user_config["username"]
    ssh_dir = Path(home_dir) / ".ssh"

    choice = prompt_choice(
        "Perimeter uses SSH keys for Ansible connectivity:",
        [
            "Generate new SSH keypairs (recommended for fresh installs)",
            "Provide paths to existing keys",
        ],
    )

    if choice == 1:
        ssh_dir.mkdir(mode=0o700, exist_ok=True)

        # ed25519 for Linux
        linux_key = ssh_dir / "perimeter_ed25519"
        if linux_key.exists():
            ok(f"Linux key already exists: {linux_key}")
        else:
            info("Generating ed25519 key for Linux hosts...")
            run_cmd(["ssh-keygen", "-t", "ed25519", "-f", str(linux_key), "-N", "", "-C", f"{username}@perimeter"])
            ok(f"Created: {linux_key}")

        # RSA for vThunder (ACOS requires RSA)
        vth_key = ssh_dir / "perimeter_rsa"
        if vth_key.exists():
            ok(f"vThunder key already exists: {vth_key}")
        else:
            info("Generating RSA-4096 key for A10 vThunder (ACOS requires RSA)...")
            run_cmd(["ssh-keygen", "-t", "rsa", "-b", "4096", "-f", str(vth_key), "-N", "", "-C", f"{username}@perimeter"])
            ok(f"Created: {vth_key}")

        # Fix ownership
        for f in ssh_dir.iterdir():
            os.chown(str(f), user_config["uid"], user_config["gid"])
        os.chown(str(ssh_dir), user_config["uid"], user_config["gid"])

        linux_key_path = str(linux_key)
        vth_key_path = str(vth_key)

        # Read public keys
        linux_pubkey = (linux_key.with_suffix(".pub")).read_text().strip() if linux_key.with_suffix(".pub").exists() else ""
        vth_pubkey = (vth_key.with_suffix(".pub")).read_text().strip() if vth_key.with_suffix(".pub").exists() else ""

    else:
        linux_key_path = prompt("Path to Linux SSH private key", f"{home_dir}/.ssh/id_ed25519")
        vth_key_path = prompt("Path to vThunder SSH private key (RSA)", f"{home_dir}/.ssh/id_rsa")

        linux_pub = Path(linux_key_path).with_suffix(".pub")
        vth_pub = Path(vth_key_path).with_suffix(".pub")
        linux_pubkey = linux_pub.read_text().strip() if linux_pub.exists() else ""
        vth_pubkey = vth_pub.read_text().strip() if vth_pub.exists() else ""

    print()
    if linux_pubkey:
        info(f"Linux public key: {linux_pubkey[:60]}...")
    if vth_pubkey:
        info(f"vThunder public key: {vth_pubkey[:60]}...")
    print()
    warn("Deploy these public keys to your target hosts for Ansible access.")

    return {
        "ssh_user": username,
        "ssh_linux_key_path": linux_key_path,
        "ssh_vthunder_key_path": vth_key_path,
        "ssh_public_keys": [k for k in [linux_pubkey] if k],
        "vth_ssh_pubkey_path": f"{vth_key_path}.pub" if vth_key_path else "",
    }


# ═════════════════════════════════════════════════════════
# SCREEN 7: Feature-Specific Credentials
# ═════════════════════════════════════════════════════════

def screen_feature_creds(features: Dict[str, bool]) -> Dict[str, str]:
    """Collect credentials for enabled features."""
    creds = {}

    if features.get("dns"):
        header("Pi-hole DNS Configuration")

        def _check_pihole(url: str) -> bool:
            """Pi-hole v6 returns 403 on root — that's fine, means it's alive."""
            try:
                import urllib.request, ssl
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                urllib.request.urlopen(url, timeout=5, context=ctx)
                return True
            except urllib.error.HTTPError:
                return True  # 403/404 = server is responding
            except Exception:
                return False

        creds["pihole_api_url"] = prompt_with_verify(
            "Pi-hole API URL", "http://pihole.local",
            _check_pihole,
            fail_msg="Pi-hole unreachable",
        )
        creds["pihole_api_password"] = prompt_password("Pi-hole API Password")

    if features.get("ipam"):
        header("Netbox IPAM Configuration")
        creds["netbox_url"] = prompt_with_verify(
            "Netbox URL", "https://netbox.local",
            lambda url: verify_url(f"{url}/api/"),
            fail_msg="Netbox unreachable",
        )
        creds["netbox_api_token"] = prompt_password("Netbox API Token")
        # Verify token works
        if verify_url(f"{creds['netbox_url']}/api/", headers={"Authorization": f"Token {creds['netbox_api_token']}"}):
            ok("Netbox API token verified")
        else:
            warn("Token verification failed — check credentials after install")

    if features.get("vthunder"):
        header("A10 vThunder Configuration")
        creds["vth_admin_pass"] = prompt_password("vThunder default admin password")

    if features.get("audit"):
        header("Loki Audit Configuration")
        creds["loki_url"] = prompt_with_verify(
            "Loki URL", "https://loki.local",
            lambda url: verify_url(f"{url}/ready"),
            fail_msg="Loki unreachable",
        )

    if features.get("certificates"):
        header("Certificate Management Configuration")
        creds["certbot_email"] = prompt("Certbot email (for Let's Encrypt)")

    return creds


# ═════════════════════════════════════════════════════════
# SCREEN 8: Write Configuration
# ═════════════════════════════════════════════════════════

def screen_write_config(
    install_dir: str,
    user_config: Dict,
    features: Dict[str, bool],
    proxmox: Dict,
    network: Dict,
    ssh: Dict,
    creds: Dict,
) -> None:
    """Generate all configuration files."""
    header("Writing Configuration")

    install_path = Path(install_dir)
    home_dir = user_config["home_dir"]
    username = user_config["username"]
    uid = user_config["uid"]
    gid = user_config["gid"]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── Create directories ─────────────────────────
    for d in ["logs", "data", "secrets"]:
        p = install_path / d
        p.mkdir(mode=0o755, exist_ok=True)
        os.chown(str(p), uid, gid)

    (install_path / "secrets").chmod(0o700)
    ok("Directories created (logs/, data/, secrets/)")

    # ── Age keypair ────────────────────────────────
    age_dir = Path(home_dir) / ".config" / "sops" / "age"
    age_key_path = age_dir / "keys.txt"

    if age_key_path.exists():
        ok(f"Age key already exists: {age_key_path}")
    else:
        age_dir.mkdir(parents=True, exist_ok=True)
        run_cmd(["age-keygen", "-o", str(age_key_path)], check=True)
        age_key_path.chmod(0o600)
        os.chown(str(age_key_path), uid, gid)
        for p in [age_dir, age_dir.parent, age_dir.parent.parent]:
            os.chown(str(p), uid, gid)
        ok(f"Age keypair generated: {age_key_path}")

    # Extract public key from age key file
    age_public_key = ""
    for line in age_key_path.read_text().splitlines():
        if line.startswith("# public key:"):
            age_public_key = line.split(":", 1)[1].strip()
            break
    if not age_public_key:
        fail("Could not extract Age public key from key file")
        sys.exit(1)
    info(f"Age public key: {age_public_key}")

    # ── .sops.yaml ─────────────────────────────────
    sops_content = f"""creation_rules:
  - path_regex: '\\.enc\\.env$'
    age: '{age_public_key}'
  - path_regex: '\\.enc\\.yaml$'
    age: '{age_public_key}'
  - path_regex: '\\.enc\\.tfvars$'
    age: '{age_public_key}'
"""
    (install_path / ".sops.yaml").write_text(sops_content)
    ok("SOPS config written: .sops.yaml")

    # ── Secrets env file ───────────────────────────
    env_lines = [
        f"# Perimeter Secrets — Generated {timestamp}",
        f"PM_API_URL={proxmox['pm_api_url']}",
        f"PM_API_TOKEN_ID={proxmox['pm_api_token_id']}",
        f"PM_API_TOKEN_SECRET={proxmox['pm_api_token_secret']}",
        f"LINUX_SSH_USER={ssh['ssh_user']}",
    ]
    for i, key in enumerate(ssh["ssh_public_keys"], 1):
        env_lines.append(f"LINUX_SSH_KEY_{i}={key}")

    if features.get("vthunder"):
        env_lines.append(f"VTH_ADMIN_PASS={creds.get('vth_admin_pass', '')}")
        env_lines.append(f"VTH_SSH_PUBKEY_PATH={ssh.get('vth_ssh_pubkey_path', '')}")

    if features.get("dns"):
        env_lines.append(f"PIHOLE_API_URL={creds.get('pihole_api_url', '')}")
        env_lines.append(f"PIHOLE_API_PASSWORD={creds.get('pihole_api_password', '')}")

    if features.get("ipam"):
        env_lines.append(f"NETBOX_URL={creds.get('netbox_url', '')}")
        env_lines.append(f"NETBOX_API_TOKEN={creds.get('netbox_api_token', '')}")

    if features.get("audit"):
        env_lines.append(f"LOKI_URL={creds.get('loki_url', '')}")

    if features.get("certificates"):
        env_lines.append(f"CERTBOT_EMAIL={creds.get('certbot_email', '')}")

    # Write plaintext to temp, encrypt with SOPS, delete plaintext
    enc_env_path = install_path / "secrets" / "automation-demo.enc.env"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False, dir=str(install_path / "secrets")) as tmp:
        tmp.write("\n".join(env_lines) + "\n")
        tmp_path = tmp.name

    try:
        run_cmd(["sops", "--disable-version-check",
                 "--age", age_public_key,
                 "-e", "--input-type", "dotenv", "--output-type", "dotenv",
                 "-i", tmp_path], check=True,
                env={**os.environ, "SOPS_AGE_KEY_FILE": str(age_key_path)})
        shutil.move(tmp_path, str(enc_env_path))
        enc_env_path.chmod(0o600)
        os.chown(str(enc_env_path), uid, gid)
        ok("Secrets encrypted: secrets/automation-demo.enc.env")
    except subprocess.CalledProcessError as e:
        stderr_msg = e.stderr.strip() if e.stderr else "no stderr"
        stdout_msg = e.stdout.strip() if e.stdout else "no stdout"
        fail(f"SOPS encryption failed: {stderr_msg or stdout_msg}")
        # Keep plaintext as fallback
        shutil.move(tmp_path, str(enc_env_path))
        warn("Secrets saved as plaintext — encrypt manually with SOPS later")

    # ── Terraform credentials ──────────────────────
    tf_creds_content = textwrap.dedent(f"""\
        pm_api_url      = "{proxmox['pm_api_url']}"
        pm_api_token_id = "{proxmox['pm_api_token_id']}"
        pm_api_token    = "{proxmox['pm_api_token_secret']}"
    """)

    for workspace in ["linux_vm", "vthunder_vm", "vyos_vm"]:
        ws_dir = install_path / "terraform" / workspace
        ws_dir.mkdir(parents=True, exist_ok=True)
        creds_file = ws_dir / "credentials.auto.tfvars"
        creds_file.write_text(tf_creds_content)
        creds_file.chmod(0o600)
        os.chown(str(creds_file), uid, gid)

        # Ensure tfvars files exist
        tfvars_file = ws_dir / f"perimeter-{workspace.replace('_vm', '')}.auto.tfvars.json"
        if not tfvars_file.exists():
            key = {"linux_vm": "vm_configs", "vthunder_vm": "vthunder_configs", "vyos_vm": "vyos_configs"}[workspace]
            tfvars_file.write_text(json.dumps({key: {}}, indent=2) + "\n")
            os.chown(str(tfvars_file), uid, gid)

    ok("Terraform credentials written to all workspaces")

    # ── Inventory ──────────────────────────────────
    inv_dir = install_path / "inventories"
    inv_dir.mkdir(exist_ok=True)
    inv_file = inv_dir / "inventory.yml"
    if not inv_file.exists():
        inv_content = textwrap.dedent(f"""\
            # ANSIBLE INVENTORY - PERIMETER AUTOMATION
            # Generated {timestamp}

            production:
              children:
                prod_linux_dnf:
                prod_linux_apt:
                prod_vthunder:
                prod_vyos:

            prod_linux_dnf:
              hosts: {{}}
              vars:
                environment: production
                package_manager: dnf

            prod_linux_apt:
              hosts: {{}}
              vars:
                environment: production
                package_manager: apt

            prod_vthunder:
              hosts: {{}}
              vars:
                environment: production
                ansible_user: admin
                ansible_ssh_private_key_file: {ssh['ssh_vthunder_key_path']}
                ansible_port: 443
                ansible_become: false

            prod_vyos:
              hosts: {{}}
              vars:
                environment: production
                ansible_user: {ssh['ssh_user']}
                ansible_ssh_private_key_file: {ssh['ssh_linux_key_path']}

            staging:
              children:
                staging_linux:
                staging_vthunder:
                staging_vyos:

            staging_linux:
              hosts: {{}}
              vars:
                environment: staging
                ansible_user: {ssh['ssh_user']}
                ansible_ssh_private_key_file: {ssh['ssh_linux_key_path']}

            staging_vthunder:
              hosts: {{}}
              vars:
                environment: staging
                ansible_become: false

            staging_vyos:
              hosts: {{}}
              vars:
                environment: staging
                ansible_user: {ssh['ssh_user']}
                ansible_ssh_private_key_file: {ssh['ssh_linux_key_path']}

            all_linux:
              children:
                prod_linux_dnf:
                prod_linux_apt:
                staging_linux:

            all_vthunder:
              children:
                staging_vthunder:
                prod_vthunder:

            all_vyos:
              children:
                staging_vyos:
                prod_vyos:

            all:
              hosts:
                localhost:
                  ansible_connection: local
                  ansible_become: false
                  ansible_python_interpreter: "{{{{ ansible_playbook_python }}}}"
              vars:
                ansible_become: true
                ansible_become_method: sudo
                ansible_user: {ssh['ssh_user']}
                ansible_ssh_private_key_file: {ssh['ssh_linux_key_path']}
        """)
        inv_file.write_text(inv_content)
        os.chown(str(inv_file), uid, gid)
        ok("Initial inventory created: inventories/inventory.yml")
    else:
        ok("Inventory already exists — keeping existing")

    # ── Systemd service ────────────────────────────
    service_content = textwrap.dedent(f"""\
        [Unit]
        Description=Perimeter Automation Platform
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type=simple
        User={username}
        Group={username}

        ExecStart={install_dir}/venv/bin/python3 -u {install_dir}/qbranch_app.py
        WorkingDirectory={install_dir}

        Environment=SOPS_AGE_KEY_FILE={age_key_path}
        Environment=QBRANCH_ROOT={install_dir}
        Environment=FLASK_PORT=8080
        Environment=PERIMETER_DNS_DOMAIN={network['dns_domain']}
        Environment=PERIMETER_FEATURE_ANSIBLE={'1' if features.get('ansible') else '0'}
        Environment=PERIMETER_FEATURE_CERTS={'1' if features.get('certificates') else '0'}
        Environment=PERIMETER_FEATURE_DNS={'1' if features.get('dns') else '0'}
        Environment=PERIMETER_FEATURE_IPAM={'1' if features.get('ipam') else '0'}
        Environment=PERIMETER_FEATURE_VTHUNDER={'1' if features.get('vthunder') else '0'}
        Environment=PERIMETER_FEATURE_AUDIT={'1' if features.get('audit') else '0'}

        StandardOutput=journal
        StandardError=journal
        SyslogIdentifier=perimeter

        KillSignal=SIGTERM
        TimeoutStopSec=90

        Restart=on-failure
        RestartSec=5

        PrivateTmp=true
        NoNewPrivileges=true
        ProtectSystem=strict
        ReadWritePaths={install_dir}
        ReadWritePaths={home_dir}/.ssh

        [Install]
        WantedBy=multi-user.target
    """)

    service_path = Path("/etc/systemd/system/perimeter.service")
    service_path.write_text(service_content)
    ok("Service file written: /etc/systemd/system/perimeter.service")

    # ── Fix ownership of install dir ───────────────
    info("Setting ownership...")
    for root, dirs, files in os.walk(install_dir):
        for d in dirs:
            try:
                os.chown(os.path.join(root, d), uid, gid)
            except PermissionError:
                pass
        for f in files:
            try:
                os.chown(os.path.join(root, f), uid, gid)
            except PermissionError:
                pass
    os.chown(install_dir, uid, gid)
    ok(f"Ownership set to {username}:{username}")

    # ── Terraform init ─────────────────────────────
    header("Initializing Terraform Workspaces")
    for workspace in ["linux_vm", "vthunder_vm", "vyos_vm"]:
        ws_dir = install_path / "terraform" / workspace
        if (ws_dir / "main.tf").exists():
            result = run_cmd(
                ["terraform", "init", "-input=false"],
                check=False,
                cwd=str(ws_dir),
                env={**os.environ, "HOME": home_dir},
            )
            if result.returncode == 0:
                ok(f"terraform init ({workspace})")
            else:
                warn(f"terraform init ({workspace}) — failed, run manually later")
        else:
            info(f"Skipping {workspace} — no main.tf found")


# ═════════════════════════════════════════════════════════
# SCREEN 9: Start Service + Summary
# ═════════════════════════════════════════════════════════

def screen_start_service(features: Dict[str, bool], install_dir: str) -> None:
    """Start the service and display summary."""
    header("Starting Perimeter Service")

    run_cmd(["systemctl", "daemon-reload"], check=False)
    run_cmd(["systemctl", "enable", "perimeter"], check=False)
    result = run_cmd(["systemctl", "start", "perimeter"], check=False)

    import time
    time.sleep(3)

    # Health check
    health_ok = verify_url("http://localhost:8080")
    if health_ok:
        ok("Perimeter is running on port 8080")
    else:
        warn("Service started but health check failed — check logs: journalctl -u perimeter -f")

    # Metrics check
    metrics_ok = verify_url("http://localhost:8080/metrics")
    if metrics_ok:
        ok("/metrics endpoint responding")

    # ── Summary ────────────────────────────────────
    print()
    print(f"{BOLD}{CYAN}╔════════════════════════════════════════════════╗{NC}")
    print(f"{BOLD}{CYAN}║          INSTALLATION COMPLETE ✓               ║{NC}")
    print(f"{BOLD}{CYAN}╚════════════════════════════════════════════════╝{NC}")
    print()

    # Detect IP
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        host_ip = s.getsockname()[0]
        s.close()
    except Exception:
        host_ip = "localhost"

    print(f"  Perimeter is running at: {BOLD}http://{host_ip}:8080{NC}")
    print()

    enabled = [k for k, v in features.items() if v]
    disabled = [k for k, v in features.items() if not v]

    print(f"  {BOLD}Enabled modules:{NC}")
    print(f"    {GREEN}✓{NC} VM Provisioning")
    for f in enabled:
        print(f"    {GREEN}✓{NC} {f.replace('_', ' ').title()}")

    if disabled:
        print(f"  {BOLD}Disabled modules:{NC}")
        for f in disabled:
            print(f"    {DIM}○{NC} {f.replace('_', ' ').title()}")

    print()
    print(f"  {BOLD}Useful commands:{NC}")
    print(f"    Logs:      journalctl -u perimeter -f")
    print(f"    Restart:   sudo systemctl restart perimeter")
    print(f"    Config:    {install_dir}/secrets/automation-demo.enc.env")
    print(f"    Features:  Edit /etc/systemd/system/perimeter.service")
    print()
    print(f"  {BOLD}Next steps:{NC}")
    print(f"    1. Configure your reverse proxy (Traefik/Nginx) for HTTPS")
    print(f"    2. Create Proxmox VM templates (cloud-init + guest agent)")
    print(f"    3. Deploy SSH public keys to target hosts")
    print(f"    4. Visit the UI and provision your first VM!")
    print()


# ═════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Perimeter Interactive Setup")
    parser.add_argument("--install-dir", default="/opt/perimeter", help="Installation directory")
    args = parser.parse_args()

    install_dir = os.path.abspath(args.install_dir)

    # Screen 1: Welcome + prereq check
    prereqs = screen_welcome(install_dir)

    # Screen 2: Service user
    user_config = screen_service_user()

    # Screen 3: Feature selection
    features = screen_features()

    # Screen 4: Proxmox configuration
    proxmox = screen_proxmox()

    # Screen 5: Network configuration
    network = screen_network()

    # Screen 6: SSH keys
    ssh = screen_ssh_keys(user_config)

    # Screen 7: Feature-specific credentials
    creds = screen_feature_creds(features)

    # Screen 8: Write all config
    screen_write_config(
        install_dir=install_dir,
        user_config=user_config,
        features=features,
        proxmox=proxmox,
        network=network,
        ssh=ssh,
        creds=creds,
    )

    # Screen 9: Start service + summary
    screen_start_service(features, install_dir)


if __name__ == "__main__":
    main()

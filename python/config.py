"""Centralized configuration for Perimeter Automation Platform."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

from utils.sops_env import load_env

# Load SOPS-encrypted secrets into the process environment so that
# os.getenv() picks them up even without the systemd EnvironmentFile.
_sops_env = load_env()
for _k, _v in _sops_env.items():
    os.environ.setdefault(_k, _v)


@dataclass
class Config:
    """All paths, constants, and environment-driven settings in one place."""

    # ── Core paths ──────────────────────────────────────────────
    ROOT_DIR: Path = field(
        default_factory=lambda: Path(
            os.getenv("QBRANCH_ROOT", "/home/mklouda/automation-demo")
        )
    )
    DOCKER_COMPOSE_DIR: Path = field(
        default_factory=lambda: Path(
            os.getenv("DOCKER_COMPOSE_DIR", "/home/mklouda/docker")
        )
    )

    # ── Derived paths (set in __post_init__) ────────────────────
    SCRIPTS_DIR: Path = field(init=False)
    LINUX_SCRIPT_DIR: Path = field(init=False)
    VTHUNDER_SCRIPT_DIR: Path = field(init=False)
    HELPERS_SCRIPT_DIR: Path = field(init=False)
    PLAYBOOK_DIR: Path = field(init=False)
    PLAYBOOK_TEMPLATE_DIR: Path = field(init=False)
    INVENTORY_DIR: Path = field(init=False)
    TERRAFORM_DIR: Path = field(init=False)
    CERTIFICATE_DIR: Path = field(init=False)
    WEB_DIR: Path = field(init=False)

    # Terraform state & tfvars paths
    TF_LINUX_STATE_PATH: Path = field(init=False)
    TF_VTHUNDER_STATE_PATH: Path = field(init=False)
    TF_VYOS_STATE_PATH: Path = field(init=False)
    TF_LINUX_TFVARS_PATH: Path = field(init=False)
    TF_VTHUNDER_TFVARS_PATH: Path = field(init=False)
    TF_VYOS_TFVARS_PATH: Path = field(init=False)

    # VM tracking
    VM_TRACK_FILE: Path = field(init=False)

    # ── Proxmox ─────────────────────────────────────────────────
    PM_API_URL: str = field(
        default_factory=lambda: os.getenv(
            "PM_API_URL", "https://goldfinger.home.klouda.co:8006/api2/json"
        )
    )
    PM_API_TOKEN_ID: str = field(
        default_factory=lambda: os.getenv("PM_API_TOKEN_ID", "")
    )
    PM_API_TOKEN_SECRET: str = field(
        default_factory=lambda: os.getenv("PM_API_TOKEN_SECRET", "")
    )

    # ── Netbox ──────────────────────────────────────────────────
    NETBOX_URL: str = field(
        default_factory=lambda: os.getenv(
            "NETBOX_URL", "https://netbox.home.klouda.co"
        )
    )
    NETBOX_API_TOKEN: str = field(
        default_factory=lambda: os.getenv("NETBOX_API_TOKEN", "")
    )
    NETBOX_CACHE_TTL: int = 45  # seconds
    NETBOX_SUBNET: str = field(
        default_factory=lambda: os.getenv("NETBOX_SUBNET", "10.1.55.0/24")
    )

    # ── Subnets ────────────────────────────────────────────────
    SUBNETS: dict = field(default_factory=lambda: {
        "10.1.55.0/24": {"gateway": "10.1.55.254", "dns": ["10.1.55.10", "10.1.55.11"]},
        "10.255.0.0/24": {"gateway": "10.255.0.1", "dns": ["10.1.55.10", "10.1.55.11"]},
    })

    # ── Feature Toggles ──────────────────────────────────────────
    FEATURES: dict = field(default_factory=lambda: {
        "ansible": os.getenv("PERIMETER_FEATURE_ANSIBLE", "1") == "1",
        "certificates": os.getenv("PERIMETER_FEATURE_CERTS", "1") == "1",
        "dns": os.getenv("PERIMETER_FEATURE_DNS", "1") == "1",
        "ipam": os.getenv("PERIMETER_FEATURE_IPAM", "1") == "1",
        "vthunder": os.getenv("PERIMETER_FEATURE_VTHUNDER", "1") == "1",
        "audit": os.getenv("PERIMETER_FEATURE_AUDIT", "1") == "1",
    })

    # ── DNS Domain ────────────────────────────────────────────────
    DNS_DOMAIN: str = field(
        default_factory=lambda: os.getenv("PERIMETER_DNS_DOMAIN", "home.klouda.co")
    )

    # ── Pi-hole ─────────────────────────────────────────────────
    PIHOLE_URL: str = field(
        default_factory=lambda: os.getenv("PIHOLE_URL", "http://10.1.55.10")
    )

    # ── Certificates ────────────────────────────────────────────
    CERTBOT_EMAIL: str = field(
        default_factory=lambda: os.getenv("CERTBOT_EMAIL", "")
    )
    ALLOWED_CERT_FILES: frozenset = frozenset(
        {"cert.pem", "chain.pem", "fullchain.pem", "privkey.pem"}
    )

    # ── Logging ────────────────────────────────────────────────
    LOG_LEVEL: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO")
    )
    LOG_FILE: str = "perimeter.log"
    LOG_MAX_BYTES: int = 10 * 1024 * 1024  # 10 MB per file
    LOG_BACKUP_COUNT: int = 5              # keep 5 rotated files

    # ── Loki (audit logging) ──────────────────────────────────
    LOKI_URL: str = field(
        default_factory=lambda: os.getenv("LOKI_URL", "https://loki.home.klouda.co")
    )

    # ── Flask ───────────────────────────────────────────────────
    FLASK_HOST: str = field(default_factory=lambda: os.getenv("FLASK_HOST", "0.0.0.0"))
    FLASK_PORT: int = field(default_factory=lambda: int(os.getenv("FLASK_PORT", "8080")))
    VERIFY_SSL: bool = False

    def __post_init__(self) -> None:
        # Ensure Path types
        self.ROOT_DIR = Path(self.ROOT_DIR)
        self.DOCKER_COMPOSE_DIR = Path(self.DOCKER_COMPOSE_DIR)

        # Derived paths from ROOT_DIR
        self.SCRIPTS_DIR = self.ROOT_DIR / "scripts"
        self.LINUX_SCRIPT_DIR = self.SCRIPTS_DIR / "linux"
        self.VTHUNDER_SCRIPT_DIR = self.SCRIPTS_DIR / "vthunder"
        self.HELPERS_SCRIPT_DIR = self.SCRIPTS_DIR / "helpers"
        self.PLAYBOOK_DIR = self.ROOT_DIR / "playbooks"
        self.PLAYBOOK_TEMPLATE_DIR = self.ROOT_DIR / "playbook_templates"
        self.INVENTORY_DIR = self.ROOT_DIR / "inventories"
        self.TERRAFORM_DIR = self.ROOT_DIR / "terraform"
        self.CERTIFICATE_DIR = self.ROOT_DIR / "certificates"
        self.WEB_DIR = self.ROOT_DIR / "web"

        # Terraform workspace paths
        self.TF_LINUX_STATE_PATH = self.TERRAFORM_DIR / "linux_vm" / "terraform-linux.tfstate"
        self.TF_VTHUNDER_STATE_PATH = self.TERRAFORM_DIR / "vthunder_vm" / "terraform-vthunder.tfstate"
        self.TF_VYOS_STATE_PATH = self.TERRAFORM_DIR / "vyos_vm" / "terraform.tfstate"
        self.TF_LINUX_TFVARS_PATH = self.TERRAFORM_DIR / "linux_vm" / "perimeter-linux.auto.tfvars.json"
        self.TF_VTHUNDER_TFVARS_PATH = self.TERRAFORM_DIR / "vthunder_vm" / "perimeter-vthunder.auto.tfvars.json"
        self.TF_VYOS_TFVARS_PATH = self.TERRAFORM_DIR / "vyos_vm" / "perimeter-vyos.auto.tfvars.json"

        # VM tracking
        self.VM_TRACK_FILE = self.ROOT_DIR / "vm_track.json"

    @property
    def cert_domains(self) -> Dict[str, dict]:
        """Certificate domain configuration — derived from DOCKER_COMPOSE_DIR and CERTIFICATE_DIR."""
        domains = ["valowgaming.com", "sinkscanyon.net", "klouda.co", "klouda.work"]
        result = {}
        for domain in domains:
            service_name = f"certbot-{domain.replace('.', '-')}"
            result[domain] = {
                "compose_dir": str(self.DOCKER_COMPOSE_DIR / domain),
                "service": service_name,
                "cert_path": str(self.CERTIFICATE_DIR / domain),
            }
        return result

    def pm_headers(self) -> Dict[str, str]:
        """Build Proxmox API authorization headers."""
        if not self.PM_API_TOKEN_ID or not self.PM_API_TOKEN_SECRET:
            return {}
        return {
            "Authorization": f"PVEAPIToken={self.PM_API_TOKEN_ID}={self.PM_API_TOKEN_SECRET}"
        }

    def netbox_headers(self) -> Dict[str, str]:
        """Build Netbox API authorization headers."""
        if not self.NETBOX_API_TOKEN:
            return {}
        return {
            "Authorization": f"Token {self.NETBOX_API_TOKEN}",
            "Accept": "application/json",
        }


# Singleton instance — import this from other modules
cfg = Config()


def subnet_for_ip(ip: str) -> dict | None:
    """Look up subnet config (gateway, dns, cidr) for a given IP.

    Returns dict with 'gateway', 'dns', 'cidr', 'network' keys, or None if no match.
    """
    import ipaddress
    try:
        addr = ipaddress.IPv4Address(ip.split("/")[0])
    except (ipaddress.AddressValueError, ValueError):
        return None
    for network_str, conf in cfg.SUBNETS.items():
        net = ipaddress.IPv4Network(network_str)
        if addr in net:
            return {
                "gateway": conf["gateway"],
                "dns": conf["dns"],
                "cidr": str(net.prefixlen),
                "network": network_str,
            }
    return None

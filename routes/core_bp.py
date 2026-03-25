"""Core dashboard routes."""

import os

from flask import Blueprint, render_template

from config import cfg
from .shared import list_playbooks, list_inventories, list_proxmox_templates

core_bp = Blueprint("core", __name__)


def _default_subnet() -> str:
    """Return the first configured subnet CIDR, or empty string."""
    if cfg.SUBNETS:
        return next(iter(cfg.SUBNETS))
    return ""


def _template_vars():
    return dict(
        playbooks=list_playbooks(),
        inventories=list_inventories(),
        templates=list_proxmox_templates(),
        features=cfg.FEATURES,
        pm_node=cfg.PM_NODE,
        default_subnet=_default_subnet(),
        homelab_name=os.getenv("PERIMETER_HOMELAB_NAME", ""),
    )


@core_bp.route("/")
def index():
    return render_template("index.html", **_template_vars())


@core_bp.route("/create-vm")
def create_vm_page():
    return render_template("index.html", **_template_vars())

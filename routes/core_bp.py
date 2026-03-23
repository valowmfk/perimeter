"""Core dashboard routes."""

from flask import Blueprint, render_template

from config import cfg
from .shared import list_playbooks, list_inventories, list_proxmox_templates

core_bp = Blueprint("core", __name__)


@core_bp.route("/")
def index():
    return render_template(
        "index.html",
        playbooks=list_playbooks(),
        inventories=list_inventories(),
        templates=list_proxmox_templates(),
        features=cfg.FEATURES,
    )


@core_bp.route("/create-vm")
def create_vm_page():
    return render_template(
        "index.html",
        playbooks=list_playbooks(),
        inventories=list_inventories(),
        templates=list_proxmox_templates(),
        features=cfg.FEATURES,
    )

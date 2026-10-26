"""
Node update drift for Perimeter's Updates page (node track, OBSERVE side).

Phase 1 is read-only observation:
  * trigger_check() launches the read-only `updates-check.yml` play through the
    EXISTING run_playbook Celery task (no new execution path).
  * get_status() reads the per-host JSON fragments that play writes to
    state/node_update_status/ and returns them for the dashboard.

Actual patching (running 01-linux-updates.yml / k3s-updates.yml) is the execute
path and arrives in Phase 2 — intentionally not here.
"""

from __future__ import annotations

import json
import os

from config import cfg

CHECK_PLAYBOOK = str(cfg.PLAYBOOK_DIR / "updates-check.yml")
STATUS_DIR = cfg.ROOT_DIR / "state" / "node_update_status"
DEFAULT_INVENTORY = str(cfg.INVENTORY_DIR / "inventory.yml")
# Default scope for Phase 1: the k3s control-plane group (k8s01-03).
DEFAULT_GROUP = "k8s_linux"


def trigger_check(group: str = DEFAULT_GROUP, inventory: str | None = None):
    """
    Launch the read-only drift check as a Celery task. Returns the AsyncResult
    so the route can hand back a session_id + stream URL (reusing the unified
    task stream).
    """
    from tasks.workflows import run_playbook as run_playbook_task

    inventory_path = inventory or DEFAULT_INVENTORY
    return run_playbook_task.delay(
        playbook_path=CHECK_PLAYBOOK,
        inventory_path=inventory_path,
        group=group or "",
        verbosity="",
    )


def get_status() -> dict:
    """
    Read all per-host drift fragments. Returns:
        {"nodes": [...], "last_checked": "<iso8601 or None>"}
    Empty/no directory yet → empty list (dashboard shows "run a check").
    """
    nodes: list[dict] = []
    last_checked: str | None = None

    if not STATUS_DIR.is_dir():
        return {"nodes": nodes, "last_checked": None}

    for entry in sorted(os.listdir(STATUS_DIR)):
        if not entry.endswith(".json"):
            continue
        try:
            with open(STATUS_DIR / entry, "r") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        nodes.append(data)
        ts = data.get("checked_at")
        if ts and (last_checked is None or ts > last_checked):
            last_checked = ts

    nodes.sort(key=lambda n: n.get("host", ""))
    return {"nodes": nodes, "last_checked": last_checked}

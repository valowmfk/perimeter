"""
Updates page API — unified update cockpit (Phase 1: read-only observe).

Two tracks, deliberately kept apart in code to make the single-writer rule
structural, not conventional:
  * NODE track  — observe drift via helpers.node_updates (read-only check play).
  * POD track   — observe ArgoCD app state via helpers.argocd (read-only client).

There is NO cluster-mutation path in this blueprint. Pod updates are ArgoCD's
job; node patching (the execute path) lands in a later phase.
"""

import os

import redis
from flask import Blueprint, jsonify, request

from config import cfg
from .audit import audit_log
from .shared import api_error, is_safe_filename

updates_bp = Blueprint("updates", __name__)

_redis = redis.Redis.from_url(cfg.REDIS_URL)

# Node track is EXECUTE-only through these two vetted playbooks. k8s_linux uses
# the quorum-safe k3s playbook (serial:1 lives inside it); everything else uses
# the universal linux updater. No arbitrary playbook is runnable here.
NODE_PLAYBOOKS = {"k8s_linux": "k3s-updates.yml"}
DEFAULT_NODE_PLAYBOOK = "01-linux-updates.yml"
ALLOWED_NODE_PLAYBOOKS = {"k3s-updates.yml", "01-linux-updates.yml"}


def _playbook_for_group(group: str) -> str:
    return NODE_PLAYBOOKS.get(group, DEFAULT_NODE_PLAYBOOK)


def _argocd_block(force: bool = False) -> dict:
    """ArgoCD observe payload, degrading gracefully to an error banner."""
    from helpers import argocd

    if not argocd.is_configured():
        return {"configured": False, "ok": False, "error": None,
                "applications": [], "summary": {"total": 0, "out_of_sync": 0, "unhealthy": 0}}
    try:
        apps = argocd.list_applications(force=force)
    except argocd.ArgocdError as e:
        return {"configured": True, "ok": False, "error": str(e),
                "applications": [], "summary": {"total": 0, "out_of_sync": 0, "unhealthy": 0}}
    return {"configured": True, "ok": True, "error": None,
            "applications": apps, "summary": argocd.summarize(apps)}


@updates_bp.route("/api/updates/overview")
def api_updates_overview():
    """Unified dashboard payload: node drift + pod (ArgoCD) drift."""
    from helpers import node_updates

    from helpers import renovate

    force = request.args.get("refresh") == "1"
    node_status = node_updates.get_status()
    return jsonify({
        "nodes": node_status["nodes"],
        "nodes_last_checked": node_status["last_checked"],
        "argocd": _argocd_block(force=force),
        "renovate": renovate.get_data(force=force),
    })


@updates_bp.route("/api/updates/argocd")
def api_updates_argocd():
    """ArgoCD applications only (read-only)."""
    return jsonify(_argocd_block(force=request.args.get("refresh") == "1"))


@updates_bp.route("/api/updates/nodes")
def api_updates_nodes():
    """Cached node drift fragments only."""
    from helpers import node_updates
    return jsonify(node_updates.get_status())


@updates_bp.route("/api/updates/nodes/check", methods=["POST"])
def api_updates_nodes_check():
    """
    Trigger the read-only drift check play against an inventory group.
    Returns a session id + stream URL (reuses the unified task stream).
    """
    from helpers import node_updates

    data = request.json or {}
    group = (data.get("group") or node_updates.DEFAULT_GROUP).strip()

    # Reuse the same filename safety used for playbook/inventory names.
    from .shared import is_safe_filename
    if group and not is_safe_filename(group):
        return api_error("Invalid group name")

    audit_log("updates_node_check", "updates-check.yml", f"group={group}")
    task = node_updates.trigger_check(group=group)
    return jsonify({
        "session_id": task.id,
        "stream_url": f"/api/tasks/{task.id}/stream",
    })


# ── Node execution (Phase 2) ────────────────────────────────

@updates_bp.route("/api/updates/nodes/run", methods=["POST"])
def api_updates_nodes_run():
    """
    Trigger a node-update playbook against an inventory group. EXECUTE path.
    Guardrails: playbook allow-list, vetted extra-vars (no serial override),
    and a Redis lock so two node updates never run against the same group.
    """
    from helpers import node_updates

    data = request.json or {}
    group = (data.get("group") or "").strip()
    if not group or not is_safe_filename(group):
        return api_error("A valid inventory group is required")

    playbook = _playbook_for_group(group)
    if playbook not in ALLOWED_NODE_PLAYBOOKS:
        return api_error("Update playbook not permitted", 403)
    playbook_path = str(cfg.PLAYBOOK_DIR / playbook)
    if not os.path.isfile(playbook_path):
        return api_error("Update playbook not found", 404)

    # Vetted extra-vars only. `serial` and friends are never accepted.
    extra_vars: dict = {}
    if "allow_reboot" in data:
        extra_vars["allow_reboot"] = bool(data["allow_reboot"])
    if data.get("min_uptime_minutes") is not None:
        try:
            extra_vars["min_uptime_minutes"] = max(0, int(data["min_uptime_minutes"]))
        except (ValueError, TypeError):
            return api_error("min_uptime_minutes must be an integer")
    if playbook == "k3s-updates.yml" and data.get("drain_timeout_seconds") is not None:
        try:
            extra_vars["drain_timeout_seconds"] = max(1, int(data["drain_timeout_seconds"]))
        except (ValueError, TypeError):
            return api_error("drain_timeout_seconds must be an integer")

    # Concurrency guard: one node update per group at a time (cluster safety).
    lock_key = f"perimeter:nodeupdate:lock:{group}"
    if not _redis.set(lock_key, "1", nx=True, ex=7200):
        return api_error("A node update is already running for this group", 409)

    audit_log("updates_node_run", playbook, f"group={group} vars={extra_vars}")
    from tasks.workflows import run_node_update
    try:
        task = run_node_update.delay(
            playbook_path=playbook_path,
            inventory_path=node_updates.DEFAULT_INVENTORY,
            group=group,
            extra_vars=extra_vars,
            lock_key=lock_key,
            triggered_by="ui",
        )
    except Exception as e:
        _redis.delete(lock_key)  # never leak the lock if enqueue fails
        return api_error(f"Failed to enqueue update: {e}", 500)

    return jsonify({
        "session_id": task.id,
        "stream_url": f"/api/tasks/{task.id}/stream",
        "playbook": playbook,
        "group": group,
    })


@updates_bp.route("/api/updates/nodes/runs")
def api_updates_nodes_runs():
    """Persisted node-update run history (newest first)."""
    from helpers import run_history
    try:
        limit = min(200, max(1, int(request.args.get("limit", 50))))
    except (ValueError, TypeError):
        limit = 50
    return jsonify({"runs": run_history.list_runs(limit)})


@updates_bp.route("/api/updates/nodes/runs/<int:run_id>")
def api_updates_nodes_run_detail(run_id):
    from helpers import run_history
    run = run_history.get_run(run_id)
    if not run:
        return api_error("Run not found", 404)
    return jsonify(run)


@updates_bp.route("/api/updates/nodes/running")
def api_updates_nodes_running():
    """Groups with an in-progress node update (locked) — for button state."""
    locked = []
    try:
        for k in _redis.scan_iter("perimeter:nodeupdate:lock:*"):
            locked.append(k.decode().rsplit(":", 1)[-1])
    except Exception:
        pass
    return jsonify({"running_groups": locked})


# ── Pod orchestration (Phase 3) ─────────────────────────────

@updates_bp.route("/api/updates/renovate")
def api_updates_renovate():
    """Renovate PRs + dependency dashboard (read-only observe)."""
    from helpers import renovate
    return jsonify(renovate.get_data(force=request.args.get("refresh") == "1"))


@updates_bp.route("/api/updates/argocd/sync", methods=["POST"])
def api_updates_argocd_sync():
    """
    Trigger ArgoCD to sync an app (git → cluster). Sanctioned native-tool
    trigger — NOT a direct cluster write. Perimeter never merges/commits.
    """
    from helpers import argocd

    data = request.json or {}
    app = (data.get("app") or "").strip()
    if not app or not is_safe_filename(app):
        return api_error("A valid ArgoCD application name is required")
    dry_run = bool(data.get("dry_run"))

    audit_log("updates_argocd_sync", app, f"dry_run={dry_run}")
    try:
        result = argocd.sync(app, dry_run=dry_run)
    except argocd.ArgocdError as e:
        return api_error(str(e), 502)
    return jsonify(result)


# ── Node update schedules (Phase 4) ─────────────────────────

@updates_bp.route("/api/updates/schedules")
def api_updates_schedules():
    from helpers import scheduler
    return jsonify({"schedules": scheduler.list_schedules()})


@updates_bp.route("/api/updates/schedules", methods=["POST"])
def api_updates_schedules_create():
    from helpers import scheduler

    data = request.json or {}
    name = (data.get("name") or "").strip()
    group = (data.get("group") or "").strip()
    frequency = (data.get("frequency") or "").strip()
    if not name or len(name) > 60:
        return api_error("A schedule name (<=60 chars) is required")
    if not group or not is_safe_filename(group):
        return api_error("A valid inventory group is required")
    if frequency not in ("daily", "weekly"):
        return api_error("frequency must be 'daily' or 'weekly'")
    try:
        hour = int(data.get("hour"))
        minute = int(data.get("minute"))
    except (ValueError, TypeError):
        return api_error("hour and minute must be integers")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return api_error("hour 0-23, minute 0-59")
    dow = None
    if frequency == "weekly":
        try:
            dow = int(data.get("day_of_week"))
        except (ValueError, TypeError):
            return api_error("weekly schedules need day_of_week 0-6 (Mon-Sun)")
        if not (0 <= dow <= 6):
            return api_error("day_of_week must be 0-6 (Mon-Sun)")

    min_uptime = data.get("min_uptime_minutes")
    drain = data.get("drain_timeout_seconds")
    try:
        min_uptime = int(min_uptime) if min_uptime is not None else None
        drain = int(drain) if drain is not None else None
    except (ValueError, TypeError):
        return api_error("min_uptime_minutes / drain_timeout_seconds must be integers")

    sid = scheduler.create_schedule(
        name=name, group_name=group, frequency=frequency, hour=hour, minute=minute,
        tz=(data.get("tz") or scheduler.DEFAULT_TZ), day_of_week=dow,
        allow_reboot=bool(data.get("allow_reboot", True)),
        min_uptime_minutes=min_uptime, drain_timeout_seconds=drain,
        enabled=bool(data.get("enabled", True)),
    )
    audit_log("updates_schedule_create", name, f"group={group} {frequency}")
    return jsonify(scheduler.get_schedule(sid))


@updates_bp.route("/api/updates/schedules/<int:sid>", methods=["PATCH"])
def api_updates_schedules_patch(sid):
    from helpers import scheduler
    if not scheduler.get_schedule(sid):
        return api_error("Schedule not found", 404)
    data = request.json or {}
    if "enabled" in data:
        scheduler.set_enabled(sid, bool(data["enabled"]))
    return jsonify(scheduler.get_schedule(sid))


@updates_bp.route("/api/updates/schedules/<int:sid>", methods=["DELETE"])
def api_updates_schedules_delete(sid):
    from helpers import scheduler
    if not scheduler.get_schedule(sid):
        return api_error("Schedule not found", 404)
    scheduler.delete_schedule(sid)
    audit_log("updates_schedule_delete", str(sid), "")
    return jsonify({"deleted": sid})

"""
Updates page API — unified update cockpit (Phase 1: read-only observe).

Two tracks, deliberately kept apart in code to make the single-writer rule
structural, not conventional:
  * NODE track  — observe drift via helpers.node_updates (read-only check play).
  * POD track   — observe ArgoCD app state via helpers.argocd (read-only client).

There is NO cluster-mutation path in this blueprint. Pod updates are ArgoCD's
job; node patching (the execute path) lands in a later phase.
"""

from flask import Blueprint, jsonify, request

from .audit import audit_log
from .shared import api_error

updates_bp = Blueprint("updates", __name__)


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

    force = request.args.get("refresh") == "1"
    node_status = node_updates.get_status()
    return jsonify({
        "nodes": node_status["nodes"],
        "nodes_last_checked": node_status["last_checked"],
        "argocd": _argocd_block(force=force),
        # Renovate panel is deferred until Renovate is stood up (Phase 3 prereq).
        "renovate": {"available": False,
                     "note": "Renovate not yet deployed — pending standup."},
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

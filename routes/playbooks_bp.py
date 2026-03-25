"""Playbook and inventory management routes."""

import os

from flask import Blueprint, Response, jsonify, request

from .audit import audit_log
from .shared import (
    INVENTORY_DIR,
    PLAYBOOK_DIR,
    api_error,
    is_safe_filename,
    list_inventories,
    list_playbooks,
    load_inventory_yaml,
)

playbooks_bp = Blueprint("playbooks", __name__)


@playbooks_bp.route("/list_playbooks")
def api_list_playbooks():
    return jsonify(list_playbooks())


@playbooks_bp.route("/list_inventories")
def api_list_inventories():
    return jsonify(list_inventories())


@playbooks_bp.route("/inventory/groups")
def api_inventory_groups():
    filename = request.args.get("file", "")
    data = load_inventory_yaml(filename)
    groups = sorted(list(data.keys()))
    return jsonify(groups)


@playbooks_bp.route("/inventory/hosts")
def api_inventory_hosts():
    filename = request.args.get("file", "")
    group = request.args.get("group", "")
    data = load_inventory_yaml(filename)

    def get_hosts_from_group(group_name, inventory_data, visited=None):
        if visited is None:
            visited = set()
        if group_name in visited:
            return []
        visited.add(group_name)
        hosts = []
        group_data = inventory_data.get(group_name, {})
        if isinstance(group_data, dict):
            hosts_dict = group_data.get("hosts", {})
            if isinstance(hosts_dict, dict):
                hosts.extend(list(hosts_dict.keys()))
            elif isinstance(hosts_dict, list):
                hosts.extend(hosts_dict)
            children = group_data.get("children", {})
            if isinstance(children, dict):
                for child_group in children.keys():
                    hosts.extend(get_hosts_from_group(child_group, inventory_data, visited))
            elif isinstance(children, list):
                for child_group in children:
                    hosts.extend(get_hosts_from_group(child_group, inventory_data, visited))
        return hosts

    hosts = get_hosts_from_group(group, data)
    hosts = sorted(list(set(hosts)))
    return jsonify({"file": filename, "group": group, "hosts": hosts})


@playbooks_bp.route("/view")
def view_playbook():
    filename = request.args.get("file")
    if not filename or not is_safe_filename(filename):
        return api_error("Invalid filename")
    if not filename.endswith((".yml", ".yaml")):
        return api_error("Only YAML playbooks can be viewed")

    file_path = os.path.join(PLAYBOOK_DIR, filename)
    real_playbook_dir = os.path.realpath(PLAYBOOK_DIR)
    real_file_path = os.path.realpath(file_path)
    if not real_file_path.startswith(real_playbook_dir):
        return api_error("Access denied", 403)

    try:
        with open(file_path, 'r') as f:
            content = f.read()
        return content
    except FileNotFoundError:
        return api_error("Playbook not found", 404)
    except PermissionError:
        return api_error("Permission denied", 403)
    except Exception as e:
        return api_error(f"Error reading playbook: {str(e)}", 500)


@playbooks_bp.route("/run", methods=["POST"])
def run_playbook():
    data = request.json or {}
    file = data.get("file")
    inventory = data.get("inventory")
    verbosity = data.get("verbosity", "")
    group = data.get("group", "")

    if not file or not inventory:
        return api_error("file and inventory are required")
    if not is_safe_filename(file):
        return api_error("Invalid playbook filename")
    if not is_safe_filename(inventory):
        return api_error("Invalid inventory filename")

    allowed_verbosity = ["", "-v", "-vv", "-vvv", "-vvvv"]
    if verbosity not in allowed_verbosity:
        return api_error("Invalid verbosity level")

    playbook_path = os.path.join(PLAYBOOK_DIR, file)
    inventory_path = os.path.join(INVENTORY_DIR, inventory)

    audit_log("playbook_run", file, f"inventory={inventory} group={group}")

    from tasks.workflows import run_playbook as run_playbook_task
    task = run_playbook_task.delay(
        playbook_path=playbook_path,
        inventory_path=inventory_path,
        group=group,
        verbosity=verbosity,
    )

    return jsonify({
        "session_id": task.id,
        "stream_url": f"/api/tasks/{task.id}/stream",
    })


@playbooks_bp.route("/stream/<session_id>")
def stream_output(session_id):
    """SSE stream for playbook output — delegates to unified task stream."""
    from utils.redis_stream import sse_subscribe, get_task_channel

    return Response(
        sse_subscribe(get_task_channel(session_id)),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

    return Response(generate(), mimetype="text/event-stream")

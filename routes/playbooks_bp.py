"""Playbook and inventory management routes."""

import os
import subprocess
import time
import uuid

from flask import Blueprint, Response, jsonify, request

from .audit import audit_log
from utils.metrics import PLAYBOOK_RUNS, PLAYBOOK_RUNS_ACTIVE
from .shared import (
    INVENTORY_DIR,
    PLAYBOOK_DIR,
    SUBPROCESS_TIMEOUT,
    api_error,
    is_safe_filename,
    list_inventories,
    list_playbooks,
    load_inventory_yaml,
    running_processes,
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

    session_id = str(uuid.uuid4())
    start_time = time.time()
    playbook_path = os.path.join(PLAYBOOK_DIR, file)

    cmd = [
        "ansible-playbook",
        "-i", os.path.join(INVENTORY_DIR, inventory),
        "--private-key", os.path.expanduser("~/.ssh/ansible"),
        playbook_path,
    ]

    apply_group_limit = True
    if group:
        try:
            with open(playbook_path, 'r') as f:
                content = f.read()
                if 'hosts: localhost' in content or 'hosts: "localhost"' in content:
                    apply_group_limit = False
        except Exception:
            pass

    if group and apply_group_limit:
        cmd.extend(["-l", group])
    if verbosity:
        cmd.append(verbosity)

    audit_log("playbook_run", file, f"inventory={inventory} group={group}")

    from utils.qlog import get_correlation_id
    env = os.environ.copy()
    cid = get_correlation_id()
    if cid:
        env["PERIMETER_CORRELATION_ID"] = cid

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )

    running_processes[session_id] = {
        "process": process,
        "start_time": start_time,
        "playbook": file,
        "inventory": inventory,
        "verbosity": verbosity,
        "group": group,
    }

    return jsonify({"session_id": session_id})


@playbooks_bp.route("/stream/<session_id>")
def stream_output(session_id):
    data = running_processes.get(session_id)
    if not data:
        return api_error("Invalid session", 404)

    process = data["process"]

    playbook_name = data.get("file", "unknown")
    PLAYBOOK_RUNS_ACTIVE.inc()

    def generate():
        for raw in process.stdout:
            line = raw.rstrip()
            yield f"data: {line}\n\n"
        try:
            process.wait(timeout=SUBPROCESS_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            yield f"data: [QBRANCH] Process killed after {SUBPROCESS_TIMEOUT}s timeout\n\n"
        status = "success" if process.returncode == 0 else "failed"
        PLAYBOOK_RUNS.labels(playbook=playbook_name, status=status).inc()
        PLAYBOOK_RUNS_ACTIVE.dec()
        yield "data: __COMPLETE__\n\n"

    return Response(generate(), mimetype="text/event-stream")

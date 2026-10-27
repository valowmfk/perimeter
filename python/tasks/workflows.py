"""Celery tasks for long-running workflows.

Wraps subprocess calls and publishes stdout to Redis pub/sub
for SSE streaming to the browser.
"""

import os
import subprocess
import time

import redis

from celery_app import celery
from config import cfg
from utils.metrics import VM_PROVISIONS, VM_PROVISIONS_ACTIVE, PLAYBOOK_RUNS, \
    PLAYBOOK_RUNS_ACTIVE, TEMPLATE_REFRESHES

_redis = redis.Redis.from_url(cfg.REDIS_URL)


def _run_subprocess(task, cmd, cwd, env_extra=None, channel=None, capture_recap=False):
    """Run a subprocess and publish stdout to Redis pub/sub.

    Args:
        task: Celery task instance (for self.request.id)
        cmd: Command list to execute
        cwd: Working directory
        env_extra: Additional environment variables
        channel: Redis pub/sub channel (defaults to perimeter:task:<task_id>)
        capture_recap: keep the final Ansible "PLAY RECAP" block (bounded — only
            the last recap's host lines) and return it as result["recap"]

    Returns:
        dict with returncode and status (+ recap when capture_recap)
    """
    task_id = task.request.id
    pub_channel = channel or f"perimeter:task:{task_id}"

    env = os.environ.copy()
    # Propagate SOPS key
    if "SOPS_AGE_KEY_FILE" not in env:
        age_key = os.path.expanduser("~/.config/sops/age/keys.txt")
        if os.path.exists(age_key):
            env["SOPS_AGE_KEY_FILE"] = age_key

    if env_extra:
        env.update(env_extra)
    env["PERIMETER_CORRELATION_ID"] = task_id

    # Store task metadata in Redis for job listing
    _redis.hset(f"perimeter:job:{task_id}", mapping={
        "status": "running",
        "started_at": str(time.time()),
        "command": " ".join(cmd[:3]),  # brief command summary
        "channel": pub_channel,
    })
    _redis.expire(f"perimeter:job:{task_id}", 7200)  # 2 hour TTL

    recap_lines = []
    in_recap = False

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=cwd,
            env=env,
        )

        for line in proc.stdout:
            s = line.rstrip()
            _redis.publish(pub_channel, s)
            if capture_recap:
                if "PLAY RECAP" in s:
                    in_recap, recap_lines = True, []   # keep only the final recap
                elif in_recap and s.strip():
                    recap_lines.append(s)

        try:
            proc.wait(timeout=3600)
        except subprocess.TimeoutExpired:
            proc.kill()
            _redis.publish(pub_channel, "[PERIMETER] ERROR: Process killed after timeout")

        returncode = proc.returncode
        status = "success" if returncode == 0 else "failed"

    except Exception as e:
        _redis.publish(pub_channel, f"[PERIMETER] ERROR: {e}")
        returncode = 1
        status = "error"

    # Signal completion
    _redis.publish(pub_channel, "__COMPLETE__")

    # Update job metadata
    _redis.hset(f"perimeter:job:{task_id}", mapping={
        "status": status,
        "finished_at": str(time.time()),
        "returncode": str(returncode),
    })

    result = {"returncode": returncode, "status": status, "task_id": task_id}
    if capture_recap:
        result["recap"] = "\n".join(recap_lines)
    return result


# ── VM Provisioning ──────────────────────────────────────

@celery.task(bind=True, name="perimeter.provision_vm")
def provision_vm(self, hostname, ip, vm_id, template, cpu, ram, disk, node,
                 vm_type, acos_version, bridges, template_id=0):
    """Provision a VM (Linux, vThunder, or VyOS)."""
    VM_PROVISIONS_ACTIVE.inc()

    root_dir = str(cfg.ROOT_DIR)
    work_dir = os.path.join(root_dir, "python")

    import json
    cmd = [
        "python3", f"workflows/provision_{vm_type}.py",
        hostname, ip, str(vm_id), template, str(template_id),
        str(cpu), str(ram), str(disk), node,
        vm_type, acos_version or "", json.dumps(bridges),
    ]

    result = _run_subprocess(self, cmd, work_dir)

    VM_PROVISIONS.labels(vm_type=vm_type, status=result["status"]).inc()
    VM_PROVISIONS_ACTIVE.dec()

    return result


# ── VM Destroy ───────────────────────────────────────────

@celery.task(bind=True, name="perimeter.destroy_vm")
def destroy_vm(self, vm_id):
    """Destroy a VM by VMID."""
    root_dir = str(cfg.ROOT_DIR)
    work_dir = os.path.join(root_dir, "python")

    cmd = ["python3", "workflows/destroy_vm.py", str(vm_id)]

    return _run_subprocess(self, cmd, work_dir)


# ── VM Bootstrap ─────────────────────────────────────────

@celery.task(bind=True, name="perimeter.bootstrap_vm")
def bootstrap_vm(self, hostname, ip):
    """Re-run bootstrap on an existing VM."""
    root_dir = str(cfg.ROOT_DIR)
    work_dir = os.path.join(root_dir, "python")

    cmd = ["python3", "workflows/bootstrap_linux.py", hostname, ip]

    return _run_subprocess(self, cmd, work_dir)


# ── Playbook Execution ───────────────────────────────────

@celery.task(bind=True, name="perimeter.run_playbook")
def run_playbook(self, playbook_path, inventory_path, group="", verbosity="",
                 ssh_key="~/.ssh/ansible_perimeter"):
    """Execute an Ansible playbook."""
    PLAYBOOK_RUNS_ACTIVE.inc()

    cmd = [
        "ansible-playbook",
        "-i", inventory_path,
        "--private-key", os.path.expanduser(ssh_key),
        playbook_path,
    ]

    if group:
        # Check if playbook targets localhost
        apply_limit = True
        try:
            with open(playbook_path, 'r') as f:
                content = f.read()
                if 'hosts: localhost' in content or 'hosts: "localhost"' in content:
                    apply_limit = False
        except Exception:
            pass
        if apply_limit:
            cmd.extend(["-l", group])

    if verbosity:
        cmd.append(verbosity)

    result = _run_subprocess(self, cmd, str(cfg.ROOT_DIR))

    playbook_name = os.path.basename(playbook_path)
    PLAYBOOK_RUNS.labels(playbook=playbook_name, status=result["status"]).inc()
    PLAYBOOK_RUNS_ACTIVE.dec()

    return result


# ── Node Update Execution (Updates page, Phase 2) ──────────

@celery.task(bind=True, name="perimeter.run_node_update")
def run_node_update(self, playbook_path, inventory_path, group="",
                    extra_vars=None, lock_key=None, triggered_by=""):
    """Execute a node-update playbook with run history + kernel before/after.

    The route holds the concurrency lock; this task releases it when done.
    serial:1 lives in the playbook and is NEVER overridden here.
    """
    import json
    from helpers import run_history, node_updates

    task_id = self.request.id
    playbook_name = os.path.basename(playbook_path)
    run_id = None
    PLAYBOOK_RUNS_ACTIVE.inc()
    # Tag job metadata so the UI's global feed can surface UNATTENDED (scheduler-
    # fired) runs — which no drawer is listening for — without double-streaming
    # operator-triggered runs (those stay "ui").
    try:
        _redis.hset(f"perimeter:job:{task_id}", "triggered_by", triggered_by or "ui")
    except Exception:
        pass
    try:
        # Snapshot kernels before patching (best-effort — never fail the run)
        t0 = node_updates.utcnow_iso()
        before = {}
        try:
            node_updates.run_check_sync(group)
            before = node_updates.kernels_since(t0)
        except Exception:
            pass
        run_id = run_history.record_start(
            task_id, playbook_name, group,
            json.dumps(before) if before else None, triggered_by,
        )

        cmd = ["ansible-playbook", "-i", inventory_path,
               "--private-key", os.path.expanduser("~/.ssh/ansible_perimeter"),
               playbook_path]
        if group:
            cmd += ["-l", group]
        if extra_vars:
            cmd += ["--extra-vars", json.dumps(extra_vars)]

        result = _run_subprocess(self, cmd, str(cfg.ROOT_DIR), capture_recap=True)
        PLAYBOOK_RUNS.labels(playbook=playbook_name, status=result["status"]).inc()

        # Snapshot kernels after; reboot = any targeted host's kernel changed
        t1 = node_updates.utcnow_iso()
        after = {}
        try:
            node_updates.run_check_sync(group)
            after = node_updates.kernels_since(t1)
        except Exception:
            pass
        reboot = None
        if before and after:
            reboot = any(before.get(h) != after.get(h) for h in before)

        recap = run_history.parse_recap(result.get("recap", ""))
        if run_id is not None:
            run_history.record_end(
                run_id, result["status"],
                json.dumps(after) if after else None,
                reboot, str(result.get("returncode")),
                json.dumps(recap) if recap else None,
            )
        return result
    except Exception as e:
        if run_id is not None:
            try:
                run_history.record_end(run_id, "error", None, None, str(e))
            except Exception:
                pass
        raise
    finally:
        PLAYBOOK_RUNS_ACTIVE.dec()
        if lock_key:
            try:
                _redis.delete(lock_key)
            except Exception:
                pass


@celery.task(name="perimeter.scheduler_tick")
def scheduler_tick():
    """
    Celery Beat tick (every 60s): fire any due node-update schedules.

    Takes the per-group lock before dispatching (same guardrail as a manual run)
    and skips a group that's already running — a scheduled window never stacks
    on or overrides an in-flight run. serial:1 still lives in the playbook.
    """
    from helpers import scheduler, node_updates

    fired = []
    for s in scheduler.due_schedules():
        group = s["group_name"]
        lock_key = f"perimeter:nodeupdate:lock:{group}"
        if not _redis.set(lock_key, "1", nx=True, ex=7200):
            continue  # a run is already active for this group — try next tick
        playbook = "k3s-updates.yml" if group == "k8s_linux" else "01-linux-updates.yml"
        extra = {"allow_reboot": bool(s["allow_reboot"])}
        if s["min_uptime_minutes"] is not None:
            extra["min_uptime_minutes"] = s["min_uptime_minutes"]
        if group == "k8s_linux" and s["drain_timeout_seconds"] is not None:
            extra["drain_timeout_seconds"] = s["drain_timeout_seconds"]
        run_node_update.delay(
            playbook_path=str(cfg.PLAYBOOK_DIR / playbook),
            inventory_path=node_updates.DEFAULT_INVENTORY,
            group=group, extra_vars=extra, lock_key=lock_key,
            triggered_by=f"schedule:{s['name']}",
        )
        scheduler.mark_fired(s["id"])
        fired.append(s["id"])
    return {"fired": fired}


# ── Template Refresh ─────────────────────────────────────

@celery.task(bind=True, name="perimeter.refresh_template")
def refresh_template(self, template_name, node="goldfinger"):
    """Refresh a Proxmox VM template."""
    root_dir = str(cfg.ROOT_DIR)

    cmd = [
        "python3", "-u",
        os.path.join(root_dir, "python", "workflows", "refresh_template.py"),
        template_name,
        "--node", node,
    ]

    result = _run_subprocess(self, cmd, root_dir)

    TEMPLATE_REFRESHES.labels(template=template_name, status=result["status"]).inc()

    return result


# ── Certbot Operations ───────────────────────────────────

@celery.task(bind=True, name="perimeter.run_certbot")
def run_certbot(self, compose_path, service, cert_args):
    """Run a certbot command via docker compose."""
    cmd = [
        "docker", "compose", "-f", compose_path,
        "run", "--rm", service,
    ] + cert_args

    return _run_subprocess(self, cmd, os.path.dirname(compose_path))


# ── Fleet Orchestrator ───────────────────────────────────

@celery.task(bind=True, name="perimeter.run_fleet")
def run_fleet(self, fleet_id, vms):
    """Orchestrate fleet deployment — sequential within each VM type, parallel across types.

    Groups VMs by vm_type (terraform workspace), then runs each group sequentially
    to avoid Terraform state lock conflicts. Different types run in parallel via threading.
    """
    import json
    from collections import defaultdict
    from threading import Thread

    root_dir = str(cfg.ROOT_DIR)
    work_dir = os.path.join(root_dir, "python")

    # Group VMs by type
    type_groups = defaultdict(list)
    for i, vm in enumerate(vms):
        vm_type = vm.get("vm_type", "linux")
        type_groups[vm_type].append((i, vm))

    def run_group(vm_type, group_vms):
        """Run VMs of the same type sequentially."""
        for idx, vm in group_vms:
            task_id = f"{fleet_id}-vm-{idx}"
            hostname = vm.get("hostname", "").strip()
            pub_channel = f"perimeter:task:{task_id}"

            # Set job metadata
            _redis.hset(f"perimeter:job:{task_id}", mapping={
                "status": "running",
                "started_at": str(time.time()),
                "command": f"provision {vm_type} {hostname}",
                "channel": pub_channel,
            })
            _redis.expire(f"perimeter:job:{task_id}", 7200)

            cmd = [
                "python3", f"workflows/provision_{vm_type}.py",
                hostname,
                vm.get("ip", "").strip(),
                str(vm.get("vm_id", 0)),
                vm.get("template", ""),
                str(vm.get("template_id", 0)),
                str(vm.get("cpu", 2)),
                str(vm.get("ram", 4096)),
                str(vm.get("disk", 32)),
                vm.get("node", "goldfinger"),
                vm_type,
                vm.get("acos_version", ""),
                json.dumps(vm.get("bridges", ["vmbr0"])),
            ]

            env = os.environ.copy()
            if "SOPS_AGE_KEY_FILE" not in env:
                age_key = os.path.expanduser("~/.config/sops/age/keys.txt")
                if os.path.exists(age_key):
                    env["SOPS_AGE_KEY_FILE"] = age_key
            env["PERIMETER_CORRELATION_ID"] = task_id

            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, cwd=work_dir, env=env,
                )

                for line in proc.stdout:
                    _redis.publish(pub_channel, line.rstrip())

                proc.wait(timeout=3600)
                returncode = proc.returncode
                status = "success" if returncode == 0 else "failed"

            except Exception as e:
                _redis.publish(pub_channel, f"[PERIMETER] ERROR: {e}")
                returncode = 1
                status = "error"

            _redis.publish(pub_channel, "__COMPLETE__")
            _redis.hset(f"perimeter:job:{task_id}", mapping={
                "status": status,
                "finished_at": str(time.time()),
                "returncode": str(returncode),
            })

            VM_PROVISIONS.labels(vm_type=vm_type, status=status).inc()

    # Set initial "queued" status for all VMs
    for i, vm in enumerate(vms):
        task_id = f"{fleet_id}-vm-{i}"
        _redis.hset(f"perimeter:job:{task_id}", mapping={
            "status": "queued",
            "started_at": str(time.time()),
            "command": f"provision {vm.get('vm_type', 'linux')} {vm.get('hostname', '')}",
            "channel": f"perimeter:task:{task_id}",
        })
        _redis.expire(f"perimeter:job:{task_id}", 7200)

    # Run each type group in a separate thread (parallel across types)
    threads = []
    for vm_type, group_vms in type_groups.items():
        t = Thread(target=run_group, args=(vm_type, group_vms), daemon=True)
        t.start()
        threads.append(t)

    # Wait for all groups to complete
    for t in threads:
        t.join(timeout=7200)

    return {"fleet_id": fleet_id, "total": len(vms), "status": "complete"}

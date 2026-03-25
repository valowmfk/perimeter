"""Job management API — Celery task status, streaming, and control."""

import redis
from flask import Blueprint, Response, jsonify

from config import cfg
from .shared import api_error

jobs_bp = Blueprint("jobs", __name__)

_redis = redis.Redis.from_url(cfg.REDIS_URL)


@jobs_bp.route("/api/tasks/<task_id>/stream")
def api_task_stream(task_id):
    """SSE stream for a Celery task's output via Redis pub/sub."""
    from utils.redis_stream import sse_subscribe, get_task_channel

    channel = get_task_channel(task_id)

    return Response(
        sse_subscribe(channel),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@jobs_bp.route("/api/tasks/<task_id>")
def api_task_status(task_id):
    """Get the status of a Celery task."""
    from celery_app import celery

    result = celery.AsyncResult(task_id)

    # Also check Redis metadata
    meta = _redis.hgetall(f"perimeter:job:{task_id}")
    meta_decoded = {k.decode(): v.decode() for k, v in meta.items()} if meta else {}

    return jsonify({
        "task_id": task_id,
        "state": result.state,
        "status": meta_decoded.get("status", result.state.lower()),
        "result": result.result if result.ready() else None,
        "started_at": meta_decoded.get("started_at"),
        "finished_at": meta_decoded.get("finished_at"),
    })


@jobs_bp.route("/api/tasks/<task_id>/cancel", methods=["POST"])
def api_task_cancel(task_id):
    """Cancel a running Celery task."""
    from celery_app import celery

    celery.control.revoke(task_id, terminate=True, signal="SIGTERM")

    # Update Redis metadata
    _redis.hset(f"perimeter:job:{task_id}", "status", "cancelled")

    return jsonify({"task_id": task_id, "cancelled": True})


@jobs_bp.route("/api/tasks")
def api_task_list():
    """List recent tasks from Redis metadata."""
    keys = _redis.keys("perimeter:job:*")
    jobs = []
    for key in keys:
        task_id = key.decode().split(":")[-1]
        meta = _redis.hgetall(key)
        if meta:
            jobs.append({
                "task_id": task_id,
                **{k.decode(): v.decode() for k, v in meta.items()},
            })

    # Sort by started_at descending
    jobs.sort(key=lambda j: j.get("started_at", "0"), reverse=True)

    return jsonify(jobs[:50])  # Last 50 jobs

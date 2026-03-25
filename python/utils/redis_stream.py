"""Redis pub/sub to SSE streaming helper.

Subscribes to a Redis channel and yields Server-Sent Events for Flask responses.
Used by all streaming endpoints (VM provision, playbook, template refresh, etc.)
"""

import redis
from config import cfg


def sse_subscribe(channel: str, timeout: int = 3600):
    """Subscribe to a Redis pub/sub channel and yield SSE events.

    Args:
        channel: Redis channel name (e.g., 'perimeter:task:<task_id>')
        timeout: Maximum time to listen in seconds (default 1 hour)

    Yields:
        SSE-formatted strings: 'data: <line>\n\n'
    """
    r = redis.Redis.from_url(cfg.REDIS_URL)
    pubsub = r.pubsub()
    pubsub.subscribe(channel)

    try:
        for message in pubsub.listen():
            if message["type"] == "message":
                line = message["data"].decode("utf-8", errors="replace")
                yield f"data: {line}\n\n"
                if line == "__COMPLETE__":
                    break
    finally:
        pubsub.unsubscribe(channel)
        pubsub.close()
        r.close()


def get_task_channel(task_id: str) -> str:
    """Return the standard Redis channel name for a task."""
    return f"perimeter:task:{task_id}"

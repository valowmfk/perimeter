"""Audit logging for destructive and state-changing API actions.

Logs are pushed to Loki in JSON format. If Loki is unavailable, logs are
written to stdout via qlog as a fallback (still captured by journald).

Traefik sets X-Forwarded-User after authentication; we trust that header
since Flask only listens on localhost behind the reverse proxy.
"""
from __future__ import annotations

import json
import time
from functools import wraps
from threading import Thread
from typing import Optional

import requests as _requests
from flask import request

from config import cfg

# Loki push endpoint — empty string disables remote push
LOKI_URL = getattr(cfg, "LOKI_URL", "") or ""
LOKI_PUSH_URL = f"{LOKI_URL}/loki/api/v1/push" if LOKI_URL else ""


def _get_user() -> str:
    """Extract authenticated user from Traefik forwarded headers."""
    return (
        request.headers.get("X-Forwarded-User")
        or request.headers.get("Remote-User")
        or "anonymous"
    )


def _get_source_ip() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")


def _push_to_loki(entry: dict) -> None:
    """Best-effort push to Loki. Runs in a background thread so it never blocks the request."""
    if not LOKI_PUSH_URL:
        return

    ts_ns = str(int(time.time() * 1e9))
    payload = {
        "streams": [
            {
                "stream": {
                    "app": "perimeter",
                    "level": "audit",
                    "action": entry.get("action", "unknown"),
                },
                "values": [[ts_ns, json.dumps(entry)]],
            }
        ]
    }

    try:
        _requests.post(
            LOKI_PUSH_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
    except Exception:
        pass  # best-effort — stdout fallback already happened


def audit_log(action: str, target: str, detail: Optional[str] = None) -> None:
    """Record an audit event. Call from route handlers for destructive actions.
    No-op if the audit feature is disabled."""
    if not cfg.FEATURES.get("audit", True):
        return
    import sys
    sys.path.insert(0, "") if False else None  # no-op, axapi already on path

    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "action": action,
        "user": _get_user(),
        "source_ip": _get_source_ip(),
        "target": target,
    }
    if detail:
        entry["detail"] = detail

    # Always log to stdout (captured by journald / qlog pipeline)
    from axapi.utils import qlog
    qlog("AUDIT", f"{entry['user']} | {action} | {target}" + (f" | {detail}" if detail else ""))

    # Async push to Loki
    Thread(target=_push_to_loki, args=(entry,), daemon=True).start()

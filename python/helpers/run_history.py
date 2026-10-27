"""
SQLite-backed run history for node update executions (Updates page, Phase 2).

Durable record of every node-update run: what ran, where, when, outcome, and
kernel before/after (→ reboot detection). Stdlib sqlite3 only — no new service.
DB lives under state/ (gitignored); the Celery worker writes, Flask reads.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from config import cfg

DB_PATH = cfg.ROOT_DIR / "state" / "updates.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS node_update_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT,
    playbook      TEXT NOT NULL,
    group_name    TEXT,
    status        TEXT NOT NULL,      -- running | success | failed | error (raw)
    started_at    TEXT NOT NULL,      -- ISO8601 UTC
    ended_at      TEXT,
    kernel_before TEXT,               -- JSON {host: kernel}
    kernel_after  TEXT,               -- JSON {host: kernel}
    reboot        INTEGER,            -- 1 kernel changed, 0 no, NULL unknown
    exit_status   TEXT,
    triggered_by  TEXT,
    recap         TEXT                -- JSON [{host, ok, changed, unreachable, failed, skipped}]
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _migrate(conn) -> None:
    """Additive migrations for DBs created before a column existed."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(node_update_runs)").fetchall()}
    if "recap" not in cols:
        conn.execute("ALTER TABLE node_update_runs ADD COLUMN recap TEXT")


@contextmanager
def _db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        _migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


# ── Outcome refinement ───────────────────────────────────────────────
# ansible-playbook's return code is the TaskQueueManager result bitmask:
#   0 ok · 2 failed hosts · 4 unreachable hosts · 8 failed-break-play ·
#   1/255 errors. So a run whose ONLY problem is unreachable hosts (e.g.
#   powered-off VMs) exits 4 — that's "partial", not a real failure.

def refine_status(status, exit_status) -> str:
    """Map raw status + Ansible exit code → user-facing status:
    success | partial | failed | error. 'running'/'error' pass through, and a
    non-numeric exit code falls back to the stored status."""
    if status in ("running", "error"):
        return status
    try:
        rc = int(str(exit_status).strip())
    except (TypeError, ValueError):
        return status
    if rc == 0:
        return "success"
    # Sentinels (not part of the host bitmask): generic/unknown/interrupt errors.
    if rc in (1, 99, 250, 255):
        return "error"
    if rc & 2 or rc & 8:      # a task failed on at least one host
        return "failed"
    if rc & 4:                # unreachable hosts only, nothing failed
        return "partial"
    return "error"


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_RECAP_RE = re.compile(
    r"^(?P<host>\S+)\s*:\s*ok=(?P<ok>\d+)\s+changed=(?P<changed>\d+)\s+"
    r"unreachable=(?P<unreachable>\d+)\s+failed=(?P<failed>\d+)"
    r"(?:\s+skipped=(?P<skipped>\d+))?"
)


def parse_recap(text: str) -> list[dict]:
    """Parse an Ansible ``PLAY RECAP`` block into per-host outcome counts."""
    if not text:
        return []
    hosts = []
    for raw in text.splitlines():
        m = _RECAP_RE.match(_ANSI_RE.sub("", raw).strip())
        if not m:
            continue
        hosts.append({
            "host": m.group("host"),
            "ok": int(m.group("ok")),
            "changed": int(m.group("changed")),
            "unreachable": int(m.group("unreachable")),
            "failed": int(m.group("failed")),
            "skipped": int(m.group("skipped") or 0),
        })
    return hosts


def _enrich(row: dict) -> dict:
    """Add UI-facing derived fields: status_display + parsed recap list."""
    row["status_display"] = refine_status(row.get("status"), row.get("exit_status"))
    raw = row.get("recap")
    if raw:
        try:
            row["recap"] = json.loads(raw)
        except (ValueError, TypeError):
            row["recap"] = None
    else:
        row["recap"] = None
    return row


def record_start(session_id: str, playbook: str, group_name: str,
                 kernel_before: str | None = None, triggered_by: str = "") -> int:
    """Insert a 'running' row; returns the new run id."""
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO node_update_runs "
            "(session_id, playbook, group_name, status, started_at, kernel_before, triggered_by) "
            "VALUES (?, ?, ?, 'running', ?, ?, ?)",
            (session_id, playbook, group_name, _now(), kernel_before, triggered_by),
        )
        return cur.lastrowid


def record_end(run_id: int, status: str, kernel_after: str | None = None,
               reboot: bool | None = None, exit_status: str | None = None,
               recap: str | None = None) -> None:
    """Finalize a run row."""
    reboot_val = None if reboot is None else (1 if reboot else 0)
    with _db() as conn:
        conn.execute(
            "UPDATE node_update_runs SET status=?, ended_at=?, kernel_after=?, "
            "reboot=?, exit_status=?, recap=? WHERE id=?",
            (status, _now(), kernel_after, reboot_val, exit_status, recap, run_id),
        )


def list_runs(limit: int = 50) -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM node_update_runs ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [_enrich(dict(r)) for r in rows]


def get_run(run_id: int) -> dict | None:
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM node_update_runs WHERE id=?", (int(run_id),)
        ).fetchone()
        return _enrich(dict(row)) if row else None

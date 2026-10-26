"""
SQLite-backed run history for node update executions (Updates page, Phase 2).

Durable record of every node-update run: what ran, where, when, outcome, and
kernel before/after (→ reboot detection). Stdlib sqlite3 only — no new service.
DB lives under state/ (gitignored); the Celery worker writes, Flask reads.
"""

from __future__ import annotations

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
    status        TEXT NOT NULL,      -- running | success | failed | error
    started_at    TEXT NOT NULL,      -- ISO8601 UTC
    ended_at      TEXT,
    kernel_before TEXT,               -- JSON {host: kernel}
    kernel_after  TEXT,               -- JSON {host: kernel}
    reboot        INTEGER,            -- 1 kernel changed, 0 no, NULL unknown
    exit_status   TEXT,
    triggered_by  TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def _db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


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
               reboot: bool | None = None, exit_status: str | None = None) -> None:
    """Finalize a run row."""
    reboot_val = None if reboot is None else (1 if reboot else 0)
    with _db() as conn:
        conn.execute(
            "UPDATE node_update_runs SET status=?, ended_at=?, kernel_after=?, "
            "reboot=?, exit_status=? WHERE id=?",
            (status, _now(), kernel_after, reboot_val, exit_status, run_id),
        )


def list_runs(limit: int = 50) -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM node_update_runs ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [dict(r) for r in rows]


def get_run(run_id: int) -> dict | None:
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM node_update_runs WHERE id=?", (int(run_id),)
        ).fetchone()
        return dict(row) if row else None

"""
Node-update schedules for Perimeter's Updates page (Phase 4).

SQLite-backed recurring windows (daily or weekly at a local time). A Celery Beat
tick (perimeter.scheduler_tick) reads due schedules and fires run_node_update,
reusing the per-group lock so a scheduled run can't collide with a manual one.

No cron dependency: recurrence is {frequency: daily|weekly, day_of_week, hour,
minute, tz}. next_run is stored in UTC and the tick fires whenever it's <= now
(so a missed tick still catches up on the next one).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

from config import cfg

DB_PATH = cfg.ROOT_DIR / "state" / "updates.db"
DEFAULT_TZ = "America/Denver"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS node_update_schedules (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    group_name    TEXT NOT NULL,
    frequency     TEXT NOT NULL,       -- daily | weekly
    day_of_week   INTEGER,             -- 0=Mon..6=Sun (weekly only)
    hour          INTEGER NOT NULL,
    minute        INTEGER NOT NULL,
    tz            TEXT NOT NULL,
    allow_reboot  INTEGER NOT NULL DEFAULT 1,
    min_uptime_minutes  INTEGER,
    drain_timeout_seconds INTEGER,
    enabled       INTEGER NOT NULL DEFAULT 1,
    last_run      TEXT,
    next_run      TEXT,                -- ISO8601 UTC
    created_at    TEXT NOT NULL
);
"""

DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def compute_next_run(frequency: str, day_of_week, hour: int, minute: int,
                     tz: str, after: datetime | None = None) -> str:
    """Next occurrence strictly after `after` (default now), returned as UTC ISO."""
    tzinfo = ZoneInfo(tz) if (ZoneInfo and tz) else timezone.utc
    base = (after or _now()).astimezone(tzinfo)
    target = base.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)

    if frequency == "weekly":
        dow = int(day_of_week) if day_of_week is not None else 0
        days_ahead = (dow - base.weekday()) % 7
        target = (base + timedelta(days=days_ahead)).replace(
            hour=int(hour), minute=int(minute), second=0, microsecond=0)
        if target <= base:
            target += timedelta(days=7)
    else:  # daily
        if target <= base:
            target += timedelta(days=1)

    return _iso(target)


def list_schedules() -> list[dict]:
    with _db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM node_update_schedules ORDER BY id").fetchall()]


def get_schedule(sid: int) -> dict | None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM node_update_schedules WHERE id=?", (int(sid),)).fetchone()
        return dict(row) if row else None


def create_schedule(name, group_name, frequency, hour, minute, tz=DEFAULT_TZ,
                    day_of_week=None, allow_reboot=True, min_uptime_minutes=None,
                    drain_timeout_seconds=None, enabled=True) -> int:
    nxt = compute_next_run(frequency, day_of_week, hour, minute, tz) if enabled else None
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO node_update_schedules (name, group_name, frequency, day_of_week, "
            "hour, minute, tz, allow_reboot, min_uptime_minutes, drain_timeout_seconds, "
            "enabled, next_run, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (name, group_name, frequency, day_of_week, int(hour), int(minute), tz,
             1 if allow_reboot else 0, min_uptime_minutes, drain_timeout_seconds,
             1 if enabled else 0, nxt, _iso(_now())),
        )
        return cur.lastrowid


def set_enabled(sid: int, enabled: bool) -> None:
    s = get_schedule(sid)
    if not s:
        return
    nxt = compute_next_run(s["frequency"], s["day_of_week"], s["hour"], s["minute"], s["tz"]) if enabled else None
    with _db() as conn:
        conn.execute("UPDATE node_update_schedules SET enabled=?, next_run=? WHERE id=?",
                     (1 if enabled else 0, nxt, int(sid)))


def delete_schedule(sid: int) -> None:
    with _db() as conn:
        conn.execute("DELETE FROM node_update_schedules WHERE id=?", (int(sid),))


def due_schedules(now: datetime | None = None) -> list[dict]:
    """Enabled schedules whose next_run has passed."""
    now_iso = _iso(now or _now())
    with _db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM node_update_schedules WHERE enabled=1 AND next_run IS NOT NULL "
            "AND next_run <= ? ORDER BY next_run", (now_iso,)).fetchall()]


def mark_fired(sid: int) -> None:
    """Record a fire and roll next_run forward."""
    s = get_schedule(sid)
    if not s:
        return
    nxt = compute_next_run(s["frequency"], s["day_of_week"], s["hour"], s["minute"],
                           s["tz"], after=_now())
    with _db() as conn:
        conn.execute("UPDATE node_update_schedules SET last_run=?, next_run=? WHERE id=?",
                     (_iso(_now()), nxt, int(sid)))

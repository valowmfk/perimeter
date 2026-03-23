"""Structured logging for Perimeter Automation Platform.

Provides JSON-formatted file logging with RotatingFileHandler and
human-readable ANSI-colored console output. Correlation IDs thread
through Flask requests and across subprocess boundaries via the
PERIMETER_CORRELATION_ID environment variable.

Usage — callers continue using the same qlog() API from axapi.utils.
This module is the backend that axapi.utils delegates to.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Correlation ID ───────────────────────────────────────────
_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def get_correlation_id() -> str:
    """Return the current correlation ID (empty string if unset)."""
    return _correlation_id.get()


def set_correlation_id(cid: str) -> None:
    """Set the correlation ID for the current context."""
    _correlation_id.set(cid)


def new_correlation_id() -> str:
    """Generate a new UUID4 correlation ID and set it in the current context."""
    cid = str(uuid.uuid4())
    _correlation_id.set(cid)
    return cid


def init_correlation_id_from_env() -> None:
    """Read PERIMETER_CORRELATION_ID from the environment (subprocess boundary).

    Call this at the top of workflow scripts that run as subprocesses.
    If the env var is missing, generates a new ID so logs are still correlated.
    """
    cid = os.environ.get("PERIMETER_CORRELATION_ID", "")
    if not cid:
        cid = str(uuid.uuid4())
    _correlation_id.set(cid)


# ── JSON Formatter ───────────────────────────────────────────

class JSONFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "component": getattr(record, "component", ""),
            "correlation_id": get_correlation_id(),
            "message": record.getMessage(),
        }
        # Strip empty fields
        return _json_dumps(entry)


def _json_dumps(obj: dict) -> str:
    """Compact JSON serialization without importing json at module level."""
    import json
    return json.dumps(obj, separators=(",", ":"))


# ── Console Formatter (ANSI) ─────────────────────────────────

class ANSIFormatter(logging.Formatter):
    """Human-readable format matching the original qlog() output.

    Format:  [HH:MM:SS] [PERIMETER-COMPONENT] message
    The component name is rendered in cyan.
    """

    CYAN = "\033[96m"
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        time_str = datetime.now().strftime("%H:%M:%S")
        component = getattr(record, "component", "")
        component_str = f"PERIMETER-{component}" if component else "PERIMETER"
        cid = get_correlation_id()
        cid_tag = f" [{cid[:8]}]" if cid else ""
        return f"[{time_str}] [{self.CYAN}{component_str}{self.RESET}]{cid_tag} {record.getMessage()}"


# ── Logger Setup ─────────────────────────────────────────────

_initialized = False


def setup_logging(
    *,
    log_dir: Optional[str | Path] = None,
    log_file: str = "perimeter.log",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    level: str = "INFO",
) -> None:
    """Initialize the perimeter logger with console + rotating file handlers.

    Safe to call multiple times — subsequent calls are no-ops.
    """
    global _initialized
    if _initialized:
        return
    _initialized = True

    logger = logging.getLogger("perimeter")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    # Console handler — ANSI colored, writes to stdout
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(ANSIFormatter())
    logger.addHandler(console)

    # File handler — JSON formatted, rotating
    if log_dir is None:
        # Default: <repo_root>/logs/
        log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(JSONFormatter())
    logger.addHandler(file_handler)


def get_logger() -> logging.Logger:
    """Return the perimeter logger, initializing with defaults if needed."""
    if not _initialized:
        setup_logging()
    return logging.getLogger("perimeter")

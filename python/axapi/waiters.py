"""
Wait/poll helpers for AXAPI operations.

We keep this generic so workflows can plug in whatever checks they need.
"""

from __future__ import annotations

import time
from typing import Callable, Any, Optional

from .errors import AxapiTimeoutError
from .utils import qlog_warning, qlog_success


CheckFunc = Callable[[], tuple[bool, Optional[str]]]
# CheckFunc returns (is_ready, debug_message)


def wait_for_condition(
    component: str,
    description: str,
    check: CheckFunc,
    *,
    timeout_seconds: int = 300,
    interval_seconds: int = 6,
) -> None:
    """
    Generic waiter that repeatedly calls `check()` until it returns True or timeout.

    - `component`: log prefix (e.g. "VTH-PW")
    - `description`: human-readable thing we're waiting for
    - `check()`: returns (is_ready, debug_msg)
    """
    deadline = time.time() + timeout_seconds
    attempt = 0

    while True:
        attempt += 1
        ready, debug_msg = check()

        if ready:
            qlog_success(component, f"{description} is ready (attempt {attempt})")
            return

        if debug_msg:
            qlog_warning(
                component,
                f"{description} not ready yet (attempt {attempt}): {debug_msg}",
            )

        if time.time() >= deadline:
            raise AxapiTimeoutError(
                f"Timed out after {timeout_seconds}s waiting for {description}"
            )

        time.sleep(interval_seconds)


def make_cm_not_ready_probe(response_json: Any) -> tuple[bool, Optional[str]]:
    """
    Helper to interpret the classic:

        {
          "response": {
            "status": "fail",
            "err": {
              "code": 1023464192,
              "from": "CM",
              "msg": "System is not ready yet."
            }
          }
        }

    pattern observed in your logs.

    Returns (is_ready, debug_msg).
    """
    if not isinstance(response_json, dict):
        return False, "non-dict JSON response"

    resp = response_json.get("response")
    if not isinstance(resp, dict):
        return False, "no 'response' wrapper"

    status = resp.get("status")
    err = resp.get("err") or {}
    msg = err.get("msg") if isinstance(err, dict) else None

    if msg and isinstance(msg, str) and "System is not ready yet" in msg:
        return False, msg

    if status == "fail":
        debug = msg or "AXAPI response.status=fail"
        return False, debug

    return True, None

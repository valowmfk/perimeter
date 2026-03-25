"""Shared CLI argument parsing helpers."""
from __future__ import annotations

import json


def parse_bridges_arg(bridges_arg: str) -> list[str]:
    """Parse bridges from JSON array or single bridge string (backwards compat).

    Accepts:
        '["vmbr0","vmbr1"]'  →  ["vmbr0", "vmbr1"]
        'vmbr0'              →  ["vmbr0"]
    """
    try:
        bridges = json.loads(bridges_arg)
        if isinstance(bridges, str):
            return [bridges]
        return bridges
    except (json.JSONDecodeError, TypeError):
        return [bridges_arg]

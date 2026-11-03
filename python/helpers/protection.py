"""
Protected Stateful Tier — Layer 3 link-out guard (READ-ONLY).

Classifies a dependency's version change and decides, from a declarative policy
(config/protected_stateful.yml), whether Perimeter should present it as a normal
one-click link-out, a caution, or a blocked update requiring a friction-ful
override. Perimeter NEVER merges or writes GitHub — this only governs its own
surface. The real enforcement is GitHub branch protection (Layer 2).

Fail modes, deliberately asymmetric:
  * Config load fails / missing / empty  -> no policy -> everything ALLOWED
    (fail-OPEN: the feature simply does nothing, exactly as before it existed).
  * A target version is present but unparseable -> classified "major"
    (fail-SAFE: when we can't tell, assume the dangerous case so the guard trips).
  * No target version could be parsed at all -> "unknown" -> never blocked
    (there is nothing to act on; don't block every protected PR whose body table
    we failed to read a version from).
"""

from __future__ import annotations

import re

from axapi.utils import qlog_error
from config import cfg

COMPONENT = "PROTECT"

POLICY_PATH = cfg.ROOT_DIR / "config" / "protected_stateful.yml"

# Positional semver → change-type name. Index 0 = major, 1 = minor, 2+ = patch.
_POS = {0: "major", 1: "minor"}

_NUM_RE = re.compile(r"\d+(?:\.\d+)*")
_DIGEST_RE = re.compile(r"(?:sha256:|@sha)|^[0-9a-f]{40,64}$", re.IGNORECASE)

# mtime-keyed cache so a deployed config edit is picked up without code changes
# (and without re-parsing the file on every dependency of every PR).
_cache: dict = {"mtime": None, "rules": None}


def _digest_like(s: str) -> bool:
    return bool(s and _DIGEST_RE.search(s.strip().strip("`")))


def classify_change(current: str | None, new: str | None) -> str:
    """Classify ``current -> new`` as major|minor|patch|digest|unknown.

    Positional semver on the leading dotted-number run of each string (a `v`
    prefix and any suffix like ``-alpine`` are ignored). See module docstring
    for the fail-safe / fail-open split."""
    if not new:
        return "unknown"
    if _digest_like(new) or _digest_like(current or ""):
        return "digest"

    new_m = _NUM_RE.search(new)
    if not new_m:
        return "major"  # present but unparseable → fail-safe to the scary case
    cur_m = _NUM_RE.search(current or "")
    if not cur_m:
        return "major"  # no baseline to diff against → conservative

    cur_parts = [int(x) for x in cur_m.group(0).split(".")]
    new_parts = [int(x) for x in new_m.group(0).split(".")]
    for i in range(max(len(cur_parts), len(new_parts))):
        c = cur_parts[i] if i < len(cur_parts) else 0
        n = new_parts[i] if i < len(new_parts) else 0
        if n != c:
            return _POS.get(i, "patch")
    return "patch"  # identical → no-op, treat as the harmless case


def _parse_policy_file(path) -> list[dict]:
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        return []
    except Exception as e:  # noqa: BLE801 — any parse error → fail-open
        qlog_error(COMPONENT, f"protected_stateful policy load failed: {e}")
        return []

    if not isinstance(data, dict):
        return []
    raw = data.get("protected") or data.get("workloads") or []
    if not isinstance(raw, list):
        return []

    rules: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        match = str(item.get("match") or item.get("name") or "").strip().lower()
        if not match:
            continue
        block = item.get("block")
        warn = item.get("warn")
        rules.append({
            "match": match,
            "reason": str(item.get("reason") or "").strip(),
            "runbook": str(item.get("runbook") or "").strip(),
            "block": [str(x).strip().lower() for x in block] if isinstance(block, list) else ["major"],
            "warn": [str(x).strip().lower() for x in warn] if isinstance(warn, list) else [],
        })
    return rules


def load_policy() -> list[dict]:
    """Return the normalized rule list, cached on the config file's mtime.
    Any failure (missing/empty/malformed) returns [] → guard disabled."""
    try:
        mtime = POLICY_PATH.stat().st_mtime
    except OSError:
        _cache["mtime"], _cache["rules"] = None, []
        return []
    if _cache["rules"] is not None and _cache["mtime"] == mtime:
        return _cache["rules"]
    rules = _parse_policy_file(POLICY_PATH)
    _cache["mtime"], _cache["rules"] = mtime, rules
    return rules


def evaluate(name: str | None, current: str | None, new: str | None) -> dict:
    """Verdict for one dependency:
    ``{protected, change_type, action: block|warn|allow, reason, runbook, match}``.
    A non-protected dep (no matching rule) is always ``allow``."""
    change = classify_change(current, new)
    nm = (name or "").lower()
    for rule in load_policy():
        if rule["match"] and rule["match"] in nm:
            if change in rule["block"]:
                action = "block"
            elif change in rule["warn"]:
                action = "warn"
            else:
                action = "allow"
            return {"protected": True, "change_type": change, "action": action,
                    "reason": rule["reason"], "runbook": rule["runbook"], "match": rule["match"]}
    return {"protected": False, "change_type": change, "action": "allow",
            "reason": "", "runbook": "", "match": ""}

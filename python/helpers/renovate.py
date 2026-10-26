"""
Renovate observe client for Perimeter's Updates page (pod track).

READ-ONLY. Reads Renovate's open PRs and its dependency dashboard issue from the
GitOps repo via the GitHub API, with a READ-scoped token. Perimeter NEVER writes
the repo: it does not merge PRs, comment, or commit. A human merges; ArgoCD
applies. This module only surfaces what Renovate has proposed and deep-links to
it. Tier/label semantics come from the repo's renovate.json.
"""

from __future__ import annotations

import re
import time

import requests

from axapi.utils import qlog, qlog_error
from config import cfg

COMPONENT = "RENOVATE"
API = "https://api.github.com"
DASHBOARD_TITLE = "Renovate — homelab update dashboard"

# Labels we surface/color in the UI (from renovate.json packageRules).
TIER_LABELS = ("tier-a", "tier-b", "tier-c", "staged-manual", "major", "needs-review")

_cache: dict = {"ts": 0.0, "data": None}


class GithubError(RuntimeError):
    """GitHub API unreachable, unauthorized, or unexpected response."""


def is_configured() -> bool:
    return bool(cfg.GITHUB_TOKEN and cfg.GITHUB_REPO)


def _get(path: str, params: dict | None = None):
    r = requests.get(f"{API}{path}", headers=cfg.github_headers(),
                     params=params or {}, timeout=10)
    if r.status_code in (401, 403):
        raise GithubError(f"GitHub rejected the token ({r.status_code})")
    if r.status_code != 200:
        raise GithubError(f"GitHub returned HTTP {r.status_code}")
    return r.json()


def _tiers(labels: list[str]) -> list[str]:
    return [l for l in TIER_LABELS if l in labels]


def _author(obj: dict) -> str:
    return (obj.get("user") or {}).get("login", "").lower()


def _is_renovate(pr: dict) -> bool:
    """A PR is Renovate's if authored by the renovate bot/account or labelled
    'renovate'. Author is the reliable signal; the label may be absent."""
    if "renovate" in _author(pr):
        return True
    return any(l.get("name") == "renovate" for l in pr.get("labels", []))


_BACKTICK_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_SEP_CHARS = set("|-: ")


def _cell_name(cell: str) -> str:
    """Package column → a clean dependency name. Renovate wraps it as a markdown
    link (sometimes with a trailing ``([source](…))``); prefer the first link
    text, else the raw cell. Backticks around the name are stripped."""
    m = _LINK_RE.search(cell)
    name = m.group(1) if m else cell
    return name.strip().strip("`").strip()


def _parse_updates(body: str) -> list[dict]:
    """Parse the 'This PR contains the following updates' markdown table from a
    Renovate PR body into ``[{name, type, current, new, update}]``.

    Column order varies (Package / Type / Update / Change / Package file / …),
    so we key off the header row by name. Returns [] when no table is found, so
    the UI falls back to title-only rendering for odd/old bodies."""
    if not body:
        return []
    lines = body.splitlines()
    header_idx, headers = -1, []
    for i, ln in enumerate(lines):
        if "|" not in ln or "package" not in ln.lower():
            continue
        cells = [c.strip().lower() for c in ln.strip().strip("|").split("|")]
        # Next line must be the markdown separator row (---|---).
        if "package" in cells and i + 1 < len(lines) and set(lines[i + 1].strip()) <= _SEP_CHARS \
                and "-" in lines[i + 1]:
            header_idx, headers = i, cells
            break
    if header_idx < 0:
        return []

    pkg_i = headers.index("package")
    type_i = headers.index("type") if "type" in headers else -1
    update_i = headers.index("update") if "update" in headers else -1
    change_i = headers.index("change") if "change" in headers else -1

    updates: list[dict] = []
    for ln in lines[header_idx + 2:]:
        s = ln.strip()
        if not s.startswith("|"):
            break  # table ended
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) <= pkg_i:
            continue
        name = _cell_name(cells[pkg_i])
        if not name:
            continue
        current = new = None
        if 0 <= change_i < len(cells):
            vers = _BACKTICK_RE.findall(cells[change_i])
            if vers:
                new = vers[-1]
                if len(vers) >= 2:
                    current = vers[0]
        dep = {
            "name": name,
            "type": cells[type_i].strip("`").strip() if 0 <= type_i < len(cells) else "",
            "update": cells[update_i].strip("`").strip() if 0 <= update_i < len(cells) else "",
            "current": current,
            "new": new,
        }
        updates.append(dep)
    return updates


def _normalize_pr(pr: dict) -> dict:
    labels = [l.get("name", "") for l in pr.get("labels", [])]
    updates = _parse_updates(pr.get("body") or "")
    return {
        "number": pr.get("number"),
        "title": pr.get("title", ""),
        "url": pr.get("html_url", ""),
        "created_at": pr.get("created_at", ""),
        "draft": bool(pr.get("draft")),
        "tiers": _tiers(labels),
        "labels": labels,
        "updates": updates,
        "grouped": len(updates) > 1,
    }


def get_data(force: bool = False) -> dict:
    """
    Return {configured, ok, error, prs, dashboard} for the UI. Never writes.
    """
    if not is_configured():
        return {"configured": False, "ok": False, "error": None, "prs": [], "dashboard": None}

    now = time.time()
    if not force and _cache["data"] is not None and (now - _cache["ts"]) < cfg.GITHUB_CACHE_TTL:
        return _cache["data"]

    repo = cfg.GITHUB_REPO
    try:
        pulls = _get(f"/repos/{repo}/pulls", {"state": "open", "per_page": 100})
        # Detect Renovate PRs by bot AUTHOR (reliable) or the 'renovate' label
        # (only present when the repo's renovate config applies labels — the
        # label can be absent/lagging, so don't depend on it).
        prs = [_normalize_pr(p) for p in pulls if _is_renovate(p)]

        # Dependency dashboard: the bot-authored issue. Match the configured
        # title OR Renovate's default "Dependency Dashboard" (issues endpoint
        # also returns PRs, so skip anything with a pull_request key).
        issues = _get(f"/repos/{repo}/issues", {"state": "open", "per_page": 100})
        dashboard = None
        for it in issues:
            if "pull_request" in it:
                continue
            title = it.get("title", "").strip()
            if (_author(it).find("renovate") >= 0
                    and (title == DASHBOARD_TITLE or "dependency dashboard" in title.lower())):
                dashboard = {"number": it.get("number"), "url": it.get("html_url", ""),
                             "updated_at": it.get("updated_at", "")}
                break
    except GithubError as e:
        qlog_error(COMPONENT, f"GitHub read failed: {e}")
        return {"configured": True, "ok": False, "error": str(e), "prs": [], "dashboard": None}
    except requests.exceptions.RequestException as e:
        qlog_error(COMPONENT, f"GitHub unreachable: {e}")
        return {"configured": True, "ok": False, "error": f"GitHub unreachable: {e}",
                "prs": [], "dashboard": None}

    prs.sort(key=lambda p: p.get("created_at", ""), reverse=True)
    data = {"configured": True, "ok": True, "error": None, "prs": prs, "dashboard": dashboard}
    _cache["data"] = data
    _cache["ts"] = now
    qlog(COMPONENT, f"Fetched {len(prs)} Renovate PR(s); dashboard={'yes' if dashboard else 'no'}")
    return data

"""
Renovate observe client for Perimeter's Updates page (pod track).

READ-ONLY. Reads Renovate's open PRs and its dependency dashboard issue from the
GitOps repo via the GitHub API, with a READ-scoped token. Perimeter NEVER writes
the repo: it does not merge PRs, comment, or commit. A human merges; ArgoCD
applies. This module only surfaces what Renovate has proposed and deep-links to
it. Tier/label semantics come from the repo's renovate.json.
"""

from __future__ import annotations

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


def _normalize_pr(pr: dict) -> dict:
    labels = [l.get("name", "") for l in pr.get("labels", [])]
    return {
        "number": pr.get("number"),
        "title": pr.get("title", ""),
        "url": pr.get("html_url", ""),
        "created_at": pr.get("created_at", ""),
        "draft": bool(pr.get("draft")),
        "tiers": _tiers(labels),
        "labels": labels,
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
        prs = [_normalize_pr(p) for p in pulls
               if any(l.get("name") == "renovate" for l in p.get("labels", []))]

        # Dependency dashboard: find the open issue by title (issues endpoint
        # also returns PRs, so skip anything with a pull_request key).
        issues = _get(f"/repos/{repo}/issues", {"state": "open", "per_page": 100})
        dashboard = None
        for it in issues:
            if "pull_request" in it:
                continue
            if it.get("title", "").strip() == DASHBOARD_TITLE:
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

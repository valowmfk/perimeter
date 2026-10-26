"""
ArgoCD observe client for Perimeter's Updates page (pod track).

READ-ONLY BY DESIGN. ArgoCD is the single writer to the k8s cluster; Perimeter
only *observes* pod/app state here. This module therefore exposes get/list
operations ONLY — there is deliberately no sync(), no app create/update/delete,
and no kubeconfig. The scoped ARGOCD_AUTH_TOKEN should grant applications
get/list and nothing more. Triggering a sync is a later phase and, when added,
belongs in a separate, clearly-named write path — not here.
"""

from __future__ import annotations

import time

import requests
import urllib3

from axapi.utils import qlog, qlog_warning, qlog_error
from config import cfg

COMPONENT = "ARGOCD"

# Simple in-process TTL cache so repeated dashboard polls don't hammer the API.
_cache: dict = {"ts": 0.0, "apps": None}


class ArgocdError(RuntimeError):
    """ArgoCD API unreachable, unauthorized, or returned an unexpected body."""


def is_configured() -> bool:
    """True when both server URL and auth token are present."""
    return bool(cfg.argocd_base_url and cfg.ARGOCD_AUTH_TOKEN)


def _short(rev: str | None) -> str:
    """Truncate a git SHA for display; leave tags/branches intact."""
    if not rev:
        return ""
    return rev[:7] if len(rev) >= 40 and all(c in "0123456789abcdef" for c in rev.lower()) else rev


def _normalize(app: dict) -> dict:
    """Flatten an ArgoCD Application object into the fields the UI needs."""
    meta = app.get("metadata", {}) or {}
    spec = app.get("spec", {}) or {}
    status = app.get("status", {}) or {}
    sync = status.get("sync", {}) or {}
    health = status.get("health", {}) or {}
    source = spec.get("source", {}) or {}
    op_state = status.get("operationState", {}) or {}

    sync_status = sync.get("status", "Unknown")
    return {
        "name": meta.get("name", "?"),
        "project": spec.get("project", ""),
        "sync_status": sync_status,
        "health_status": health.get("status", "Unknown"),
        "health_message": health.get("message", ""),
        "out_of_sync": sync_status != "Synced",
        "revision_current": _short(sync.get("revision")),
        "revision_target": source.get("targetRevision", ""),
        "repo": source.get("repoURL", ""),
        "path": source.get("path", ""),
        "operation_phase": op_state.get("phase", ""),
    }


def list_applications(force: bool = False) -> list[dict]:
    """
    Return normalized ArgoCD applications (read-only), TTL-cached.

    Raises ArgocdError on unconfigured/unreachable/unauthorized so the caller
    can surface a clear banner. Never mutates cluster state.
    """
    if not is_configured():
        raise ArgocdError("ArgoCD is not configured (ARGOCD_SERVER / ARGOCD_AUTH_TOKEN unset)")

    now = time.time()
    if not force and _cache["apps"] is not None and (now - _cache["ts"]) < cfg.ARGOCD_CACHE_TTL:
        return _cache["apps"]

    url = f"{cfg.argocd_base_url}/applications"
    try:
        if not cfg.ARGOCD_VERIFY_SSL:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        r = requests.get(
            url,
            headers=cfg.argocd_headers(),
            verify=cfg.ARGOCD_VERIFY_SSL,
            timeout=10,
        )
    except requests.exceptions.SSLError as e:
        qlog_error(COMPONENT, f"TLS verification failed for {url}: {e}")
        raise ArgocdError(f"ArgoCD TLS verification failed — set ARGOCD_VERIFY_SSL=0 if intended ({e})")
    except requests.exceptions.RequestException as e:
        qlog_error(COMPONENT, f"ArgoCD request failed: {e}")
        raise ArgocdError(f"ArgoCD unreachable: {e}")

    if r.status_code in (401, 403):
        qlog_error(COMPONENT, f"ArgoCD auth rejected ({r.status_code}) — token scope/validity")
        raise ArgocdError(f"ArgoCD rejected the token ({r.status_code})")
    if r.status_code != 200:
        qlog_error(COMPONENT, f"ArgoCD unexpected status {r.status_code}: {r.text[:200]}")
        raise ArgocdError(f"ArgoCD returned HTTP {r.status_code}")

    try:
        items = (r.json() or {}).get("items") or []
    except ValueError as e:
        raise ArgocdError(f"ArgoCD returned non-JSON body: {e}")

    apps = sorted((_normalize(a) for a in items), key=lambda a: a["name"])
    _cache["apps"] = apps
    _cache["ts"] = now
    qlog(COMPONENT, f"Fetched {len(apps)} ArgoCD applications")
    return apps


def summarize(apps: list[dict]) -> dict:
    """Roll up counts for the dashboard header."""
    total = len(apps)
    out_of_sync = sum(1 for a in apps if a["out_of_sync"])
    unhealthy = sum(1 for a in apps if a["health_status"] not in ("Healthy", "Unknown", ""))
    return {"total": total, "out_of_sync": out_of_sync, "unhealthy": unhealthy}


def sync(app_name: str, dry_run: bool = False, prune: bool = False) -> dict:
    """
    TRIGGER ArgoCD to reconcile an application (git → cluster).

    This is the ONLY mutating call in this module. It does NOT write the cluster
    directly — it asks ArgoCD (the single writer) to sync the app to its git
    source, which is the sanctioned way to trigger a native tool. Requires the
    'applications, sync' RBAC grant on the scoped token. Perimeter never bumps
    images or commits — that path stays git → Renovate PR → human merge → ArgoCD.
    """
    if not is_configured():
        raise ArgocdError("ArgoCD is not configured")

    url = f"{cfg.argocd_base_url}/applications/{app_name}/sync"
    try:
        if not cfg.ARGOCD_VERIFY_SSL:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        r = requests.post(url, headers=cfg.argocd_headers(),
                          json={"dryRun": bool(dry_run), "prune": bool(prune)},
                          verify=cfg.ARGOCD_VERIFY_SSL, timeout=30)
    except requests.exceptions.RequestException as e:
        qlog_error(COMPONENT, f"ArgoCD sync request failed: {e}")
        raise ArgocdError(f"ArgoCD unreachable: {e}")

    if r.status_code in (401, 403):
        raise ArgocdError(f"ArgoCD rejected sync ({r.status_code}) — token RBAC may lack 'applications, sync'")
    if r.status_code != 200:
        raise ArgocdError(f"ArgoCD sync returned HTTP {r.status_code}: {r.text[:200]}")

    _cache["apps"] = None  # force a fresh list so the UI reflects the new op
    qlog(COMPONENT, f"Triggered ArgoCD sync for {app_name} (dry_run={dry_run})")
    return {"app": app_name, "dry_run": bool(dry_run), "started": True}

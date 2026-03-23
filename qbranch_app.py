"""Perimeter Automation Platform — Flask application entry point."""

import os
import sys
import time

from flask import Flask, g
import urllib3

# Add python directory to path for AXAPI client and helpers
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'python'))

# Suppress SSL warnings for internal homelab use (behind Traefik reverse proxy)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import cfg
from utils.qlog import setup_logging, new_correlation_id, get_correlation_id
from utils.metrics import HTTP_REQUESTS, HTTP_DURATION
from routes.shared import JOB_STATUS, cert_sessions, COMPLETED_TTL

# Initialize structured logging early
setup_logging(
    log_dir=cfg.ROOT_DIR / "logs",
    log_file=cfg.LOG_FILE,
    max_bytes=cfg.LOG_MAX_BYTES,
    backup_count=cfg.LOG_BACKUP_COUNT,
    level=cfg.LOG_LEVEL,
)


def create_app() -> Flask:
    """Application factory."""
    app = Flask(
        __name__,
        static_folder=str(cfg.WEB_DIR / "static"),
        template_folder=str(cfg.WEB_DIR / "templates"),
    )

    # ── Correlation ID hook ─────────────────────────────────
    @app.before_request
    def _set_correlation_id():
        cid = new_correlation_id()
        g.correlation_id = cid

    @app.after_request
    def _add_correlation_header(response):
        cid = getattr(g, "correlation_id", "")
        if cid:
            response.headers["X-Correlation-ID"] = cid
        return response

    # ── TTL cleanup hook ────────────────────────────────────
    @app.before_request
    def _prune_stale_entries():
        now = time.time()
        cutoff = now - COMPLETED_TTL

        stale_jobs = [
            jid for jid, j in JOB_STATUS.items()
            if j.get("_finished_at", 0) and j["_finished_at"] < cutoff
        ]
        for jid in stale_jobs:
            del JOB_STATUS[jid]

        stale_certs = [
            sid for sid, s in cert_sessions.items()
            if s.get("status") in ("completed", "failed", "error")
            and s.get("start_time", 0) < cutoff
        ]
        for sid in stale_certs:
            del cert_sessions[sid]

    # ── Prometheus metrics hooks ────────────────────────────
    @app.before_request
    def _start_timer():
        g.start_time = time.time()

    @app.after_request
    def _record_metrics(response):
        if hasattr(g, "start_time"):
            duration = time.time() - g.start_time
            from flask import request
            endpoint = request.endpoint or "unknown"
            # Skip metrics endpoint itself to avoid self-referential noise
            if endpoint != "metrics":
                HTTP_REQUESTS.labels(
                    method=request.method,
                    endpoint=endpoint,
                    status=response.status_code,
                ).inc()
                HTTP_DURATION.labels(
                    method=request.method,
                    endpoint=endpoint,
                ).observe(duration)
        return response

    @app.route("/metrics")
    def metrics():
        from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
        from flask import Response
        return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

    # ── Register blueprints (conditional on feature flags) ──
    from routes.core_bp import core_bp
    from routes.vms_bp import vms_bp
    from routes.network_bp import network_bp

    app.register_blueprint(core_bp)       # Always — serves the UI
    app.register_blueprint(vms_bp)        # Always — core VM operations
    app.register_blueprint(network_bp)    # Always — subnets API

    if cfg.FEATURES.get("ansible", True):
        from routes.playbooks_bp import playbooks_bp
        app.register_blueprint(playbooks_bp)

    if cfg.FEATURES.get("certificates", True):
        from routes.certificates_bp import certificates_bp
        app.register_blueprint(certificates_bp)

    if cfg.FEATURES.get("vthunder", True):
        from routes.system_bp import system_bp
        app.register_blueprint(system_bp)

    return app


# ── Direct execution ────────────────────────────────────────
app = create_app()

if __name__ == "__main__":
    app.run(host=cfg.FLASK_HOST, port=cfg.FLASK_PORT)

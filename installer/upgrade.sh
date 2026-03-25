#!/usr/bin/env bash
# ============================================================
# Perimeter Upgrade Script
# Usage: sudo bash installer/upgrade.sh
#
# Pulls latest code, updates dependencies, and restarts services.
# Safe to run multiple times — idempotent.
# ============================================================

set -euo pipefail

# ── Colors ──────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✔${NC} $1"; }
info() { echo -e "  ${CYAN}ℹ${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
fail() { echo -e "  ${RED}✖${NC} $1"; }

# ── Detect install directory ────────────────────────────────
INSTALL_DIR="${PERIMETER_ROOT:-/opt/perimeter}"

if [[ ! -f "$INSTALL_DIR/perimeter_app.py" ]]; then
    fail "Perimeter not found at $INSTALL_DIR"
    echo "  Set PERIMETER_ROOT if installed elsewhere."
    exit 1
fi

cd "$INSTALL_DIR"

# ── Read current version ────────────────────────────────────
OLD_VERSION="unknown"
if [[ -f VERSION ]]; then
    OLD_VERSION=$(cat VERSION)
fi

echo ""
echo -e "${BOLD}${CYAN}╔════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║        PERIMETER UPGRADE                       ║${NC}"
echo -e "${BOLD}${CYAN}║        Current: v${OLD_VERSION}                          ║${NC}"
echo -e "${BOLD}${CYAN}╚════════════════════════════════════════════════╝${NC}"
echo ""

# ── Step 1: Pull latest code ────────────────────────────────
info "Pulling latest code..."
if git rev-parse --is-inside-work-tree > /dev/null 2>&1; then
    git pull --ff-only
    ok "Code updated"
else
    fail "Not a git repository. Clone fresh or use git pull manually."
    exit 1
fi

# ── Read new version ────────────────────────────────────────
NEW_VERSION="unknown"
if [[ -f VERSION ]]; then
    NEW_VERSION=$(cat VERSION)
fi
info "Upgrading: v${OLD_VERSION} → v${NEW_VERSION}"

# ── Step 2: Update Python dependencies ──────────────────────
info "Updating Python dependencies..."
if [[ -d "$INSTALL_DIR/venv" ]]; then
    "$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt" 2>/dev/null
    ok "Dependencies updated (venv)"
elif command -v pip3 &> /dev/null; then
    pip3 install -q -r "$INSTALL_DIR/requirements.txt" 2>/dev/null
    ok "Dependencies updated (system pip)"
else
    warn "pip3 not found — skip dependency update"
fi

# ── Step 3: Initialize Terraform providers ──────────────────
info "Initializing Terraform providers..."
for ws in linux_vm vthunder_vm vyos_vm; do
    ws_dir="$INSTALL_DIR/terraform/$ws"
    if [[ -f "$ws_dir/main.tf" ]]; then
        (cd "$ws_dir" && terraform init -upgrade -input=false > /dev/null 2>&1) && ok "  $ws" || warn "  $ws — init failed (non-fatal)"
    fi
done

# ── Step 4: Check for missing worker service ─────────────────
if [[ ! -f /etc/systemd/system/perimeter-worker.service ]]; then
    warn "perimeter-worker.service not found"
    info "Re-run the setup wizard to generate it:"
    echo "    sudo python3 $INSTALL_DIR/installer/setup.py --install-dir $INSTALL_DIR"
    echo ""
    info "Or create it manually — see installer/templates/worker-service.j2"
fi

# ── Step 5: Restart services ────────────────────────────────
info "Restarting services..."
systemctl daemon-reload

if systemctl is-enabled perimeter > /dev/null 2>&1; then
    systemctl restart perimeter
    ok "perimeter.service restarted"
else
    warn "perimeter.service not enabled"
fi

if systemctl is-enabled perimeter-worker > /dev/null 2>&1; then
    systemctl restart perimeter-worker
    ok "perimeter-worker.service restarted"
else
    info "perimeter-worker.service not enabled (optional)"
fi

# ── Step 6: Health check ────────────────────────────────────
sleep 3
FLASK_PORT=$(grep -oP 'FLASK_PORT=\K\d+' /etc/systemd/system/perimeter.service 2>/dev/null || echo "8080")

if curl -sf "http://localhost:${FLASK_PORT}/api/version" > /dev/null 2>&1; then
    RUNNING_VER=$(curl -sf "http://localhost:${FLASK_PORT}/api/version" | python3 -c "import sys,json; print(json.load(sys.stdin).get('version','?'))" 2>/dev/null || echo "?")
    ok "Perimeter is running — v${RUNNING_VER}"
else
    warn "Health check failed — check: journalctl -u perimeter -f"
fi

# ── Done ────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}Upgrade complete: v${OLD_VERSION} → v${NEW_VERSION}${NC}"
echo ""
echo "  Useful commands:"
echo "    Logs:      journalctl -u perimeter -f"
echo "    Status:    systemctl status perimeter"
echo "    Version:   curl -s http://localhost:${FLASK_PORT}/api/version | python3 -m json.tool"
echo ""

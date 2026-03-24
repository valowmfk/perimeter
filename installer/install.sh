#!/usr/bin/env bash
# ================================================
# Perimeter Automation Platform — Installer
# Bootstrap script: installs system dependencies,
# then hands off to the Python interactive setup.
# ================================================
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/perimeter}"
PERIMETER_VERSION="3.0"
SOPS_VERSION="3.11.0"
AGE_VERSION="1.3.1"
INSTALLER_LOG="/tmp/perimeter-install.log"

# ── Colors ────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

ok()   { printf "  ${GREEN}✓${NC} %s\n" "$1"; }
fail() { printf "  ${RED}✖${NC} %s\n" "$1"; }
info() { printf "  ${CYAN}→${NC} %s\n" "$1"; }
warn() { printf "  ${YELLOW}⚠${NC} %s\n" "$1"; }

header() {
    echo ""
    printf "${BOLD}${CYAN}%s${NC}\n" "$1"
    printf "${CYAN}%s${NC}\n" "$(printf '─%.0s' $(seq 1 ${#1}))"
}

# Run a command quietly — show output only on failure
run_quiet() {
    local logfile="$INSTALLER_LOG"
    if ! "$@" >> "$logfile" 2>&1; then
        echo ""
        fail "Command failed: $*"
        echo "--- Last 20 lines of output ---"
        tail -20 "$logfile"
        echo "--- Full log: $logfile ---"
        exit 1
    fi
}

# ── Error trap ────────────────────────────────
cleanup_on_error() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        echo ""
        fail "Install failed (exit code $exit_code)"
        if [[ -f "$INSTALLER_LOG" ]]; then
            echo "--- Last 20 lines of log ---"
            tail -20 "$INSTALLER_LOG"
        fi
        echo ""
        info "Full log: $INSTALLER_LOG"
        info "Please include this log when reporting issues."
    fi
}
trap cleanup_on_error EXIT

# Clear log
true > "$INSTALLER_LOG"

# ── Pre-flight ────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    printf '%b%s%b\n' "$RED" "Error: This script must be run as root (or with sudo)." "$NC"
    exit 1
fi

echo ""
echo "================================================"
echo "   PERIMETER v${PERIMETER_VERSION} — BOOTSTRAP INSTALLER"
echo "   Automation Platform for Labs"
echo "================================================"
echo ""

# ── Detect OS ─────────────────────────────────
header "Detecting Operating System"

if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    OS_ID="${ID}"
    OS_NAME="${PRETTY_NAME}"
    OS_CODENAME="${VERSION_CODENAME:-}"
else
    fail "Cannot detect OS — /etc/os-release not found"
    exit 1
fi

case "$OS_ID" in
    rocky|rhel|alma|centos)
        OS_FAMILY="rhel"
        ;;
    ubuntu|debian)
        OS_FAMILY="debian"
        ;;
    *)
        fail "Unsupported OS: $OS_ID. Perimeter supports Rocky/RHEL/Alma/CentOS and Ubuntu/Debian."
        exit 1
        ;;
esac

ok "Detected: $OS_NAME ($OS_FAMILY family)"

# ── Detect pip flags ──────────────────────────
# PEP 668 (Ubuntu 23.04+, Debian 12+) blocks system-wide pip installs
PIP_EXTRA_FLAGS=""
if [[ "$OS_FAMILY" == "debian" ]]; then
    if python3 -c "import sysconfig; marker=sysconfig.get_path('stdlib') + '/EXTERNALLY-MANAGED'; import os; exit(0 if os.path.exists(marker) else 1)" 2>/dev/null; then
        PIP_EXTRA_FLAGS="--break-system-packages"
        info "PEP 668 detected — using --break-system-packages for pip"
    fi
fi

# ── Check existing installation ───────────────
if [[ -d "$INSTALL_DIR" && -f "$INSTALL_DIR/qbranch_app.py" ]]; then
    warn "Existing installation found at $INSTALL_DIR"
    read -p "  Overwrite? [y/N]: " -n 1 -r < /dev/tty
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 0
    fi
fi

# ── System packages ──────────────────────────
header "Installing System Packages"

export DEBIAN_FRONTEND=noninteractive

if [[ "$OS_FAMILY" == "rhel" ]]; then
    info "Installing via dnf..."
    run_quiet dnf install -y python3 python3-pip python3-devel git curl unzip jq openssh-clients
    ok "System packages installed (dnf)"
else
    # Remove any broken third-party repos (e.g. HashiCorp with unsupported codename)
    if [[ -f /etc/apt/sources.list.d/hashicorp.list ]]; then
        rm -f /etc/apt/sources.list.d/hashicorp.list
        info "Removed stale HashiCorp apt repo (will re-add with correct codename)"
    fi
    info "Updating apt package index..."
    run_quiet apt-get update -qq
    info "Installing packages via apt..."
    run_quiet apt-get install -y -qq python3 python3-pip python3-venv python3-full \
        git curl unzip jq openssh-client lsb-release gnupg software-properties-common
    ok "System packages installed (apt)"
fi

# ── Terraform ─────────────────────────────────
header "Installing Terraform"

if command -v terraform &> /dev/null; then
    TF_VER=$(terraform version -json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['terraform_version'])" 2>/dev/null || terraform version | head -1 | awk '{print $2}')
    ok "Terraform already installed: $TF_VER"
else
    info "Adding HashiCorp repository..."
    if [[ "$OS_FAMILY" == "rhel" ]]; then
        run_quiet dnf install -y yum-utils
        run_quiet yum-config-manager --add-repo https://rpm.releases.hashicorp.com/RHEL/hashicorp.repo
        run_quiet dnf install -y terraform
    else
        # Get codename from os-release, fall back to lsb_release
        CODENAME="${OS_CODENAME}"
        if [[ -z "$CODENAME" ]] && command -v lsb_release &>/dev/null; then
            CODENAME="$(lsb_release -cs)"
        fi
        if [[ -z "$CODENAME" ]]; then
            CODENAME="noble"
        fi

        curl -fsSL https://apt.releases.hashicorp.com/gpg | gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg 2>/dev/null

        # Try the detected codename first, fall back to noble (latest LTS) if HashiCorp doesn't support it
        echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com ${CODENAME} main" \
            > /etc/apt/sources.list.d/hashicorp.list
        if ! apt-get update -qq >> "$INSTALLER_LOG" 2>&1; then
            warn "HashiCorp repo doesn't support '${CODENAME}' — using 'noble' (Ubuntu 24.04 LTS)"
            CODENAME="noble"
            echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com ${CODENAME} main" \
                > /etc/apt/sources.list.d/hashicorp.list
            run_quiet apt-get update -qq
        fi
        run_quiet apt-get install -y terraform
    fi
    ok "Terraform installed: $(terraform version | head -1 | awk '{print $2}')"
fi

# ── SOPS ──────────────────────────────────────
header "Installing SOPS + Age"

if command -v sops &> /dev/null; then
    ok "SOPS already installed: $(sops --version 2>&1 | head -1)"
else
    info "Downloading SOPS v${SOPS_VERSION}..."
    run_quiet curl -sLo /usr/local/bin/sops \
        "https://github.com/getsops/sops/releases/download/v${SOPS_VERSION}/sops-v${SOPS_VERSION}.linux.amd64"
    chmod +x /usr/local/bin/sops
    ok "SOPS installed: v${SOPS_VERSION}"
fi

if command -v age &> /dev/null; then
    ok "Age already installed: $(age --version 2>&1)"
else
    info "Downloading Age v${AGE_VERSION}..."
    run_quiet curl -sLo /tmp/age.tar.gz \
        "https://github.com/FiloSottile/age/releases/download/v${AGE_VERSION}/age-v${AGE_VERSION}-linux-amd64.tar.gz"
    tar xf /tmp/age.tar.gz -C /tmp
    cp /tmp/age/age /usr/local/bin/age
    cp /tmp/age/age-keygen /usr/local/bin/age-keygen
    chmod +x /usr/local/bin/age /usr/local/bin/age-keygen
    rm -rf /tmp/age /tmp/age.tar.gz
    ok "Age installed: v${AGE_VERSION}"
fi

# ── Ansible ───────────────────────────────────
header "Installing Ansible"

if command -v ansible-playbook &> /dev/null; then
    ok "Ansible already installed: $(ansible --version | head -1)"
else
    info "Installing Ansible via pip..."
    run_quiet pip3 install $PIP_EXTRA_FLAGS ansible
    # Ensure pip-installed binaries are in PATH
    export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"
    ok "Ansible installed: $(ansible --version 2>/dev/null | head -1 || echo 'installed')"
fi

# Ensure all installed binaries are findable for the Python setup
export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"

# ── Docker (optional — checked during Python setup) ──
header "Checking Docker"

if command -v docker &> /dev/null; then
    ok "Docker available: $(docker --version)"
else
    warn "Docker not installed — Certificate Management feature requires Docker"
    info "Install later with: https://docs.docker.com/engine/install/"
fi

# ── Clone / Update Repository ────────────────
header "Setting Up Perimeter"

if [[ -d "$INSTALL_DIR/.git" ]]; then
    info "Updating existing installation..."
    cd "$INSTALL_DIR"
    git pull --ff-only 2>/dev/null || warn "Could not auto-update (manual merge may be needed)"
    ok "Repository updated"
else
    info "Cloning Perimeter to $INSTALL_DIR..."
    git clone https://github.com/valowmfk/perimeter.git "$INSTALL_DIR" 2>/dev/null || {
        # If repo doesn't exist yet (pre-release), just use current directory
        if [[ -f "$(dirname "$0")/../qbranch_app.py" ]]; then
            INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
            info "Using local installation at $INSTALL_DIR"
        else
            fail "Could not clone repository"
            exit 1
        fi
    }
    ok "Perimeter installed to $INSTALL_DIR"
fi

# ── Python dependencies ──────────────────────
header "Installing Python Dependencies"

cd "$INSTALL_DIR"
run_quiet pip3 install $PIP_EXTRA_FLAGS -r requirements.txt
ok "Python packages installed"

# ── Hand off to Python setup ─────────────────
echo ""
printf '%b%bBootstrap complete. Starting interactive setup...%b\n' "$BOLD" "$CYAN" "$NC"
echo ""

# Clear the error trap — Python setup handles its own errors
trap - EXIT

# Re-open stdin from terminal (stdin is consumed by curl pipe)
python3 "$INSTALL_DIR/installer/setup.py" --install-dir "$INSTALL_DIR" < /dev/tty

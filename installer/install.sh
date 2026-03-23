#!/usr/bin/env bash
# ═══════════════════════════════════════════════
# Perimeter Automation Platform — Installer
# Bootstrap script: installs system dependencies,
# then hands off to the Python interactive setup.
# ═══════════════════════════════════════════════
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/perimeter}"
PERIMETER_VERSION="3.0"
SOPS_VERSION="3.11.0"
AGE_VERSION="1.3.1"

# ── Colors ────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✖${NC} $1"; }
info() { echo -e "  ${CYAN}→${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }

header() {
    echo ""
    echo -e "${BOLD}${CYAN}$1${NC}"
    echo -e "${CYAN}$(printf '─%.0s' $(seq 1 ${#1}))${NC}"
}

# ── Pre-flight ────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}Error: This script must be run as root (or with sudo).${NC}"
    exit 1
fi

echo ""
echo -e "${BOLD}${CYAN}╔════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║    PERIMETER v${PERIMETER_VERSION} — BOOTSTRAP INSTALLER       ║${NC}"
echo -e "${BOLD}${CYAN}║      Automation Platform for Labs             ║${NC}"
echo -e "${BOLD}${CYAN}╚════════════════════════════════════════════════╝${NC}"
echo ""

# ── Detect OS ─────────────────────────────────
header "Detecting Operating System"

if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    OS_ID="${ID}"
    OS_VERSION="${VERSION_ID}"
    OS_NAME="${PRETTY_NAME}"
else
    fail "Cannot detect OS — /etc/os-release not found"
    exit 1
fi

case "$OS_ID" in
    rocky|rhel|alma|centos)
        OS_FAMILY="rhel"
        PKG="dnf"
        ;;
    ubuntu|debian)
        OS_FAMILY="debian"
        PKG="apt-get"
        ;;
    *)
        fail "Unsupported OS: $OS_ID. Perimeter supports Rocky/RHEL/Alma/CentOS and Ubuntu/Debian."
        exit 1
        ;;
esac

ok "Detected: $OS_NAME ($OS_FAMILY family)"

# ── Check existing installation ───────────────
if [[ -d "$INSTALL_DIR" && -f "$INSTALL_DIR/qbranch_app.py" ]]; then
    warn "Existing installation found at $INSTALL_DIR"
    read -p "  Overwrite? [y/N]: " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 0
    fi
fi

# ── System packages ──────────────────────────
header "Installing System Packages"

if [[ "$OS_FAMILY" == "rhel" ]]; then
    info "Installing via dnf..."
    dnf install -y python3 python3-pip python3-devel git curl unzip jq openssh-clients \
        > /dev/null 2>&1
    ok "System packages installed (dnf)"
else
    info "Installing via apt..."
    apt-get update -qq > /dev/null 2>&1
    apt-get install -y python3 python3-pip python3-venv git curl unzip jq openssh-client \
        > /dev/null 2>&1
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
        dnf install -y yum-utils > /dev/null 2>&1
        yum-config-manager --add-repo https://rpm.releases.hashicorp.com/RHEL/hashicorp.repo > /dev/null 2>&1
        dnf install -y terraform > /dev/null 2>&1
    else
        curl -fsSL https://apt.releases.hashicorp.com/gpg | gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg 2>/dev/null
        echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" \
            > /etc/apt/sources.list.d/hashicorp.list
        apt-get update -qq > /dev/null 2>&1
        apt-get install -y terraform > /dev/null 2>&1
    fi
    ok "Terraform installed: $(terraform version | head -1 | awk '{print $2}')"
fi

# ── SOPS ──────────────────────────────────────
header "Installing SOPS + Age"

if command -v sops &> /dev/null; then
    ok "SOPS already installed: $(sops --version 2>&1 | head -1)"
else
    info "Downloading SOPS v${SOPS_VERSION}..."
    curl -sLo /usr/local/bin/sops \
        "https://github.com/getsops/sops/releases/download/v${SOPS_VERSION}/sops-v${SOPS_VERSION}.linux.amd64"
    chmod +x /usr/local/bin/sops
    ok "SOPS installed: v${SOPS_VERSION}"
fi

if command -v age &> /dev/null; then
    ok "Age already installed: $(age --version 2>&1)"
else
    info "Downloading Age v${AGE_VERSION}..."
    curl -sLo /tmp/age.tar.gz \
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
    pip3 install ansible > /dev/null 2>&1
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
pip3 install -r requirements.txt > /dev/null 2>&1
ok "Python packages installed"

# ── Hand off to Python setup ─────────────────
echo ""
echo -e "${BOLD}${CYAN}Bootstrap complete. Starting interactive setup...${NC}"
echo ""

# Re-open stdin from terminal (stdin is consumed by curl pipe)
python3 "$INSTALL_DIR/installer/setup.py" --install-dir "$INSTALL_DIR" < /dev/tty

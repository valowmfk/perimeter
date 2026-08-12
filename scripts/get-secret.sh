#!/bin/bash
# Decrypt a single secret from the SOPS-encrypted env file.
# Usage: get-secret.sh KEY_NAME

set -euo pipefail

INSTALL_DIR="${PERIMETER_ROOT:-/opt/perimeter}"
ENCRYPTED_ENV="${INSTALL_DIR}/secrets/perimeter.enc.env"

KEY="${1:?Usage: get-secret.sh KEY_NAME}"

# Resolve the sops binary. systemd — and therefore Ansible launched by the
# perimeter service — does not put ~/.local/bin on PATH, so a bare
# `command -v sops` fails there and this script would wrongly report "not
# installed". Check the known install locations first, mirroring
# python/utils/sops_env.py:_find_sops().
resolve_sops() {
    local candidate
    for candidate in "${HOME:-/root}/.local/bin/sops" /usr/local/bin/sops; do
        if [[ -x "$candidate" ]]; then
            echo "$candidate"
            return 0
        fi
    done
    command -v sops 2>/dev/null && return 0
    return 1
}

if ! SOPS_BIN="$(resolve_sops)"; then
    echo "ERROR: sops binary not found (checked ~/.local/bin, /usr/local/bin, PATH)" >&2
    exit 1
fi

if [[ ! -f "$ENCRYPTED_ENV" ]]; then
    echo "ERROR: SOPS encrypted env file not found: $ENCRYPTED_ENV" >&2
    exit 1
fi

"$SOPS_BIN" -d "$ENCRYPTED_ENV" 2>/dev/null | grep "^${KEY}=" | head -1 | cut -d= -f2- | sed 's/^["'\'']*//;s/["'\'']*$//'

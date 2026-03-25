#!/bin/bash
# Decrypt a single secret from the SOPS-encrypted env file.
# Usage: get-secret.sh KEY_NAME

set -euo pipefail

INSTALL_DIR="${PERIMETER_ROOT:-/opt/perimeter}"
ENCRYPTED_ENV="${INSTALL_DIR}/secrets/perimeter.enc.env"

KEY="${1:?Usage: get-secret.sh KEY_NAME}"

if [[ -f "$ENCRYPTED_ENV" ]] && command -v sops &>/dev/null; then
    sops -d "$ENCRYPTED_ENV" 2>/dev/null | grep "^${KEY}=" | head -1 | cut -d= -f2- | sed 's/^["'\'']*//;s/["'\'']*$//'
    exit 0
fi

echo "ERROR: SOPS encrypted env file not found or sops not installed" >&2
exit 1

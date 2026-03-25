#!/bin/bash
# Certbot deploy hook — fix ownership after renewal
# Ensures the Perimeter service user can read cert/key files

INSTALL_DIR="${PERIMETER_ROOT:-/opt/perimeter}"
CERT_DIR="${INSTALL_DIR}/certificates"
OWNER="${PERIMETER_USER:-perimeter}:${PERIMETER_USER:-perimeter}"

chown -R "$OWNER" "$CERT_DIR"

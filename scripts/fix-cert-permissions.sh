#!/bin/bash
# Certbot deploy hook — fix ownership after renewal
# Ensures the Perimeter service (mklouda) can read cert/key files
#
# Install as deploy hook:
#   ln -sf /home/mklouda/automation-demo/scripts/fix-cert-permissions.sh \
#          /home/mklouda/automation-demo/certificates/klouda.work/renewal-hooks/deploy/fix-permissions.sh

CERT_DIR="/home/mklouda/automation-demo/certificates"
OWNER="mklouda:mklouda"

chown -R "$OWNER" "$CERT_DIR"

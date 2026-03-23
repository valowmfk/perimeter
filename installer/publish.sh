#!/usr/bin/env bash
# Publish Perimeter to the public repository.
# Usage: bash installer/publish.sh

set -euo pipefail

SRC="/home/mklouda/automation-demo"
DEST="/tmp/perimeter-publish"
REPO="git@github.com:valowmfk/perimeter.git"

echo "Publishing Perimeter to public repo..."

# Clean destination
rm -rf "$DEST"

# Clone the public repo
git clone "$REPO" "$DEST"

# Copy codebase, excluding private/generated files
rsync -av --delete \
  --exclude='.git' \
  --exclude='secrets/' \
  --exclude='*.tfstate*' \
  --exclude='.terraform/' \
  --exclude='.terraform.lock.hcl' \
  --exclude='__pycache__/' \
  --exclude='tasks/' \
  --exclude='CLAUDE.md' \
  --exclude='vm_track.json' \
  --exclude='template_refresh.json' \
  --exclude='data/' \
  --exclude='logs/' \
  --exclude='credentials.auto.tfvars' \
  --exclude='.sops.yaml' \
  --exclude='certificates/' \
  --exclude='*.auto.tfvars.json' \
  --exclude='inventories/inventory.yml' \
  --exclude='version2.0.md' \
  --exclude='perimeter-test.service' \
  --exclude='installer/publish.sh' \
  --exclude='installer/public-gitignore' \
  --exclude='installer/README.md' \
  "$SRC/" "$DEST/"

# Use the public gitignore
cp "$SRC/installer/public-gitignore" "$DEST/.gitignore"

# Use the public README
cp "$SRC/installer/README.md" "$DEST/README.md"

# Create empty placeholder files that the installer expects
mkdir -p "$DEST/secrets" "$DEST/logs" "$DEST/data" "$DEST/inventories"
touch "$DEST/secrets/.gitkeep"
touch "$DEST/logs/.gitkeep"
touch "$DEST/data/.gitkeep"

# Create empty tfvars for each workspace
for ws in linux_vm vthunder_vm vyos_vm; do
  key="vm_configs"
  [[ "$ws" == "vthunder_vm" ]] && key="vthunder_configs"
  [[ "$ws" == "vyos_vm" ]] && key="vyos_configs"
  ws_name="${ws/_vm/}"
  echo "{\"${key}\": {}}" > "$DEST/terraform/${ws}/perimeter-${ws_name}.auto.tfvars.json"
done

# Create empty inventory template
cat > "$DEST/inventories/inventory.yml.example" << 'EOF'
# Example inventory — the installer generates this automatically.
# See installer/templates/inventory.j2 for the full template.
all:
  hosts:
    localhost:
      ansible_connection: local
EOF

cd "$DEST"
git add -A
git status

echo ""
echo "Review the changes above, then:"
echo "  cd $DEST"
echo "  git commit -m 'Perimeter v3.0 — initial public release'"
echo "  git push"

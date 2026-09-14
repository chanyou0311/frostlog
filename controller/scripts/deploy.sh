#!/usr/bin/env bash
# Copy this unit to the Pi and run the installer there.
# Usage: controller/scripts/deploy.sh [user@host]   (default: chanyou@192.168.100.40)
#
# Only this directory goes: what lands in ~/frostlog-controller/ is the layout
# the systemd units and the installer expect.
set -euo pipefail
target="${1:-chanyou@192.168.100.40}"
cd "$(dirname "$0")/.."
rsync -az --delete \
  --exclude .venv --exclude __pycache__ --exclude .pytest_cache --exclude .ruff_cache \
  ./ "$target:frostlog-controller/"
ssh "$target" 'bash frostlog-controller/scripts/install.sh'

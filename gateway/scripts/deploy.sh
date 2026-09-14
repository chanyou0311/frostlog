#!/usr/bin/env bash
# Copy this unit to the Pi and run the installer there.
# Usage: gateway/scripts/deploy.sh [user@host]   (default: chanyou@192.168.100.40)
#
# Only this directory goes, into a directory of its own: the gateway is installed
# and restarted without touching the collector beside it, and what lands in
# ~/frostlog-gateway/ is the layout the systemd unit and the installer expect.
set -euo pipefail
target="${1:-chanyou@192.168.100.40}"
cd "$(dirname "$0")/.."
rsync -az --delete \
  --exclude .venv --exclude __pycache__ --exclude .pytest_cache --exclude .ruff_cache \
  ./ "$target:frostlog-gateway/"
ssh "$target" 'bash frostlog-gateway/scripts/install.sh'

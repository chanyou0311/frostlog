#!/usr/bin/env bash
# Copy this unit to the Pi and run the installer there.
# Usage: collection/scripts/deploy.sh [user@host]   (default: chanyou@192.168.100.40)
#
# Only this directory goes: the Pi runs the collector and nothing else, and what
# lands in ~/frostlog/ is the layout the systemd units and the installer expect.
set -euo pipefail
target="${1:-chanyou@192.168.100.40}"
cd "$(dirname "$0")/.."
rsync -az --delete \
  --exclude .git --exclude .venv --exclude __pycache__ --exclude .pytest_cache \
  --exclude .ruff_cache --exclude .claude --exclude .github \
  ./ "$target:frostlog/"
ssh "$target" 'bash frostlog/scripts/install.sh'

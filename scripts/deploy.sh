#!/usr/bin/env bash
# Copy this checkout to the Pi and run the installer there.
# Usage: scripts/deploy.sh [user@host]   (default: chanyou@192.168.100.26)
set -euo pipefail
target="${1:-chanyou@192.168.100.26}"
cd "$(dirname "$0")/.."
rsync -az --delete \
  --exclude .git --exclude .venv --exclude __pycache__ --exclude .pytest_cache \
  --exclude .ruff_cache --exclude .claude \
  ./ "$target:frostlog/"
ssh "$target" 'bash frostlog/scripts/install.sh'

#!/usr/bin/env bash
# Set the gateway up on the Pi: uv, the virtualenv and the user-level systemd unit.
# Runs as the ordinary user (no sudo). Running it again only changes what differs.
set -euo pipefail
cd "$(dirname "$0")/.."

UV_VERSION=0.12.10
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null || [ "$(uv --version | awk '{print $2}')" != "$UV_VERSION" ]; then
  curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" | sh
fi

# The Pi is armv6: use the OS Python (uv has no builds for it) and build
# dbus-fast (and Cython, if it comes as an sdist) as pure Python.
export UV_PYTHON_PREFERENCE=only-system
export SKIP_CYTHON=1
export NO_CYTHON_COMPILE=true
uv sync --frozen --no-dev

# The same settings file the rest of frostlog reads; the collector may have made it.
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}"
mkdir -p "$config_dir/frostlog"
if [ ! -f "$config_dir/frostlog/env" ]; then
  install -m 600 scripts/env.example "$config_dir/frostlog/env"
  echo "created $config_dir/frostlog/env; set FROSTLOG_COOLER_ADDRESS to start the gateway"
fi

mkdir -p "$config_dir/systemd/user"
install -m 644 scripts/systemd/* "$config_dir/systemd/user/"
systemctl --user daemon-reload
loginctl enable-linger "$USER"
systemctl --user enable --now frostlog-gateway.service
# Pick up new code. The unit stays skipped until FROSTLOG_COOLER_ADDRESS is set.
systemctl --user restart frostlog-gateway.service
systemctl --user --no-pager --no-legend list-units 'frostlog-*'

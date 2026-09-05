#!/usr/bin/env bash
# Set frostlog up on the Pi: uv, the virtualenv and the user-level systemd units.
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

config_dir="${XDG_CONFIG_HOME:-$HOME/.config}"
mkdir -p "$config_dir/frostlog" "${XDG_STATE_HOME:-$HOME/.local/state}/frostlog"
if [ ! -f "$config_dir/frostlog/env" ]; then
  install -m 600 scripts/env.example "$config_dir/frostlog/env"
  echo "created $config_dir/frostlog/env; fill in the FROSTLOG_S3_* values for uploads"
fi

mkdir -p "$config_dir/systemd/user"
install -m 644 scripts/systemd/* "$config_dir/systemd/user/"
systemctl --user daemon-reload
loginctl enable-linger "$USER"
systemctl --user enable --now frostlog-ambient.service frostlog-upload.timer
systemctl --user restart frostlog-ambient.service
# Without a pinned address the receiver would latch onto any nearby Anker device,
# so the cooler service only runs once FROSTLOG_COOLER_ADDRESS is set.
if grep -Eq '^FROSTLOG_COOLER_ADDRESS=[^[:space:]]' "$config_dir/frostlog/env"; then
  systemctl --user enable --now frostlog-cooler.service
  systemctl --user restart frostlog-cooler.service
else
  systemctl --user disable --now frostlog-cooler.service 2>/dev/null || true
  echo "frostlog-cooler stays off until FROSTLOG_COOLER_ADDRESS is set in $config_dir/frostlog/env"
fi
systemctl --user --no-pager --no-legend list-units 'frostlog-*'

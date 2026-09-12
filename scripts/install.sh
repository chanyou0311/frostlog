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
# The environment sensor is read with every cooler message now; its own service is gone.
if [ -f "$config_dir/systemd/user/frostlog-ambient@.service" ]; then
  # systemctl expands no globs here, so the instances have to be named. Removing the
  # template would not stop one that is already running: it would go on reading the
  # sensor beside the cooler service. Loaded units and enabled symlinks are both looked
  # for, because an instance can be either without being the other.
  instances=$(
    {
      systemctl --user list-units --all --plain --no-legend 'frostlog-ambient@*.service' |
        awk '{print $1}'
      find "$config_dir/systemd/user" -name 'frostlog-ambient@*.service' -exec basename {} \;
    } 2>/dev/null | sort -u
  )
  if [ -n "$instances" ]; then
    # shellcheck disable=SC2086 # one unit per word is what systemctl wants
    systemctl --user disable --now $instances
  fi
  rm -f "$config_dir/systemd/user/frostlog-ambient@.service"
fi
systemctl --user daemon-reload
loginctl enable-linger "$USER"
systemctl --user enable --now frostlog-cooler.service frostlog-upload.timer
# Pick up new code. frostlog-cooler stays skipped until FROSTLOG_COOLER_ADDRESS is set.
systemctl --user restart frostlog-cooler.service
systemctl --user --no-pager --no-legend list-units 'frostlog-*'

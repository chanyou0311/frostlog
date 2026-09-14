#!/usr/bin/env bash
# Set frostlog-controller up on the Pi: user-level systemd units, nothing else.
#
# No uv, no virtualenv: the job is standard library only, so it runs against
# the OS /usr/bin/python3 directly -- a venv would add import cost to a job
# that starts often on a Pi Zero's single core, for no library it needs.
# Runs as the ordinary user (no sudo). Running it again only changes what differs.
set -euo pipefail
cd "$(dirname "$0")/.."

config_dir="${XDG_CONFIG_HOME:-$HOME/.config}"
mkdir -p "$config_dir/frostlog" "${XDG_STATE_HOME:-$HOME/.local/state}/frostlog"
if [ ! -f "$config_dir/frostlog/controller.toml" ]; then
  install -m 644 scripts/controller.toml.example "$config_dir/frostlog/controller.toml"
  echo "created $config_dir/frostlog/controller.toml; fill in home_ssid"
fi

mkdir -p "$config_dir/systemd/user"
install -m 644 scripts/systemd/* "$config_dir/systemd/user/"
systemctl --user daemon-reload
loginctl enable-linger "$USER"
systemctl --user enable --now frostlog-controller.timer
systemctl --user --no-pager --no-legend list-timers 'frostlog-controller.timer'

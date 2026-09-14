"""Controller settings, read fresh from a TOML file on every run.

The thresholds and the setpoints are tuned by watching the fridge, not by
reading code, so they live in a config file rather than as constants -- and
the file is re-read every run rather than cached, since nothing here justifies
a resident process.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    """The config file is missing, unreadable, or missing a required key."""


@dataclass(frozen=True)
class Config:
    home_ssid: str
    home_setpoint_celsius: float = 20
    away_setpoint_celsius: float = -20
    max_attempts: int = 5


def config_path() -> Path:
    env = os.environ.get("FROSTLOG_CONTROLLER_CONFIG")
    if env:
        return Path(env)
    config_home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(config_home) / "frostlog" / "controller.toml"


def load_config(path: Path | None = None) -> Config:
    path = path if path is not None else config_path()
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"no config file at {path}") from None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from None
    try:
        home_ssid = data["home_ssid"]
    except KeyError:
        raise ConfigError(f"{path}: home_ssid is required") from None
    if not isinstance(home_ssid, str) or not home_ssid:
        raise ConfigError(f"{path}: home_ssid must be a non-empty string")
    return Config(
        home_ssid=home_ssid,
        home_setpoint_celsius=data.get("home_setpoint_celsius", 20),
        away_setpoint_celsius=data.get("away_setpoint_celsius", -20),
        max_attempts=data.get("max_attempts", 5),
    )

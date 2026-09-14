"""Controller settings, read fresh from a TOML file on every run.

The thresholds and the setpoints are tuned by watching the fridge, not by
reading code, so they live in a config file rather than as constants -- and
the file is re-read every run rather than cached, since nothing here justifies
a resident process.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from frostlog_controller.paths import xdg_path


class ConfigError(Exception):
    """The config file is missing, unreadable, or missing a required key."""


@dataclass(frozen=True)
class Config:
    home_ssid: str
    home_setpoint_celsius: int = 20
    away_setpoint_celsius: int = -20
    max_attempts: int = 5


def config_path() -> Path:
    return xdg_path(
        "FROSTLOG_CONTROLLER_CONFIG", "XDG_CONFIG_HOME", Path.home() / ".config", "controller.toml"
    )


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
        home_setpoint_celsius=_integer(data, "home_setpoint_celsius", 20, path),
        away_setpoint_celsius=_integer(data, "away_setpoint_celsius", -20, path),
        max_attempts=_integer(data, "max_attempts", 5, path),
    )


def _integer(data: dict[str, Any], key: str, default: int, path: Path) -> int:
    """A whole number, as the gateway wants it; `20.0` in TOML is a float and would be refused."""
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{path}: {key} must be an integer")
    return value

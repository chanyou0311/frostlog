from pathlib import Path

import pytest

from frostlog_controller.config import ConfigError, load_config


def test_requires_home_ssid(tmp_path: Path) -> None:
    path = tmp_path / "controller.toml"
    path.write_text("home_setpoint_celsius = 21\n")
    with pytest.raises(ConfigError, match="home_ssid"):
        load_config(path)


def test_home_ssid_must_be_a_non_empty_string(tmp_path: Path) -> None:
    path = tmp_path / "controller.toml"
    path.write_text("home_ssid = 123\n")
    with pytest.raises(ConfigError, match="home_ssid"):
        load_config(path)


def test_defaults(tmp_path: Path) -> None:
    path = tmp_path / "controller.toml"
    path.write_text('home_ssid = "Home"\n')
    config = load_config(path)
    assert config.home_ssid == "Home"
    assert config.home_setpoint_celsius == 20
    assert config.away_setpoint_celsius == -20
    assert config.max_attempts == 5


def test_overrides(tmp_path: Path) -> None:
    path = tmp_path / "controller.toml"
    path.write_text(
        'home_ssid = "Home"\n'
        "home_setpoint_celsius = 18\n"
        "away_setpoint_celsius = -19\n"
        "max_attempts = 3\n"
    )
    config = load_config(path)
    assert config.home_setpoint_celsius == 18
    assert config.away_setpoint_celsius == -19
    assert config.max_attempts == 3


def test_setpoints_must_be_integers(tmp_path: Path) -> None:
    # The gateway takes whole degrees; a TOML `20.0` is a float and would be refused there.
    path = tmp_path / "controller.toml"
    path.write_text('home_ssid = "Home"\nhome_setpoint_celsius = 20.0\n')
    with pytest.raises(ConfigError, match="home_setpoint_celsius"):
        load_config(path)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="no config file"):
        load_config(tmp_path / "does-not-exist.toml")


def test_malformed_toml(tmp_path: Path) -> None:
    path = tmp_path / "controller.toml"
    path.write_text("this is not valid toml [[[")
    with pytest.raises(ConfigError):
        load_config(path)

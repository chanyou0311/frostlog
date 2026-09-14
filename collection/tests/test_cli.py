"""Argument handling and the small wiring the commands do (no hardware is touched)."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from frostlog import cli
from frostlog.ambient import i2c
from frostlog.settings import Settings

runner = CliRunner()


def test_bad_address_is_a_usage_error() -> None:
    result = runner.invoke(cli.app, ["read", "ambient", "--address", "0xzz"])
    assert result.exit_code == 2, result.output


def test_unknown_sensor_is_a_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(i2c, "SMBus2Bus", lambda number: object())
    result = runner.invoke(cli.app, ["read", "ambient", "--sensor", "nope"])
    assert result.exit_code == 2, result.output


def test_the_cooler_is_still_recorded_when_the_sensor_cannot_be_opened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_bus(number: int) -> object:
        raise OSError(2, "No such file or directory: '/dev/i2c-1'")

    monkeypatch.setattr(i2c, "SMBus2Bus", no_bus)
    assert cli.environment_sampler(Settings(), "dht20", lambda _event: None) is None


def test_an_unknown_sensor_for_the_cooler_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(i2c, "SMBus2Bus", lambda number: object())
    monkeypatch.setenv("FROSTLOG_GATEWAY_SOCKET_DIR", str(tmp_path))
    result = runner.invoke(cli.app, ["read", "cooler", "--sensor", "nope", "--duration", "0"])
    assert result.exit_code == 2, result.output


def test_read_cooler_needs_to_know_where_the_gateway_listens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FROSTLOG_GATEWAY_SOCKET_DIR", raising=False)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    result = runner.invoke(cli.app, ["read", "cooler", "--no-environment"])
    assert result.exit_code == 1

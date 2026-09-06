"""Argument handling of ``read ambient`` (no hardware is touched)."""

import pytest
from typer.testing import CliRunner

from frostlog import cli
from frostlog.ambient import i2c

runner = CliRunner()


def test_bad_address_is_a_usage_error() -> None:
    result = runner.invoke(cli.app, ["read", "ambient", "--address", "0xzz"])
    assert result.exit_code == 2
    assert "--address" in result.output


def test_unknown_sensor_is_a_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(i2c, "SMBus2Bus", lambda number: object())
    result = runner.invoke(cli.app, ["read", "ambient", "--sensor", "nope"])
    assert result.exit_code == 2
    assert "nope" in result.output

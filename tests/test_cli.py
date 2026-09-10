"""Argument handling and the small wiring the commands do (no hardware is touched)."""

import json

import captured
import pytest
from typer.testing import CliRunner

from frostlog import cli, records
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(i2c, "SMBus2Bus", lambda number: object())
    result = runner.invoke(
        cli.app, ["read", "cooler", "--address", "AA:BB", "--sensor", "nope", "--duration", "0"]
    )
    assert result.exit_code == 2, result.output


def test_read_cooler_needs_an_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FROSTLOG_COOLER_ADDRESS", raising=False)
    result = runner.invoke(cli.app, ["read", "cooler", "--no-environment"])
    assert result.exit_code == 1


def test_decode_prints_the_parameters_of_a_recorded_message() -> None:
    record = records.cooler(
        "everfrost",
        captured.ADDRESS,
        captured.DISCHARGING.frames,
        pattern="03010f",
        cmd="4402",
        plain=captured.DISCHARGING.plain,
    )
    result = runner.invoke(cli.app, ["decode"], input=records.line(record))
    assert result.exit_code == 0, result.output
    decoded = json.loads(result.stdout)["decoded"]
    assert decoded["source"] == "plain"
    assert decoded["params"]["a2"]["str"] == captured.SERIAL_NUMBER


def test_decode_skips_lines_that_are_not_records() -> None:
    result = runner.invoke(cli.app, ["decode"], input='{"not":"a record"}\n\n')
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == ""

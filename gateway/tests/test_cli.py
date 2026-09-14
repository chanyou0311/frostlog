"""The command line: what it refuses to start without, and what ``decode`` reads."""

import json
from pathlib import Path

import captured
import pytest
from typer.testing import CliRunner

from frostlog_gateway.cli import app

runner = CliRunner()


def test_the_gateway_will_not_guess_which_device_to_talk_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FROSTLOG_COOLER_ADDRESS", raising=False)
    result = runner.invoke(app, ["run"])
    assert result.exit_code == 1


def test_an_unknown_cooler_model_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FROSTLOG_COOLER_MODEL", "fridge")
    monkeypatch.setenv("FROSTLOG_COOLER_ADDRESS", "AA:BB")
    assert runner.invoke(app, ["run"]).exit_code == 1


def test_the_sockets_go_where_the_runtime_directory_is(monkeypatch: pytest.MonkeyPatch) -> None:
    from frostlog_gateway import cli

    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    monkeypatch.delenv("FROSTLOG_GATEWAY_SOCKET_DIR", raising=False)
    assert cli._socket_dir(None) == Path("/run/user/1000/frostlog")
    monkeypatch.setenv("FROSTLOG_GATEWAY_SOCKET_DIR", "/somewhere/else")
    assert cli._socket_dir(None) == Path("/somewhere/else")
    assert cli._socket_dir(Path("/asked/for")) == Path("/asked/for")


def test_decode_adds_a_reading_to_what_it_is_given() -> None:
    message = {"kind": "message", "cmd": "4402", "plain": captured.HANDSHAKE_PLAIN}
    result = runner.invoke(app, ["decode"], input=json.dumps(message) + "\n\nnot json\n")
    assert result.exit_code == 0
    decoded = json.loads(result.stdout.splitlines()[0])["decoded"]
    assert decoded["params"]["a2"]["ascii"] == "ESP32"


def test_decode_passes_on_what_it_cannot_read() -> None:
    event = {"kind": "ble_connected", "address": "AA:BB"}
    result = runner.invoke(app, ["decode"], input=json.dumps(event) + "\n")
    assert json.loads(result.stdout) == event  # an event with no message is left alone

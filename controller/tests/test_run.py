import subprocess
from pathlib import Path
from typing import Any

from frostlog_controller import state
from frostlog_controller.config import Config
from frostlog_controller.gateway import GatewayError, GatewayUnreachable
from frostlog_controller.presence import NmcliRunner
from frostlog_controller.run import run

CONFIG = Config(
    home_ssid="Home", home_setpoint_celsius=20, away_setpoint_celsius=-20, max_attempts=3
)


def _nmcli(home: bool) -> NmcliRunner:
    stdout = "yes:Home\n" if home else "no:Home\n"
    return lambda: subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _unknown_nmcli() -> NmcliRunner:
    def runner() -> "subprocess.CompletedProcess[str]":
        raise FileNotFoundError("nmcli")

    return runner


class RecordingSender:
    """Stands in for gateway.send_command: records calls, returns or raises on cue."""

    def __init__(
        self, response: dict[str, Any] | None = None, error: Exception | None = None
    ) -> None:
        self.calls: list[tuple[Path, str, Any, str, str]] = []
        self._response = response
        self._error = error

    def __call__(
        self, path: Path, setting: str, value: Any, source: str, reason: str
    ) -> dict[str, Any]:
        self.calls.append((path, setting, value, source, reason))
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


def _state_path(tmp_path: Path) -> Path:
    return tmp_path / "controller.json"


def test_first_run_adopts_presence_without_a_command(tmp_path: Path) -> None:
    path = _state_path(tmp_path)
    sender = RecordingSender()
    run(CONFIG, path, tmp_path / "commands.sock", _nmcli(True), sender)
    assert sender.calls == []
    assert state.load(path) == state.State(home=True, attempts=0)


def test_unchanged_presence_does_nothing(tmp_path: Path) -> None:
    path = _state_path(tmp_path)
    state.save(path, state.State(home=True, attempts=0))
    sender = RecordingSender()
    run(CONFIG, path, tmp_path / "commands.sock", _nmcli(True), sender)
    assert sender.calls == []
    assert state.load(path) == state.State(home=True, attempts=0)


def test_a_change_that_undid_itself_takes_its_failed_attempts_with_it(tmp_path: Path) -> None:
    # Leaving failed a few times, then presence came back to the stored judgment:
    # the next change starts its own count, not this one's.
    path = _state_path(tmp_path)
    state.save(path, state.State(home=True, attempts=CONFIG.max_attempts - 1))
    sender = RecordingSender()
    run(CONFIG, path, tmp_path / "commands.sock", _nmcli(True), sender)
    assert sender.calls == []
    assert state.load(path) == state.State(home=True, attempts=0)


def test_arriving_home_sends_the_home_setpoint(tmp_path: Path) -> None:
    path = _state_path(tmp_path)
    state.save(path, state.State(home=False, attempts=0))
    socket_path = tmp_path / "commands.sock"
    sender = RecordingSender(response={"command_id": "1", "status": "accepted"})
    run(CONFIG, path, socket_path, _nmcli(True), sender)
    assert sender.calls == [(socket_path, "setpoint_celsius", 20, "controller", "arrived_home")]
    assert state.load(path) == state.State(home=True, attempts=0)


def test_leaving_home_sends_the_away_setpoint(tmp_path: Path) -> None:
    path = _state_path(tmp_path)
    state.save(path, state.State(home=True, attempts=0))
    socket_path = tmp_path / "commands.sock"
    sender = RecordingSender(response={"command_id": "1", "status": "accepted"})
    run(CONFIG, path, socket_path, _nmcli(False), sender)
    assert sender.calls == [(socket_path, "setpoint_celsius", -20, "controller", "left_home")]
    assert state.load(path) == state.State(home=False, attempts=0)


def test_rejected_command_keeps_the_old_judgment_and_counts_the_attempt(tmp_path: Path) -> None:
    path = _state_path(tmp_path)
    state.save(path, state.State(home=False, attempts=0))
    sender = RecordingSender(response={"command_id": "1", "status": "rejected", "error": "busy"})
    run(CONFIG, path, tmp_path / "commands.sock", _nmcli(True), sender)
    assert state.load(path) == state.State(home=False, attempts=1)


def test_connection_error_is_treated_like_a_rejection(tmp_path: Path) -> None:
    path = _state_path(tmp_path)
    state.save(path, state.State(home=False, attempts=0))
    sender = RecordingSender(error=GatewayUnreachable("no gateway"))
    run(CONFIG, path, tmp_path / "commands.sock", _nmcli(True), sender)
    assert state.load(path) == state.State(home=False, attempts=1)


def test_an_unknown_outcome_is_not_asked_again(tmp_path: Path) -> None:
    # The request went out and no answer came back: the gateway may have written it,
    # so asking again could write over what the cooler or a hand has set since.
    path = _state_path(tmp_path)
    state.save(path, state.State(home=False, attempts=0))
    sender = RecordingSender(error=GatewayError("no answer"))
    run(CONFIG, path, tmp_path / "commands.sock", _nmcli(True), sender)
    assert len(sender.calls) == 1
    assert state.load(path) == state.State(home=True, attempts=0)


def test_gives_up_at_max_attempts_and_adopts_the_new_judgment(tmp_path: Path) -> None:
    path = _state_path(tmp_path)
    state.save(path, state.State(home=False, attempts=CONFIG.max_attempts - 1))
    sender = RecordingSender(response={"command_id": "1", "status": "rejected", "error": "busy"})
    run(CONFIG, path, tmp_path / "commands.sock", _nmcli(True), sender)
    assert state.load(path) == state.State(home=True, attempts=0)


def test_unknown_presence_does_nothing(tmp_path: Path) -> None:
    path = _state_path(tmp_path)
    state.save(path, state.State(home=False, attempts=0))
    sender = RecordingSender()
    run(CONFIG, path, tmp_path / "commands.sock", _unknown_nmcli(), sender)
    assert sender.calls == []
    assert state.load(path) == state.State(home=False, attempts=0)

import json
import socket
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from frostlog_controller import gateway
from frostlog_controller.gateway import GatewayError, GatewayUnreachable, send_command


@pytest.fixture
def socket_path() -> Iterator[Path]:
    # AF_UNIX paths are capped around 100 bytes; pytest's own tmp_path (nested
    # under this repository's worktree path) routinely runs past that, so the
    # socket gets its own short-lived directory instead.
    with tempfile.TemporaryDirectory(prefix="fc-") as directory:
        yield Path(directory) / "c.sock"


def _fake_gateway(
    path: Path, response: dict[str, Any], answer: bool = True
) -> tuple[threading.Thread, list[bytes]]:
    """A gateway that accepts one connection, echoes what it received, and replies once."""
    received: list[bytes] = []
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)

    def handle() -> None:
        conn, _ = server.accept()
        with conn:
            data = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            received.append(data)
            if answer:
                conn.sendall((json.dumps(response) + "\n").encode("utf-8"))
        server.close()

    thread = threading.Thread(target=handle, daemon=True)
    thread.start()
    return thread, received


def test_send_command_accepted(socket_path: Path) -> None:
    path = socket_path
    thread, received = _fake_gateway(path, {"command_id": "abc", "status": "accepted"})
    response = send_command(path, "setpoint_celsius", 20, "controller", "arrived_home")
    thread.join(timeout=5)
    assert response == {"command_id": "abc", "status": "accepted"}
    assert json.loads(received[0]) == {
        "setting": "setpoint_celsius",
        "value": 20,
        "source": "controller",
        "reason": "arrived_home",
    }


def test_send_command_rejected(socket_path: Path) -> None:
    path = socket_path
    thread, _ = _fake_gateway(
        path, {"command_id": "abc", "status": "rejected", "error": "not_connected"}
    )
    response = send_command(path, "setpoint_celsius", -20, "controller", "left_home")
    thread.join(timeout=5)
    assert response["status"] == "rejected"
    assert response["error"] == "not_connected"


def test_the_socket_is_where_the_units_agree_it_is(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FROSTLOG_GATEWAY_SOCKET_DIR", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    assert gateway.socket_path() == Path("/run/user/1000/frostlog/commands.sock")
    monkeypatch.setenv("FROSTLOG_GATEWAY_SOCKET_DIR", "/somewhere/else")
    assert gateway.socket_path() == Path("/somewhere/else/commands.sock")
    monkeypatch.delenv("FROSTLOG_GATEWAY_SOCKET_DIR")
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    with pytest.raises(GatewayError):
        gateway.socket_path()  # not a relative path that looks like a gateway that is down


def test_send_command_no_server(socket_path: Path) -> None:
    path = socket_path  # nothing listens here
    with pytest.raises(GatewayUnreachable):
        send_command(path, "setpoint_celsius", 20, "controller", "arrived_home")


def test_a_gateway_that_takes_the_request_and_says_nothing_is_not_unreachable(
    socket_path: Path,
) -> None:
    # The request went out; that it was not answered is a different failure from
    # never having got through, and the caller must not treat it as one.
    thread, _ = _fake_gateway(socket_path, {}, answer=False)
    with pytest.raises(GatewayError) as caught:
        send_command(socket_path, "setpoint_celsius", 20, "controller", "arrived_home")
    thread.join(timeout=5)
    assert not isinstance(caught.value, GatewayUnreachable)

import json
import socket
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from frostlog_controller.gateway import GatewayError, send_command


@pytest.fixture
def socket_path() -> Iterator[Path]:
    # AF_UNIX paths are capped around 100 bytes; pytest's own tmp_path (nested
    # under this repository's worktree path) routinely runs past that, so the
    # socket gets its own short-lived directory instead.
    with tempfile.TemporaryDirectory(prefix="fc-") as directory:
        yield Path(directory) / "c.sock"


def _fake_gateway(path: Path, response: dict[str, Any]) -> tuple[threading.Thread, list[bytes]]:
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


def test_send_command_no_server(socket_path: Path) -> None:
    path = socket_path  # nothing listens here
    with pytest.raises(GatewayError):
        send_command(path, "setpoint_celsius", 20, "controller", "arrived_home")

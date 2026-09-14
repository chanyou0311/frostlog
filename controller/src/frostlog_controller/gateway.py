"""One request to frostlog-gateway's command socket: one JSON line out, one back.

The socket, not a library import, is the only thing shared with the gateway --
see the gateway-protocol design record (Issue #38) for the wire format. A
connection error and an explicit rejection are both just "the command did not
take"; callers are expected to treat them the same way.
"""

import json
import os
import socket
from pathlib import Path
from typing import Any

#: The gateway answers only after the frame has gone to the cooler, and a Bluetooth
#: write on a poor link can take seconds. Giving up sooner than the write would count
#: a command that reached the cooler as one that did not, and send it again.
TIMEOUT_SECONDS = 30.0


class GatewayError(Exception):
    """The request went out, and what became of it is unknown: no answer, or one that
    could not be read. Not to be retried -- the gateway may well have written it."""


class GatewayUnreachable(GatewayError):
    """The request never went out: no socket, no gateway. Safe to try again."""


def socket_dir() -> Path:
    """Where the gateway's sockets are: the same answer the gateway and the collector give.

    Without either variable there is no answer; a relative path would look like a
    gateway that is down, and be retried as one.
    """
    env = os.environ.get("FROSTLOG_GATEWAY_SOCKET_DIR")
    if env:
        return Path(env)
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        raise GatewayError("set FROSTLOG_GATEWAY_SOCKET_DIR or XDG_RUNTIME_DIR")
    return Path(runtime_dir) / "frostlog"


def socket_path() -> Path:
    return socket_dir() / "commands.sock"


def send_command(
    path: Path, setting: str, value: float | int | str, source: str, reason: str
) -> dict[str, Any]:
    """Send one command request and return the gateway's decoded response."""
    request = {"setting": setting, "value": value, "source": source, "reason": reason}
    line = json.dumps(request) + "\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(TIMEOUT_SECONDS)
        try:
            sock.connect(str(path))
        except OSError as exc:
            raise GatewayUnreachable(f"cannot reach the gateway at {path}: {exc}") from None
        try:
            sock.sendall(line.encode("utf-8"))
            sock.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        except OSError as exc:
            raise GatewayError(f"no answer from the gateway: {exc}") from None
    response = b"".join(chunks).decode("utf-8").strip()
    if not response:
        raise GatewayError("gateway closed the connection without a response")
    try:
        decoded = json.loads(response.splitlines()[0])
    except json.JSONDecodeError as exc:
        raise GatewayError(f"malformed response from the gateway: {exc}") from None
    if not isinstance(decoded, dict):
        raise GatewayError(f"malformed response from the gateway: {response!r}")
    return decoded

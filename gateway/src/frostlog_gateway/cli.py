"""The ``frostlog-gateway`` command: hold the link, look for the cooler, read messages.

Logs go to stderr; the gateway's output is the event socket, not stdout. Exit
status 0 on success, 1 when the run failed, 2 for bad arguments.
"""

import asyncio
import json
import logging
import os
import signal
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

import typer

from frostlog_gateway import MODEL

log = logging.getLogger("frostlog_gateway")

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)


@app.callback()
def main(
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Debug logging on stderr.")
    ] = False,
) -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def fail(message: str) -> typer.Exit:
    log.error("%s", message)
    return typer.Exit(1)


def _model() -> str:
    """Which cooler this Pi has. One is implemented; another would be a new dialect."""
    model = os.environ.get("FROSTLOG_COOLER_MODEL") or MODEL
    if model != MODEL:
        raise fail(f"unknown cooler model {model!r}; known: [{MODEL!r}]")
    return model


def _socket_dir(given: Path | None) -> Path:
    if given is not None:
        return given
    directory = os.environ.get("FROSTLOG_GATEWAY_SOCKET_DIR")
    if directory:
        return Path(directory)
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        raise fail("set FROSTLOG_GATEWAY_SOCKET_DIR or XDG_RUNTIME_DIR (--socket-dir overrides)")
    return Path(runtime) / "frostlog"


@app.command("run")
def run(
    address: Annotated[
        str | None,
        typer.Option(help="Bluetooth address of the cooler; default FROSTLOG_COOLER_ADDRESS."),
    ] = None,
    socket_dir: Annotated[
        Path | None,
        typer.Option(help="Where to put the sockets; default FROSTLOG_GATEWAY_SOCKET_DIR."),
    ] = None,
) -> None:
    """Hold the link to the cooler and serve the event and command sockets.

    The address is required: without one the gateway would latch onto whatever
    Anker device is nearby and negotiate with it. Use ``scan`` to find the cooler.
    """
    from frostlog_gateway import service

    _model()
    address = address or os.environ.get("FROSTLOG_COOLER_ADDRESS")
    if not address:
        raise fail("set FROSTLOG_COOLER_ADDRESS or pass --address (scan lists devices)")
    service.run(address, _socket_dir(socket_dir))


@app.command("scan")
def scan(
    duration: Annotated[float, typer.Option(help="Seconds to listen for advertisements.")] = 10.0,
) -> None:
    """List nearby Bluetooth devices, those advertising Anker's service first."""
    from frostlog_gateway import ble

    _model()
    try:
        found = asyncio.run(ble.scan(duration))
    except Exception as exc:  # Bluetooth stack errors are library-specific
        raise fail(f"scan failed: {exc}") from None
    for device in found:
        print(json.dumps(asdict(device)), flush=True)


@app.command("decode")
def decode() -> None:
    """Read messages on stdin and write them back with a "decoded" field (for development).

    Takes the gateway's own ``message`` events and the collector's cooler records
    alike: both carry the frames as they arrived and the body in the clear.
    """
    from frostlog_gateway.decoder import EverfrostDecoder

    # Behave like other UNIX filters when the reader goes away (e.g. `| head`). Only
    # here: the resident gateway writes to sockets whose readers come and go, and
    # must outlive every one of them.
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    decoder = EverfrostDecoder()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message: Any = json.loads(line)
        except json.JSONDecodeError as exc:
            log.warning("skipping a line that is not JSON: %s", exc)
            continue
        if isinstance(message, dict) and (message.get("frames") or message.get("plain")):
            message["decoded"] = decoder.decode(message)
        print(json.dumps(message), flush=True)

"""The ``frostlog`` command: read sources, upload files, decode cooler records.

Data goes to stdout as JSON lines (or to files with ``--output``), logs go to
stderr. Exit status 0 on success, 1 when the run failed, 2 for bad arguments.
"""

import logging
import signal
import sys
import threading
from pathlib import Path
from typing import Annotated

import typer

from frostlog import records
from frostlog.settings import Settings
from frostlog.store import Store

log = logging.getLogger("frostlog")

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)
read_app = typer.Typer(no_args_is_help=True, help="Read from a source and emit records.")
app.add_typer(read_app, name="read")

OutputOption = Annotated[
    Path | None,
    typer.Option(
        "--output", "-o", help="Save records under this directory instead of printing them."
    ),
]


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
    # Behave like other UNIX filters when the reader goes away (e.g. `| head`).
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)


class Output:
    """Where records go: files under a directory, or stdout one line at a time."""

    def __init__(self, directory: Path | None) -> None:
        self._store = Store(directory) if directory is not None else None

    def write(self, record: records.Ambient | records.Cooler | records.Event) -> None:
        if self._store is not None:
            self._store.append(record)
        else:
            sys.stdout.write(records.to_json(record))
            sys.stdout.write("\n")
            sys.stdout.flush()

    def close(self) -> None:
        if self._store is not None:
            self._store.close()

    def __enter__(self) -> "Output":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def stop_signal() -> threading.Event:
    """An event that is set when SIGTERM or SIGINT arrives (systemd stop, Ctrl-C)."""
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    return stop


def fail(message: str) -> typer.Exit:
    log.error("%s", message)
    return typer.Exit(1)


@read_app.command("ambient")
def read_ambient(
    interval: Annotated[float, typer.Option(help="Seconds between reads.")] = 10.0,
    count: Annotated[int | None, typer.Option(help="Stop after this many reads.")] = None,
    output: OutputOption = None,
) -> None:
    """Read the ambient temperature and humidity sensor (FROSTLOG_AMBIENT_SENSOR)."""
    from frostlog.ambient import registry
    from frostlog.ambient.i2c import SMBus2Bus
    from frostlog.ambient.reader import read_loop

    settings = Settings()
    try:
        bus = SMBus2Bus(settings.i2c_bus)
        sensor = registry.create(settings.ambient_sensor, bus, settings.ambient_address)
    except (OSError, ValueError) as exc:
        raise fail(f"cannot open sensor: {exc}") from None
    stop = stop_signal()
    with Output(output) as out:
        for record in read_loop(sensor, interval, count, sleep=lambda s: stop.wait(s)):
            out.write(record)
            if stop.is_set():
                break

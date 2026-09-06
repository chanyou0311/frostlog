"""The ``frostlog`` command: read sources, upload files, decode cooler records.

Data goes to stdout as JSON lines (or to files with ``--output``), logs go to
stderr. Exit status 0 on success, 1 when the run failed, 2 for bad arguments.
"""

import asyncio
import json
import logging
import signal
import sys
import threading
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

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

    def write(self, record: records.Record) -> None:
        if self._store is not None:
            self._store.append(record)
        else:
            sys.stdout.write(records.line(record))
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


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise fail(f"bad FROSTLOG_* setting: {problems}") from None


@read_app.command("ambient")
def read_ambient(
    sensor: Annotated[str, typer.Option(help="Which sensor to read: dht20 or am2320.")] = "dht20",
    address: Annotated[
        int | None,
        typer.Option(
            parser=lambda text: int(text, 0),
            help="I2C address such as 0x38; default: the sensor's usual one.",
        ),
    ] = None,
    interval: Annotated[float, typer.Option(help="Seconds between reads.")] = 10.0,
    count: Annotated[int | None, typer.Option(help="Stop after this many reads.")] = None,
    output: OutputOption = None,
) -> None:
    """Read one ambient temperature and humidity sensor on the I2C bus (FROSTLOG_I2C_BUS)."""
    from frostlog.ambient import registry
    from frostlog.ambient.i2c import SMBus2Bus
    from frostlog.ambient.reader import read_loop

    settings = load_settings()
    try:
        bus = SMBus2Bus(settings.i2c_bus)
    except OSError as exc:
        raise fail(f"cannot open I2C bus {settings.i2c_bus}: {exc}") from None
    try:
        device = registry.create(sensor, bus, address)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--sensor") from None
    stop = stop_signal()
    with Output(output) as out:
        for record in read_loop(device, interval, count, sleep=lambda s: stop.wait(s)):
            out.write(record)
            if stop.is_set():
                break


@read_app.command("cooler")
def read_cooler(
    address: Annotated[
        str | None,
        typer.Option(help="Bluetooth address of the cooler; default FROSTLOG_COOLER_ADDRESS."),
    ] = None,
    duration: Annotated[float | None, typer.Option(help="Stop after this many seconds.")] = None,
    output: OutputOption = None,
    scan: Annotated[
        bool, typer.Option("--scan", help="List nearby Bluetooth devices and exit.")
    ] = False,
) -> None:
    """Receive what the cooler sends over Bluetooth (FROSTLOG_COOLER_MODEL).

    The address is required: without one the receiver would latch onto whatever
    Anker device is nearby and negotiate with it. Use --scan to find the cooler.
    """
    from frostlog.cooler import registry

    settings = load_settings()
    try:
        scanner = registry.scanner(settings.cooler_model)
    except ValueError as exc:
        raise fail(str(exc)) from None
    if scan:
        try:
            found = asyncio.run(scanner(10.0))
        except Exception as exc:  # Bluetooth stack errors are library-specific
            raise fail(f"scan failed: {exc}") from None
        for device in found:
            print(json.dumps(asdict(device)), flush=True)
        return
    address = address or settings.cooler_address
    if address is None:
        raise fail("set FROSTLOG_COOLER_ADDRESS or pass --address (--scan lists devices)")
    out = Output(output)
    receiver = registry.create_receiver(settings.cooler_model, out.write, address, duration)

    async def run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, stop.set)
        await receiver.run(stop)

    with out:
        asyncio.run(run())


@app.command("decode")
def decode() -> None:
    """Read cooler records on stdin and write them back with a "decoded" field (for development)."""
    from frostlog.cooler import base, registry

    decoders: dict[str, base.Decoder] = {}
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            record = records.from_json(line)
        except ValidationError as exc:
            log.warning("skipping a line that is not a record: %s", exc.errors()[0]["msg"])
            continue
        out = record.model_dump(mode="json")
        if isinstance(record, records.Cooler):
            if record.model not in decoders:
                try:
                    decoders[record.model] = registry.create_decoder(record.model)
                except ValueError as exc:
                    log.warning("%s", exc)
                    continue
            out["decoded"] = decoders[record.model].decode(record.payload)
        print(json.dumps(out), flush=True)


@app.command("upload")
def upload(
    directory: Annotated[
        Path, typer.Argument(help="Root of the record files (what --output wrote).")
    ],
) -> None:
    """Upload record files to the S3-compatible bucket (FROSTLOG_S3_*); re-running is a no-op."""
    from frostlog.upload.healthcheck import ping
    from frostlog.upload.s3 import S3ObjectStore
    from frostlog.upload.sync import sync

    settings = load_settings()
    if not (settings.s3_endpoint and settings.s3_access_key_id and settings.s3_secret_access_key):
        raise fail(
            "FROSTLOG_S3_ENDPOINT, FROSTLOG_S3_ACCESS_KEY_ID and "
            "FROSTLOG_S3_SECRET_ACCESS_KEY must be set"
        )
    if not directory.is_dir():
        raise fail(f"{directory} is not a directory")
    store = S3ObjectStore(
        settings.s3_endpoint,
        settings.s3_bucket,
        settings.s3_access_key_id,
        settings.s3_secret_access_key,
    )
    counts: Counter[str] = Counter()
    for action in sync(directory, store):
        counts[action.action] += 1
        if action.action != "skip" or log.isEnabledFor(logging.DEBUG):
            print(json.dumps(asdict(action)), flush=True)
    log.info("%s", ", ".join(f"{name} {n}" for name, n in sorted(counts.items())) or "no files")
    if counts["failed"]:
        raise typer.Exit(1)
    # Report only a run that reached the bucket, found files, and left nothing behind.
    if settings.healthcheck_url and counts and not (counts["conflict"] or counts["offline"]):
        ping(settings.healthcheck_url)

#!/usr/bin/env python3
"""Build the sample chunks the data contract is tested against in CI.

The samples are what one day's chunk of each stream looks like: real messages
captured from the cooler (``tests/captured.py``) turned into v1 records, one
connection's worth of them. Run after changing the record shape:

    uv run python scripts/build_samples.py

The output is deterministic, so a run that changes nothing leaves the files
untouched in git.
"""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import captured  # noqa: E402

from frostlog import records  # noqa: E402
from frostlog.cooler.everfrost.decoder import decode_state  # noqa: E402
from frostlog.cooler.everfrost.protocol import parse_frame  # noqa: E402

SAMPLES = ROOT / "contracts" / "samples" / "raw"
DAY = datetime(2026, 9, 6, 11, 2, 10, 719777, tzinfo=UTC)
UPTIME = 867.1
MODEL = "everfrost"
STATE_REPORT_COUNT = 48

#: Real readings from the DHT20 next to the cooler, cycled through the messages.
READINGS = [
    (25.21, 58.99),
    (25.34, 58.61),
    (25.48, 58.2),
    (25.63, 57.84),
    (25.79, 57.41),
    (26.02, 56.98),
    (26.2, 56.55),
]


def at(seconds: float) -> dict[str, Any]:
    return {
        "ts": DAY + timedelta(seconds=seconds),
        "uptime_seconds": round(UPTIME + seconds, 6),
        "boot_id": captured.BOOT_ID,
        "ts_synced": True,
    }


def environment(index: int) -> records.Environment:
    temperature, humidity = READINGS[index % len(READINGS)]
    return records.Environment(
        sensor="dht20", temperature_celsius=temperature, humidity_percent=humidity
    )


def cooler_rows() -> list[records.Cooler]:
    rows: list[records.Cooler] = []
    handshake = parse_frame(bytes.fromhex(captured.HANDSHAKE_FRAME))
    rows.append(
        records.Cooler(
            **at(0.0),
            model=MODEL,
            address=captured.ADDRESS,
            pattern=handshake.pattern.hex(),
            cmd=handshake.cmd.hex(),
            frames=[captured.HANDSHAKE_FRAME],
            plain=captured.HANDSHAKE_PLAIN,
            environment=environment(0),
        )
    )
    for index in range(STATE_REPORT_COUNT):
        message = captured.STATE_REPORTS[index % len(captured.STATE_REPORTS)]
        frame = parse_frame(bytes.fromhex(message.frames[0]))
        rows.append(
            records.Cooler(
                **at(9.2 + index * 3.0),
                model=MODEL,
                address=captured.ADDRESS,
                pattern=frame.pattern.hex(),
                cmd=frame.cmd.hex(),
                frames=message.frames,
                plain=message.plain,
                payload=decode_state(bytes.fromhex(message.plain)),
                environment=environment(index),
            )
        )
    rows.append(
        records.Cooler(
            **at(9.2 + STATE_REPORT_COUNT * 3.0),
            model=MODEL,
            address=captured.ADDRESS,
            frames=[captured.TORN_FRAME],
            error=f"length field 250 but {len(captured.TORN_FRAME) // 2} bytes",
            environment=environment(STATE_REPORT_COUNT),
        )
    )
    return rows


def event_rows() -> list[records.Event]:
    end = 9.2 + (STATE_REPORT_COUNT + 1) * 3.0
    return [
        records.Event(
            **at(-0.5), kind="ble_connected", address=captured.ADDRESS, name=captured.NAME
        ),
        records.Event(
            **at(0.9),
            kind="ble_negotiated",
            variant="solix",
            mtu=253,
            chip=captured.CHIP,
            firmware=captured.FIRMWARE,
            serial=captured.SERIAL_NUMBER,
            secret=captured.SECRET,
        ),
        records.Event(
            **at(60.0),
            kind="environment_read_failed",
            sensor="dht20",
            error="DHT20: I2C error: [Errno 5] Input/output error",
        ),
        records.Event(**at(120.0), kind="upload_started"),
        records.Event(
            **at(121.4), kind="upload_done", uploaded_chunk_count=2, uploaded_line_count=311
        ),
        records.Event(**at(end), kind="ble_silent", address=captured.ADDRESS),
        records.Event(**at(end + 0.2), kind="ble_disconnected", address=captured.ADDRESS),
        records.Event(**at(end + 15.0), kind="ble_device_not_found", address=captured.ADDRESS),
        records.Event(
            **at(end + 60.0),
            kind="ble_handshake_failed",
            cmd="0821",
            error="stage 5: expected a 64-byte public key, got 32",
        ),
        records.Event(**at(end + 120.0), kind="upload_started"),
        records.Event(
            **at(end + 121.1), kind="upload_done", uploaded_chunk_count=1, uploaded_line_count=42
        ),
    ]


def write(stream: str, rows: list[records.Cooler] | list[records.Event]) -> None:
    # One JSON record per line, as in a chunk, but uncompressed: datacontract-cli
    # reads only plain `.json` files from a local server, and a text file is
    # something a reviewer can read.
    path = SAMPLES / stream / f"{DAY.date().isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(records.line(row) for row in rows).encode("utf-8")
    path.write_bytes(content)
    print(f"{path.relative_to(ROOT)}: {len(rows)} records, {len(content)} bytes")


def main() -> None:
    write("cooler", cooler_rows())
    write("events", event_rows())


if __name__ == "__main__":
    main()

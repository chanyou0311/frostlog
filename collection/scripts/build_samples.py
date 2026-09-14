#!/usr/bin/env python3
"""Build the sample chunks the data contract is tested against in CI.

The samples are what one day's chunk of each stream looks like: real messages
captured from the cooler turned into v1 records, one connection's worth of them,
with the events that surround them. They come from the gateway's fixtures and are
decoded with the gateway's decoder, because that is where the cooler is spoken to;
the collector depends on neither outside this script.

Run `make samples` in collection/ after changing the record shape. The output is
deterministic, so a run that changes nothing leaves the files untouched in git.
"""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

COLLECTION = Path(__file__).resolve().parent.parent
REPOSITORY = COLLECTION.parent
sys.path.insert(0, str(REPOSITORY / "gateway" / "tests"))

import captured  # noqa: E402
from frostlog_gateway.commands import Command  # noqa: E402
from frostlog_gateway.decoder import decode_state  # noqa: E402
from frostlog_gateway.protocol import CbcCipher, parse_frame  # noqa: E402

from frostlog import records  # noqa: E402

SAMPLES = REPOSITORY / "contracts" / "samples" / "collection"
DAY = datetime(2026, 9, 6, 11, 2, 10, 719777, tzinfo=UTC)
UPTIME = 867.1
MODEL = "everfrost"
STATE_REPORT_COUNT = 48

#: The two commands in the sample day. A command_id is a UUID4 per request; these
#: are fixed so that a run that changes nothing leaves the files untouched.
ARRIVED = "0f0b6a4c-8f8b-4a1f-9a1e-2c2a6d3f5b71"
LEFT = "7c4a6f2d-3b19-4e8a-8f0c-51d2e9a7b4c3"

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


def setpoint_frame(celsius: int, seconds: float) -> str:
    """The frame a setpoint command is written as, encrypted with the captured session key.

    Built here rather than quoted so that the sample shows the bytes the gateway
    really sends; AES-CBC with the session's key and IV makes it the same bytes
    every run.
    """
    cipher = CbcCipher(bytes.fromhex(captured.SECRET))
    timestamp = int((DAY + timedelta(seconds=seconds)).timestamp())
    return Command(setting="setpoint_celsius", celsius=celsius).frame(cipher, "C", timestamp).hex()


def event_rows() -> list[records.Event]:
    end = 9.2 + (STATE_REPORT_COUNT + 1) * 3.0
    return [
        # Every run of the collector starts here: it found the gateway's stream.
        records.Event(**at(-310.0), kind="gateway_connected"),
        # An earlier connection, from before anything could be written to the cooler:
        # its ble_negotiated still carries the session key. The column is deprecated
        # and no connection since has one, but a day of old rows still shows it, and a
        # consumer that meets one should find it described.
        records.Event(
            **at(-305.0), kind="ble_connected", address=captured.ADDRESS, name=captured.NAME
        ),
        records.Event(
            **at(-304.1),
            kind="ble_negotiated",
            variant="solix",
            mtu=253,
            chip=captured.CHIP,
            firmware=captured.FIRMWARE,
            serial=captured.SERIAL_NUMBER,
            secret=captured.SECRET,
        ),
        records.Event(**at(-300.0), kind="ble_disconnected", address=captured.ADDRESS),
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
        # One command, step by step: asked for, written, acknowledged by the cooler,
        # then seen in a state report.
        records.Event(
            **at(130.0),
            kind="command_requested",
            command_id=ARRIVED,
            source="controller",
            reason="arrived_home",
            setting="setpoint_celsius",
            value="20",
            address=captured.ADDRESS,
        ),
        records.Event(
            **at(130.1),
            kind="command_sent",
            command_id=ARRIVED,
            cmd="4080",
            frames=[setpoint_frame(20, 130.1)],
            address=captured.ADDRESS,
        ),
        records.Event(**at(130.2), kind="command_accepted", command_id=ARRIVED),
        records.Event(**at(132.6), kind="command_applied", command_id=ARRIVED),
        # What the collector saw of the stream it was reading: events the gateway threw
        # away because this reader was behind, and a jump in the numbering it could not
        # account for. Neither is common; both are rows rather than silence.
        records.Event(**at(140.0), kind="gateway_dropped", count=23),
        records.Event(**at(150.0), kind="gateway_gap", expected=8841, received=8844),
        records.Event(**at(end), kind="ble_silent", address=captured.ADDRESS),
        records.Event(**at(end + 0.2), kind="ble_disconnected", address=captured.ADDRESS),
        records.Event(**at(end + 15.0), kind="ble_device_not_found", address=captured.ADDRESS),
        # And one that never reached the cooler: the link was gone when it was asked for.
        records.Event(
            **at(end + 20.0),
            kind="command_requested",
            command_id=LEFT,
            source="controller",
            reason="left_home",
            setting="setpoint_celsius",
            value="-20",
            address=captured.ADDRESS,
        ),
        records.Event(
            **at(end + 20.1), kind="command_rejected", command_id=LEFT, error="not_connected"
        ),
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
        # The gateway was restarted. Its sockets went with it, so the collector waited
        # and looked again; nothing of the cooler was recorded in between.
        records.Event(
            **at(end + 200.0),
            kind="gateway_disconnected",
            error="[Errno 2] No such file or directory",
        ),
        records.Event(**at(end + 212.0), kind="gateway_connected"),
    ]


def write(stream: str, rows: list[records.Cooler] | list[records.Event]) -> None:
    # One JSON record per line, as in a chunk, but uncompressed: datacontract-cli
    # reads only plain `.json` files from a local server, and a text file is
    # something a reviewer can read.
    path = SAMPLES / stream / f"{DAY.date().isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(records.line(row) for row in rows).encode("utf-8")
    path.write_bytes(content)
    print(f"{path.relative_to(REPOSITORY)}: {len(rows)} records, {len(content)} bytes")


def main() -> None:
    write("cooler", cooler_rows())
    write("events", event_rows())


if __name__ == "__main__":
    main()

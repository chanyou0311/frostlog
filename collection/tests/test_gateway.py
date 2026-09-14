"""The subscriber against a stand-in gateway on a real socket.

What is worth checking is the seam: that a record carries the gateway's stamp and
not one of its own, that the sensor is read for each message, that a hole in the
numbering becomes a row, and that a gateway which is not running is waited for.
"""

import asyncio
import json
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from frostlog import records
from frostlog.ambient.base import Reading, Sensor, SensorError
from frostlog.ambient.sampler import EnvironmentSampler
from frostlog.gateway import EVENTS_SOCKET, GatewaySubscriber, events_socket

STAMP: dict[str, Any] = {
    "ts": "2026-09-14T08:42:33.123456Z",
    "uptime_seconds": 34047.018,
    "boot_id": "8990f9bd-9c4f-4b3c-9a2e-2b1c6a4f0d11",
    "ts_synced": True,
}

MESSAGE: dict[str, Any] = {
    "seq": 1,
    "connection": 7,
    **STAMP,
    "kind": "message",
    "model": "everfrost",
    "address": "F4:9D:8A:C3:4F:84",
    "pattern": "03010f",
    "cmd": "4402",
    "frames": ["ff09"],
    "plain": "a1012b",
    "payload": {
        "setpoint_celsius": -20,
        "interior_temperature_celsius": -18,
        "display_unit": "C",
        "input_watts": 0,
        "usb_a_output_watts": 0,
        "usb_c_output_watts": 0,
        "charge_watts": 0,
        "discharge_watts": 42,
        "battery_state": "discharging",
        "state_of_charge_percent": 63,
        "protection_level": "M",
        "brightness": "mid",
        "serial_number": "ANSDHW40F15400347",
    },
}


@pytest.fixture
def socket_dir() -> Iterator[Path]:
    """A directory to put a socket in, short enough to bind.

    Not ``tmp_path``: an AF_UNIX path is about a hundred bytes at most, and pytest's
    directory carries the name of the test, which spends them.
    """
    with tempfile.TemporaryDirectory(prefix="frostlog-") as directory:
        yield Path(directory)


class _Sensor(Sensor):
    """The DHT20's place in the loop, without a bus."""

    name = "dht20"
    default_address = 0x38

    def __init__(self, fail: bool = False) -> None:
        self.reads = 0
        self.fail = fail

    def read(self) -> Reading:
        self.reads += 1
        if self.fail:
            raise SensorError("DHT20: I2C error")
        return Reading(temperature_celsius=25.2, humidity_percent=59.0)


async def _serve(path: Path, events: list[dict[str, Any]]) -> asyncio.Server:
    """A gateway that says its piece to whoever connects, then hangs up."""

    async def client(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        for event in events:
            writer.write(json.dumps(event).encode() + b"\n")
        await writer.drain()
        writer.close()

    return await asyncio.start_unix_server(client, path)


async def _collect(
    path: Path, events: list[dict[str, Any]], expected: int, **kwargs: Any
) -> list[records.Record]:
    """Run the subscriber until it has written ``expected`` records."""
    out: list[records.Record] = []
    stop = asyncio.Event()

    def sink(record: records.Cooler | records.Event) -> None:
        out.append(record)
        if len(out) >= expected:
            stop.set()

    server = await _serve(path, events)
    subscriber = GatewaySubscriber(
        sink, path, reconnect_delay=0.01, reconnect_delay_max=0.01, **kwargs
    )
    try:
        await asyncio.wait_for(subscriber.run(stop), timeout=10.0)
    finally:
        server.close()
        await server.wait_closed()
    return out


def _run(path: Path, events: list[dict[str, Any]], expected: int, **kwargs: Any) -> list[Any]:
    return asyncio.run(_collect(path, events, expected, **kwargs))


def _kinds(rows: list[Any]) -> list[str]:
    return [row.kind for row in rows if isinstance(row, records.Event)]


def test_a_message_keeps_the_stamp_it_arrived_with(socket_dir: Path) -> None:
    rows = _run(socket_dir / EVENTS_SOCKET, [MESSAGE], 2)

    message = next(row for row in rows if isinstance(row, records.Cooler))
    assert message.uptime_seconds == STAMP["uptime_seconds"]
    assert message.boot_id == STAMP["boot_id"]
    assert message.ts_synced is True
    assert message.ts.isoformat() == "2026-09-14T08:42:33.123456+00:00"
    assert message.model == "everfrost" and message.address == MESSAGE["address"]
    assert message.frames == ["ff09"] and message.plain == "a1012b"
    assert message.payload and message.payload.state_of_charge_percent == 63
    # The numbering is how the stream is read, not something a row carries.
    assert "seq" not in message.model_dump() and "connection" not in message.model_dump()


def test_the_air_is_measured_once_for_every_message(socket_dir: Path) -> None:
    sensor = _Sensor()
    sampler = EnvironmentSampler(sensor, lambda _event: None)
    events = [dict(MESSAGE, seq=seq) for seq in (1, 2, 3)]

    rows = _run(socket_dir / EVENTS_SOCKET, events, 4, environment=sampler)

    messages = [row for row in rows if isinstance(row, records.Cooler)]
    assert sensor.reads == len(messages) == 3
    assert all(row.environment and row.environment.temperature_celsius == 25.2 for row in messages)


def test_a_message_whose_payload_is_not_what_the_contract_says_keeps_its_bytes(
    socket_dir: Path,
) -> None:
    broken = dict(MESSAGE, payload=dict(MESSAGE["payload"], battery_state="melting"))

    rows = _run(socket_dir / EVENTS_SOCKET, [broken], 2)

    message = next(row for row in rows if isinstance(row, records.Cooler))
    assert message.payload is None
    assert message.frames == ["ff09"] and message.plain == "a1012b"


def test_the_gateways_own_events_are_recorded_as_they_came(socket_dir: Path) -> None:
    events = [
        {
            "seq": 1,
            "connection": 0,
            **STAMP,
            "kind": "ble_connected",
            "address": "AA:BB",
            "name": "cooler",
        },
        {
            "seq": 2,
            "connection": 1,
            **STAMP,
            "kind": "command_requested",
            "command_id": "c-1",
            "source": "controller",
            "reason": "arrived_home",
            "setting": "setpoint_celsius",
            "value": "20",
            "address": "AA:BB",
        },
        {
            "seq": 3,
            "connection": 1,
            **STAMP,
            "kind": "command_sent",
            "command_id": "c-1",
            "cmd": "4080",
            "frames": ["ff09"],
            "address": "AA:BB",
        },
    ]

    rows = _run(socket_dir / EVENTS_SOCKET, events, 4)

    assert _kinds(rows) == [
        "gateway_connected",
        "ble_connected",
        "command_requested",
        "command_sent",
    ]
    sent = rows[-1].model_dump()
    assert sent["command_id"] == "c-1" and sent["cmd"] == "4080" and sent["frames"] == ["ff09"]
    assert sent["uptime_seconds"] == STAMP["uptime_seconds"]
    assert "seq" not in sent and "connection" not in sent


def test_events_this_subscriber_lost_are_counted_not_guessed(socket_dir: Path) -> None:
    events = [
        dict(MESSAGE, seq=1),
        {"seq": 40, "connection": 0, **STAMP, "kind": "dropped", "count": 39},
        dict(MESSAGE, seq=41),
    ]

    rows = _run(socket_dir / EVENTS_SOCKET, events, 4)

    assert _kinds(rows) == ["gateway_connected", "gateway_dropped"]
    dropped = next(
        row for row in rows if isinstance(row, records.Event) and row.kind == "gateway_dropped"
    )
    assert dropped.model_dump()["count"] == 39
    # The dropped event accounts for the numbers up to its own; nothing is a gap.
    assert len([row for row in rows if isinstance(row, records.Cooler)]) == 2


def test_a_hole_in_the_numbering_is_a_row(socket_dir: Path) -> None:
    events = [dict(MESSAGE, seq=1), dict(MESSAGE, seq=5)]

    rows = _run(socket_dir / EVENTS_SOCKET, events, 4)

    gap = next(row for row in rows if isinstance(row, records.Event) and row.kind == "gateway_gap")
    written = gap.model_dump()
    assert written["expected"] == 2 and written["received"] == 5
    assert written["uptime_seconds"] == STAMP["uptime_seconds"]


def test_a_gateway_that_is_not_running_is_waited_for(socket_dir: Path) -> None:
    """Stopping the gateway takes the whole runtime directory, not only the socket."""

    async def run() -> list[records.Record]:
        out: list[records.Record] = []
        stop = asyncio.Event()
        appeared = asyncio.Event()

        def sink(record: records.Cooler | records.Event) -> None:
            out.append(record)
            if isinstance(record, records.Cooler):
                stop.set()

        missing = socket_dir / "gone" / EVENTS_SOCKET
        subscriber = GatewaySubscriber(
            sink, missing, reconnect_delay=0.01, reconnect_delay_max=0.01
        )
        running = asyncio.ensure_future(subscriber.run(stop))

        async def start_the_gateway() -> asyncio.Server:
            await asyncio.sleep(0.05)
            missing.parent.mkdir()
            server = await _serve(missing, [MESSAGE])
            appeared.set()
            return server

        server = await asyncio.wait_for(start_the_gateway(), timeout=10.0)
        try:
            await asyncio.wait_for(running, timeout=10.0)
        finally:
            server.close()
            await server.wait_closed()
        assert appeared.is_set()
        return out

    rows = asyncio.run(run())

    # Said once however long it stays away, and again only when it comes back.
    assert _kinds(rows) == ["gateway_disconnected", "gateway_connected"]
    down = rows[0].model_dump()
    assert down["error"]  # why the socket could not be opened
    assert down["boot_id"]  # stamped here: nobody else was there to stamp it


def test_the_socket_is_where_the_units_agree_it_is(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    assert events_socket(None) == Path("/run/user/1000/frostlog/events.sock")
    assert events_socket(Path("/tmp/sockets")) == Path("/tmp/sockets/events.sock")
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    with pytest.raises(ValueError):
        events_socket(None)

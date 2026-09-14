"""The event stream over a real socket: what a subscriber sees, and what it loses."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from frostlog_gateway.stream import EventStream, serve


@asynccontextmanager
async def _running(stream: EventStream, path: Path) -> AsyncIterator[list[asyncio.StreamWriter]]:
    """The server, and the readers to hang up at the end of the test."""
    server = await serve(stream, path)
    clients: list[asyncio.StreamWriter] = []
    try:
        yield clients
    finally:
        for client in clients:
            client.close()
        server.close()
        await server.wait_closed()


async def _reader(
    stream: EventStream, path: Path, clients: list[asyncio.StreamWriter]
) -> asyncio.StreamReader:
    """Connect, and wait until the gateway has this reader on its list."""
    was = stream.subscribers
    reader, writer = await asyncio.open_unix_connection(path)
    clients.append(writer)
    for _ in range(500):
        if stream.subscribers > was:
            return reader
        await asyncio.sleep(0.01)
    raise AssertionError("the server never subscribed the reader")


async def _read(reader: asyncio.StreamReader, count: int) -> list[dict[str, Any]]:
    return [
        json.loads(await asyncio.wait_for(reader.readline(), timeout=5.0)) for _ in range(count)
    ]


def test_every_subscriber_sees_the_same_events_with_the_same_numbers(socket_dir: Path) -> None:
    async def run() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        stream = EventStream()
        path = socket_dir / "events.sock"
        async with _running(stream, path) as clients:
            first = await _reader(stream, path, clients)
            second = await _reader(stream, path, clients)
            stream.emit("ble_connected", connection=1, address="AA:BB", name="cooler")
            stream.emit("message", connection=1, model="everfrost", frames=["ff09"], plain=None)
            return await _read(first, 2), await _read(second, 2)

    one, two = asyncio.run(run())
    assert one == two
    assert [event["seq"] for event in one] == [1, 2]
    assert one[0]["kind"] == "ble_connected" and one[0]["connection"] == 1
    assert set(one[0]) >= {"seq", "connection", "ts", "uptime_seconds", "boot_id", "ts_synced"}
    assert one[0]["ts"].endswith("Z")
    assert "plain" not in one[1]  # an absent field is left out, never sent as null


def test_a_reader_that_falls_behind_is_told_what_it_lost(socket_dir: Path) -> None:
    async def run() -> list[dict[str, Any]]:
        stream = EventStream(queue_limit=2)
        path = socket_dir / "events.sock"
        async with _running(stream, path) as clients:
            reader = await _reader(stream, path, clients)
            # Nothing yields to the writing task while these are emitted, which is how
            # a reader that has stopped reading makes the queue overflow.
            for index in range(5):
                stream.emit("message", connection=1, model="everfrost", frames=[f"{index:02x}"])
            return await _read(reader, 3)

    dropped, *kept = asyncio.run(run())
    assert dropped["kind"] == "dropped" and dropped["count"] == 3
    assert dropped["connection"] == 0
    assert dropped["seq"] == 3  # the last one lost; the gap 3 -> 4 is what it explains
    assert [event["seq"] for event in kept] == [4, 5]


def test_a_stale_socket_file_is_replaced(socket_dir: Path) -> None:
    async def run() -> None:
        stream = EventStream()
        path = socket_dir / "events.sock"
        path.write_text("what a killed gateway left behind")
        async with _running(stream, path) as clients:
            await _reader(stream, path, clients)

    asyncio.run(run())


def test_the_stream_goes_on_when_a_reader_hangs_up(socket_dir: Path) -> None:
    async def run() -> dict[str, Any]:
        stream = EventStream()
        path = socket_dir / "events.sock"
        async with _running(stream, path) as clients:
            await _reader(stream, path, clients)
            staying = await _reader(stream, path, clients)
            clients[0].close()
            stream.emit("ble_disconnected", connection=1, address="AA:BB")
            return (await _read(staying, 1))[0]

    assert asyncio.run(run())["kind"] == "ble_disconnected"


def test_an_event_is_one_line_whatever_is_in_it(socket_dir: Path) -> None:
    async def run() -> bytes:
        stream = EventStream()
        path = socket_dir / "events.sock"
        async with _running(stream, path) as clients:
            reader = await _reader(stream, path, clients)
            stream.emit("ble_error", address="AA:BB", error="adapter hci0\nnot found")
            return await asyncio.wait_for(reader.readline(), timeout=5.0)

    line = asyncio.run(run())
    assert line.count(b"\n") == 1  # the newline in the error text is escaped, not sent
    assert json.loads(line)["error"] == "adapter hci0\nnot found"

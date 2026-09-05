"""The receiver against a scripted session: what gets recorded, and when it gives up."""

import asyncio
from collections import deque
from typing import Any, cast

import pytest

from frostlog import records
from frostlog.cooler.everfrost import ble, receiver
from frostlog.cooler.everfrost.protocol import DEFAULT_MTU, PATTERN_NEGOTIATION, build_frame

TELEMETRY = bytes.fromhex("03010f")


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def uptime(self) -> float:
        return self.now


class FakeSession:
    """Hands out scripted notifications; ``None`` entries are a second of silence."""

    def __init__(self, clock: FakeClock, script: list[bytes | None]) -> None:
        self.clock = clock
        self.script = deque(script)
        self.written: list[bytes] = []
        self.disconnected = asyncio.Event()
        self.dropped = 0
        self.address = "AA:BB"
        self.name = "cooler"

    async def write(self, data: bytes) -> None:
        self.written.append(data)

    async def next_notification(self, timeout: float) -> bytes | None:
        if not self.script:
            self.disconnected.set()
            return None
        data = self.script.popleft()
        if data is None:
            self.clock.now += timeout
        return data


def _run(
    monkeypatch: pytest.MonkeyPatch, script: list[bytes | None]
) -> tuple[list[Any], FakeSession]:
    clock = FakeClock()
    monkeypatch.setattr(receiver.clock, "uptime", clock.uptime)
    out: list[Any] = []
    session = FakeSession(clock, script)
    rx = receiver.EverfrostReceiver(out.append, address="AA:BB", negotiation_timeout=15.0)
    asyncio.run(rx._session(cast(ble.Session, session), asyncio.Event(), None))
    return out, session


def _fragment(index: int, total: int, data: bytes, fill: int | None = None) -> bytes:
    payload = bytes([index << 4 | total]) + data
    if fill is not None:
        payload += b"\0" * (fill - 10 - len(payload))
    return build_frame(TELEMETRY, b"\xc4\x05", payload)


def test_joined_message_records_every_notification_it_came_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _fragment(1, 2, b"aaa", fill=DEFAULT_MTU)
    stray = build_frame(TELEMETRY, b"\xc4\x05", b"")  # same pattern+cmd, no fragment header
    second = _fragment(2, 2, b"bbb")
    out, _ = _run(monkeypatch, [first, stray, second])
    cooler = [r.payload for r in out if isinstance(r, records.Cooler)]
    assert cooler[0]["error"] == "empty fragment"
    assert cooler[0]["frames"] == [first.hex(), stray.hex()]
    # The stray frame ended that message, so the second fragment stands on its own:
    # nothing claims the bytes of ``first`` without listing it.
    assert "error" not in cooler[1]
    assert cooler[1]["frames"] == [second.hex()] and cooler[1]["payload"] == b"\x22bbb".hex()


def test_negotiation_silence_tries_prime_then_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    out, session = _run(monkeypatch, [None] * 40)
    kinds = [r.kind for r in out if isinstance(r, records.Event)]
    assert kinds == ["ble_handshake_retry", "ble_negotiation_timeout"]
    assert [f[7:9].hex() for f in session.written] == ["0001", "4001"]
    assert session.script  # gave up before the script ran out: the caller reconnects


def test_other_messages_keep_the_session_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    chatter = build_frame(TELEMETRY, b"\x03\x00", b"\x00\xa1\x01\x05")
    out, _ = _run(monkeypatch, [chatter, None] * 30)
    assert not [r for r in out if isinstance(r, records.Event)]
    assert len([r for r in out if isinstance(r, records.Cooler)]) == 30


def test_stalled_handshake_ends_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    stage1 = build_frame(PATTERN_NEGOTIATION, b"\x08\x01", b"\x00\xa1\x01\x01")
    out, session = _run(monkeypatch, [stage1] + [None] * 40)
    kinds = [r.kind for r in out if isinstance(r, records.Event)]
    assert kinds == ["ble_negotiation_timeout"]
    assert [f[7:9].hex() for f in session.written] == ["0001", "0003"]


def test_scan_errors_are_recorded_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken_find_device(address: str, timeout: float) -> None:
        raise ble.BleakError("adapter hci0 not found")

    monkeypatch.setattr(ble, "find_device", broken_find_device)
    out: list[Any] = []
    rx = receiver.EverfrostReceiver(out.append, address="AA:BB", duration=0.01, reconnect_delay=0.0)
    asyncio.run(rx.run(asyncio.Event()))
    kinds = [r.kind for r in out]
    assert kinds[:2] == ["ble_error", "ble_device_not_found"]

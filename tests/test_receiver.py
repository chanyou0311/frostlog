"""The receiver against a scripted session: what gets recorded, and when it gives up."""

import asyncio
from collections import deque
from typing import Any, cast

import captured
import pytest

from frostlog import records
from frostlog.ambient.base import Reading, Sensor, SensorError
from frostlog.ambient.sampler import EnvironmentSampler
from frostlog.cooler.everfrost import ble, receiver
from frostlog.cooler.everfrost.protocol import DEFAULT_MTU, PATTERN_NEGOTIATION, build_frame

TELEMETRY = bytes.fromhex("03010f")


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def uptime(self) -> float:
        return self.now


class FakeSensor(Sensor):
    """A sensor that answers with the given readings; a ``None`` entry is a failed read."""

    name = "dht20"
    default_address = 0x38

    def __init__(self, readings: list[tuple[float, float] | None]) -> None:
        self.readings = deque(readings)
        self.reads = 0

    def read(self) -> Reading:
        self.reads += 1
        reading = self.readings.popleft() if self.readings else (25.0, 50.0)
        if reading is None:
            raise SensorError("DHT20: I2C error: [Errno 5] Input/output error")
        return Reading(temperature_celsius=reading[0], humidity_percent=reading[1])


class FakeSession:
    """Hands out scripted notifications; ``None`` entries are a second of silence."""

    def __init__(self, clock: FakeClock, script: list[bytes | None]) -> None:
        self.clock = clock
        self.script = deque(script)
        self.written: list[bytes] = []
        self.disconnected = asyncio.Event()
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
    monkeypatch: pytest.MonkeyPatch,
    script: list[bytes | None],
    environment: EnvironmentSampler | None = None,
    **options: Any,
) -> tuple[list[Any], FakeSession]:
    clock = FakeClock()
    monkeypatch.setattr(receiver.clock, "uptime", clock.uptime)
    monkeypatch.setattr("frostlog.ambient.sampler.clock.uptime", clock.uptime)
    out: list[Any] = []
    session = FakeSession(clock, script)
    rx = receiver.EverfrostReceiver(
        out.append,
        address="AA:BB",
        environment=environment,
        **{"negotiation_timeout": 15.0, **options},
    )
    asyncio.run(rx._session(cast(ble.Session, session), asyncio.Event(), None))
    return out, session


def _fragment(index: int, total: int, data: bytes, fill: int | None = None) -> bytes:
    payload = bytes([index << 4 | total]) + data
    if fill is not None:
        payload += b"\0" * (fill - 10 - len(payload))
    return build_frame(TELEMETRY, b"\xc4\x05", payload)


def _cooler(out: list[Any]) -> list[records.Cooler]:
    return [record for record in out if isinstance(record, records.Cooler)]


def _events(out: list[Any]) -> list[records.Event]:
    return [record for record in out if isinstance(record, records.Event)]


def test_joined_message_records_every_notification_it_came_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _fragment(1, 2, b"aaa", fill=DEFAULT_MTU)
    stray = build_frame(TELEMETRY, b"\xc4\x05", b"")  # same pattern+cmd, no fragment header
    second = _fragment(2, 2, b"bbb")
    out, _ = _run(monkeypatch, [first, stray, second])
    messages = _cooler(out)
    assert {record.address for record in messages} == {"AA:BB"}
    assert messages[0].error == "empty fragment"
    assert messages[0].frames == [first.hex(), stray.hex()]
    # The stray frame ended that message, so the second fragment stands on its own:
    # nothing claims the bytes of ``first`` without listing it.
    assert messages[1].error is None
    assert messages[1].frames == [second.hex()] and messages[1].payload is None


def test_a_message_before_the_session_is_encrypted_is_recorded_as_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = build_frame(TELEMETRY, b"\x44\x89", b"\x00\xa1\x01\x05")
    out, _ = _run(monkeypatch, [frame])
    message = _cooler(out)[0]
    assert message.pattern == "03010f" and message.cmd == "4489"
    assert message.plain == "00a10105"
    assert message.payload is None  # only a state report carries decoded values


def test_a_state_report_is_decoded_onto_the_record(monkeypatch: pytest.MonkeyPatch) -> None:
    plain = bytes.fromhex(captured.DISCHARGING.plain)
    out, _ = _run(monkeypatch, [build_frame(TELEMETRY, b"\x44\x02", plain)])
    message = _cooler(out)[0]
    assert message.payload is not None
    assert message.payload.state_of_charge_percent == 80
    assert message.payload.battery_state == "discharging"


def test_a_state_report_that_cannot_be_read_keeps_its_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out, _ = _run(monkeypatch, [build_frame(TELEMETRY, b"\x44\x02", b"\xa1\x01\x05")])
    message = _cooler(out)[0]
    assert message.payload is None and message.plain == "a10105"


def test_the_environment_is_measured_with_every_message(monkeypatch: pytest.MonkeyPatch) -> None:
    sensor = FakeSensor([(25.21, 58.99), (25.34, 58.61)])
    out: list[Any] = []
    sampler = EnvironmentSampler(sensor, out.append)
    frame = build_frame(TELEMETRY, b"\x44\x89", b"\x00\xa1\x01\x05")
    recorded, _ = _run(monkeypatch, [frame, frame], environment=sampler)
    messages = _cooler(recorded)
    assert sensor.reads == 2
    assert messages[0].environment is not None
    assert messages[0].environment.temperature_celsius == 25.21
    assert messages[1].environment is not None
    assert messages[1].environment.humidity_percent == 58.61


def test_a_sensor_that_fails_is_reported_once_a_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    sensor = FakeSensor([None, None, None])
    failures: list[records.Event] = []
    sampler = EnvironmentSampler(sensor, failures.append, report_interval=60.0)
    frame = build_frame(TELEMETRY, b"\x44\x89", b"\x00\xa1\x01\x05")
    # Two messages a second apart, then one after the reporting interval has passed.
    script: list[bytes | None] = [frame, frame, *[None] * 61, frame]
    out, _ = _run(monkeypatch, script, environment=sampler, negotiation_timeout=1000.0)
    messages = _cooler(out)
    assert [message.environment for message in messages] == [None, None, None]
    assert [event.kind for event in failures] == ["environment_read_failed"] * 2
    assert failures[0].model_dump()["sensor"] == "dht20"


def test_silence_from_a_connected_cooler_drops_the_link(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = build_frame(TELEMETRY, b"\x44\x89", b"\x00\xa1\x01\x05")
    out, session = _run(
        monkeypatch,
        [frame, *[None] * 100],
        negotiation_timeout=1000.0,
        silence_timeout=30.0,
    )
    assert [event.kind for event in _events(out)] == ["ble_silent"]
    assert _events(out)[0].model_dump()["address"] == "AA:BB"
    assert len(session.script) == 70  # it gave up at 30 s of silence, not at the end


def test_negotiation_silence_tries_prime_then_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    out, session = _run(monkeypatch, [None] * 40)
    kinds = [event.kind for event in _events(out)]
    assert kinds == ["ble_handshake_retry", "ble_negotiation_timeout"]
    assert [f[7:9].hex() for f in session.written] == ["0001", "4001"]
    assert session.script  # gave up before the script ran out: the caller reconnects


def test_other_messages_keep_the_session_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    chatter = build_frame(TELEMETRY, b"\x03\x00", b"\x00\xa1\x01\x05")
    out, _ = _run(monkeypatch, [chatter, None] * 30)
    assert not _events(out)
    assert len(_cooler(out)) == 30


def test_stalled_handshake_ends_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    stage1 = build_frame(PATTERN_NEGOTIATION, b"\x08\x01", b"\x00\xa1\x01\x01")
    out, session = _run(monkeypatch, [stage1] + [None] * 40)
    kinds = [event.kind for event in _events(out)]
    assert kinds == ["ble_negotiation_timeout"]
    assert [f[7:9].hex() for f in session.written] == ["0001", "0003"]


def test_scan_errors_are_recorded_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken_find_device(address: str, timeout: float) -> None:
        raise ble.BleakError("adapter hci0 not found")

    monkeypatch.setattr(ble, "find_device", broken_find_device)
    out: list[Any] = []
    rx = receiver.EverfrostReceiver(out.append, address="AA:BB", duration=0.01, reconnect_delay=0.0)
    asyncio.run(rx.run(asyncio.Event()))
    kinds = [record.kind for record in out]
    assert kinds[:2] == ["ble_error", "ble_device_not_found"]

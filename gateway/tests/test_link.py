"""The link against a scripted session: what it streams, what it writes, when it gives up."""

import asyncio
from collections import deque
from collections.abc import Callable, Coroutine
from typing import Any, cast

import captured
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from frostlog_gateway import ble, link
from frostlog_gateway.handshake import SOLIX_PRIVATE_KEY, SolixHandshake
from frostlog_gateway.protocol import (
    DEFAULT_MTU,
    PATTERN_NEGOTIATION,
    CbcCipher,
    Parameter,
    build_frame,
    build_parameters,
    parse_frame,
    parse_parameters,
    shared_secret,
)
from frostlog_gateway.stream import EventStream

TELEMETRY = bytes.fromhex("03010f")
SECRET = bytes.fromhex(captured.SECRET)

#: A step the test takes when the session has read everything before it in the script.
Step = Callable[[], Coroutine[Any, Any, None]]


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def uptime(self) -> float:
        return self.now


class FakeSession:
    """Hands out scripted notifications; ``None`` is a second of silence, a step is the
    test doing something at exactly that point in the session."""

    def __init__(self, clock: FakeClock, script: list[bytes | Step | None]) -> None:
        self.clock = clock
        self.script: deque[bytes | Step | None] = deque(script)
        self.written: list[bytes] = []
        self.disconnected = asyncio.Event()
        self.address = "AA:BB"
        self.name = "cooler"

    async def write(self, data: bytes) -> None:
        self.written.append(data)

    async def next_notification(self, timeout: float) -> bytes | None:
        while self.script:
            entry = self.script.popleft()
            if entry is None:
                self.clock.now += timeout
                return None
            if isinstance(entry, bytes):
                return entry
            await entry()
        self.disconnected.set()
        return None


class Cooler:
    """The other end: a session cipher, and the messages it would have sent."""

    def __init__(self) -> None:
        self.cipher = CbcCipher(SECRET)

    def state(self, cmd: str = "4402", **changes: Any) -> bytes:
        """A captured state report with a value or two changed, as the cooler sent it."""
        body = bytearray.fromhex(captured.DISCHARGING.plain)
        for key, value in changes.items():
            at = body.index(_PARAMETERS[key]) + 3
            body[at : at + 1] = _VALUES[key](value)
        return self.message(cmd, bytes(body))

    def message(self, cmd: str, plain: bytes) -> bytes:
        return build_frame(TELEMETRY, bytes.fromhex(cmd), self.cipher.encrypt(plain))


_PARAMETERS = {"setpoint_celsius": b"\xa6\x02\x01", "display_unit": b"\xb0\x02\x01"}
_VALUES: dict[str, Callable[[Any], bytes]] = {
    "setpoint_celsius": lambda value: int(value).to_bytes(1, "little", signed=True),
    "display_unit": lambda value: bytes([1 if value == "F" else 0]),
}

#: The reply that tells the cooler took a setpoint write (cmd 4080 with 0x0800 set).
ACK = "4880"


class Recording(EventStream):
    """The stream, plus everything it was ever given: a reader that never falls behind."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []

    def emit(self, kind: str, connection: int = 0, **fields: Any) -> dict[str, Any]:
        event = super().emit(kind, connection, **fields)
        self.events.append(event)
        return event


class Harness:
    """A link, the events it emitted, and the session it was given."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, **options: Any) -> None:
        self.clock = FakeClock()
        self.stream = Recording()
        self.outcome: list[dict[str, Any]] = []
        monkeypatch.setattr(link.clock, "uptime", self.clock.uptime)
        self.link = link.Link(
            self.stream, address="AA:BB", **{"negotiation_timeout": 15.0, **options}
        )

    def negotiated(self, monkeypatch: pytest.MonkeyPatch, cooler: Cooler) -> None:
        """Start the session already encrypted; the key exchange itself is tested apart."""

        def already_done() -> SolixHandshake:
            negotiation = SolixHandshake()
            negotiation.cipher = cooler.cipher
            negotiation.done = True
            return negotiation

        monkeypatch.setattr(link.handshake, "SolixHandshake", already_done)

    def ask(self, **request: Any) -> Step:
        async def step() -> None:
            self.outcome.append(await self.link.command(request))

        return step

    def asking(self, *requests: Any) -> Step:
        async def step() -> None:
            for request in requests:
                self.outcome.append(await self.link.command(request))

        return step

    def run(self, script: list[bytes | Step | None]) -> FakeSession:
        session = FakeSession(self.clock, script)
        asyncio.run(self.link._run_session(cast(ble.Session, session), asyncio.Event()))
        return session

    @property
    def events(self) -> list[dict[str, Any]]:
        return self.stream.events

    def kinds(self, prefix: str = "") -> list[str]:
        return [event["kind"] for event in self.events if event["kind"].startswith(prefix)]

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event["kind"] == kind]


def _fragment(index: int, total: int, data: bytes, fill: int | None = None) -> bytes:
    payload = bytes([index << 4 | total]) + data
    if fill is not None:
        payload += b"\0" * (fill - 10 - len(payload))
    return build_frame(TELEMETRY, b"\xc4\x05", payload)


def _written(session: FakeSession, cmd: str) -> list[bytes]:
    return [frame for frame in session.written if parse_frame(frame).cmd.hex() == cmd]


def _sent_parameters(session: FakeSession, cooler: Cooler, cmd: str) -> dict[int, Parameter]:
    plain, _ = cooler.cipher.decrypt(parse_frame(_written(session, cmd)[0]).payload)
    return parse_parameters(plain)[1]


# --- what the cooler says ----------------------------------------------------------


def test_joined_message_streams_every_notification_it_came_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _fragment(1, 2, b"aaa", fill=DEFAULT_MTU)
    stray = build_frame(TELEMETRY, b"\xc4\x05", b"")  # same pattern+cmd, no fragment header
    second = _fragment(2, 2, b"bbb")
    harness = Harness(monkeypatch)
    harness.run([first, stray, second])
    messages = harness.of("message")
    assert {event["address"] for event in messages} == {"AA:BB"}
    assert messages[0]["error"] == "empty fragment"
    assert messages[0]["frames"] == [first.hex(), stray.hex()]
    # The stray frame ended that message, so the second fragment stands on its own:
    # nothing claims the bytes of ``first`` without listing it.
    assert "error" not in messages[1]
    assert messages[1]["frames"] == [second.hex()] and "payload" not in messages[1]


def test_a_message_before_the_session_is_encrypted_is_streamed_as_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)
    harness.run([build_frame(TELEMETRY, b"\x44\x89", b"\x00\xa1\x01\x05")])
    message = harness.of("message")[0]
    assert message["pattern"] == "03010f" and message["cmd"] == "4489"
    assert message["plain"] == "00a10105" and message["model"] == "everfrost"
    assert "payload" not in message  # only a state report carries decoded values


def test_a_state_report_is_decoded_onto_the_event(monkeypatch: pytest.MonkeyPatch) -> None:
    plain = bytes.fromhex(captured.DISCHARGING.plain)
    harness = Harness(monkeypatch)
    harness.run([build_frame(TELEMETRY, b"\x44\x02", plain)])
    payload = harness.of("message")[0]["payload"]
    assert payload["state_of_charge_percent"] == 80
    assert payload["battery_state"] == "discharging"


def test_the_answer_to_a_state_request_is_read_with_the_same_decoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plain = bytes.fromhex(captured.DISCHARGING.plain)
    harness = Harness(monkeypatch)
    harness.run([build_frame(TELEMETRY, b"\x48\x40", plain)])
    message = harness.of("message")[0]
    assert message["cmd"] == "4840" and message["payload"]["setpoint_celsius"] == -20


def test_a_state_report_that_cannot_be_read_keeps_its_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(monkeypatch)
    harness.run([build_frame(TELEMETRY, b"\x44\x02", b"\xa1\x01\x05")])
    message = harness.of("message")[0]
    assert "payload" not in message and message["plain"] == "a10105"


def test_silence_from_a_connected_cooler_drops_the_link(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = build_frame(TELEMETRY, b"\x44\x89", b"\x00\xa1\x01\x05")
    harness = Harness(monkeypatch, negotiation_timeout=1000.0, silence_timeout=30.0)
    session = harness.run([frame, *[None] * 100])
    assert [event["address"] for event in harness.of("ble_silent")] == ["AA:BB"]
    assert len(session.script) == 70  # it gave up at 30 s of silence, not at the end


def test_negotiation_silence_tries_prime_then_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    session = harness.run([None] * 40)
    assert harness.kinds() == ["ble_handshake_retry", "ble_negotiation_timeout"]
    assert [f[7:9].hex() for f in session.written] == ["0001", "4001"]
    assert session.script  # gave up before the script ran out: the caller reconnects


def test_other_messages_keep_the_session_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    chatter = build_frame(TELEMETRY, b"\x03\x00", b"\x00\xa1\x01\x05")
    harness = Harness(monkeypatch)
    harness.run([chatter, None] * 30)
    assert harness.kinds() == ["message"] * 30  # nothing gave up


def test_stalled_handshake_ends_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    stage1 = build_frame(PATTERN_NEGOTIATION, b"\x08\x01", b"\x00\xa1\x01\x01")
    harness = Harness(monkeypatch)
    session = harness.run([stage1, *[None] * 40])
    assert harness.kinds("ble_") == ["ble_negotiation_timeout"]
    assert [f[7:9].hex() for f in session.written] == ["0001", "0003"]


def test_scan_errors_are_streamed_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    stop = asyncio.Event()

    async def broken_find_device(address: str, timeout: float) -> None:
        stop.set()  # one turn round the loop is enough
        raise ble.BleakError("adapter hci0 not found")

    monkeypatch.setattr(ble, "find_device", broken_find_device)
    harness = Harness(monkeypatch, reconnect_delay=0.0)
    asyncio.run(harness.link.run(stop))
    assert harness.kinds()[:2] == ["ble_error", "ble_device_not_found"]
    # Nothing that happens outside a connection belongs to one.
    assert all(event["connection"] == 0 for event in harness.events)


# --- the end of the handshake ------------------------------------------------------


def test_the_negotiated_session_asks_for_the_state_it_may_not_be_told(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The cooler's half of the key exchange, which the handshake finishes on.
    device = ec.generate_private_key(ec.SECP256R1())
    peer = device.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)[1:]
    cipher = CbcCipher(shared_secret(SOLIX_PRIVATE_KEY, peer))
    key_exchange = build_frame(
        PATTERN_NEGOTIATION, b"\x08\x21", build_parameters([Parameter(0xA1, None, peer)])
    )
    harness = Harness(monkeypatch)
    session = harness.run([key_exchange])
    negotiated = harness.of("ble_negotiated")[0]
    assert negotiated["variant"] == "solix" and negotiated["address"] == "AA:BB"
    assert "secret" not in negotiated  # the key to the cooler is not stream material
    # The report the cooler sends by itself may not come; the gateway asks.
    request = parse_frame(_written(session, "4040")[0])
    assert parse_parameters(cipher.decrypt(request.payload)[0])[1][0xA1].raw == b"\x21"


# --- commands ----------------------------------------------------------------------


def test_a_command_is_sent_acknowledged_and_seen_in_the_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cooler = Cooler()
    harness = Harness(monkeypatch, negotiation_timeout=1e9)
    harness.negotiated(monkeypatch, cooler)
    session = harness.run(
        [
            cooler.state(),
            harness.ask(setting="setpoint_celsius", value=4, source="test", reason="warmer"),
            cooler.message(ACK, b"\xa1\x01\x31"),
            cooler.state(setpoint_celsius=4),
        ]
    )
    assert harness.outcome[0]["status"] == "accepted"
    assert harness.kinds("command") == [
        "command_requested",
        "command_sent",
        "command_accepted",
        "command_applied",
    ]
    requested, sent = harness.of("command_requested")[0], harness.of("command_sent")[0]
    assert requested["setting"] == "setpoint_celsius" and requested["value"] == "4"
    assert (requested["source"], requested["reason"]) == ("test", "warmer")
    assert sent["cmd"] == "4080" and sent["frames"] == [_written(session, "4080")[0].hex()]
    assert len({event["command_id"] for event in harness.events if "command_id" in event}) == 1
    parameters = _sent_parameters(session, cooler, "4080")
    assert parameters[0xA1].raw == b"\x21"
    assert parameters[0xA3].raw == b"\x01\x04"  # type 0x01, then the value in °C


def test_a_setpoint_is_written_in_the_unit_the_cooler_displays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cooler = Cooler()
    harness = Harness(monkeypatch, negotiation_timeout=1e9)
    harness.negotiated(monkeypatch, cooler)
    session = harness.run(
        [
            cooler.state(display_unit="F", setpoint_celsius=-4),
            harness.ask(setting="setpoint_celsius", value=10, source="test", reason="warmer"),
        ]
    )
    # 10 °C is 50 °F, and the cooler takes the number in what it is displaying.
    assert _sent_parameters(session, cooler, "4080")[0xA3].raw == b"\x01\x32"


def test_a_command_the_cooler_ignores_is_called_unconfirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cooler = Cooler()
    harness = Harness(monkeypatch, negotiation_timeout=1e9, command_timeout=10.0)
    harness.negotiated(monkeypatch, cooler)
    harness.run(
        [
            cooler.state(),
            harness.ask(setting="setpoint_celsius", value=4, source="test", reason="warmer"),
            *[None] * 30,
        ]
    )
    assert harness.kinds("command") == [
        "command_requested",
        "command_sent",
        "command_unconfirmed",
    ]
    assert harness.of("command_unconfirmed")[0]["error"] == "no acknowledgement"


def test_an_acknowledged_command_that_never_shows_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cooler = Cooler()
    harness = Harness(monkeypatch, negotiation_timeout=1e9, command_timeout=10.0)
    harness.negotiated(monkeypatch, cooler)
    harness.run(
        [
            cooler.state(),
            harness.ask(setting="setpoint_celsius", value=4, source="test", reason="warmer"),
            cooler.message(ACK, b"\xa1\x01\x31"),
            *[None] * 30,
        ]
    )
    assert harness.kinds("command")[-2:] == ["command_accepted", "command_unconfirmed"]
    assert "acknowledged" in harness.of("command_unconfirmed")[0]["error"]


def test_one_command_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    cooler = Cooler()
    harness = Harness(monkeypatch, negotiation_timeout=1e9)
    request = {"setting": "setpoint_celsius", "value": 4, "source": "test", "reason": "warmer"}
    harness.negotiated(monkeypatch, cooler)
    harness.run([cooler.state(), harness.asking(request, request)])
    assert [result["status"] for result in harness.outcome] == ["accepted", "rejected"]
    assert harness.outcome[1]["error"] == "busy"
    assert harness.of("command_rejected")[0]["error"] == "busy"


def test_what_the_gateway_will_not_write(monkeypatch: pytest.MonkeyPatch) -> None:
    cooler = Cooler()
    harness = Harness(monkeypatch, negotiation_timeout=1e9)
    harness.negotiated(monkeypatch, cooler)
    session = harness.run(
        [
            cooler.state(),
            harness.asking(
                "not an object",
                {"setting": "setpoint_celsius", "value": 4},  # nobody said who or why
                {"setting": "brightness", "value": 1, "source": "t", "reason": "r"},
                {"setting": "setpoint_celsius", "value": 40, "source": "t", "reason": "r"},
                {"setting": "setpoint_celsius", "value": "warm", "source": "t", "reason": "r"},
            ),
        ]
    )
    assert [result["error"] for result in harness.outcome] == [
        "invalid_request",
        "invalid_request",
        "unknown_setting",
        "value_out_of_range",
        "invalid_request",
    ]
    assert not _written(session, "4080")  # nothing reached the cooler
    # Even a refused request is on the stream, with whatever was asked for.
    assert len(harness.of("command_requested")) == 5
    assert harness.of("command_requested")[2]["setting"] == "brightness"


def test_nothing_is_written_before_the_cooler_has_said_what_it_shows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cooler = Cooler()
    harness = Harness(monkeypatch, negotiation_timeout=1e9)
    harness.negotiated(monkeypatch, cooler)
    harness.run([harness.ask(setting="setpoint_celsius", value=4, source="t", reason="r")])
    assert harness.outcome[0]["error"] == "state_unknown"


def test_a_command_with_no_link_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    asyncio.run(harness.ask(setting="setpoint_celsius", value=4, source="t", reason="r")())
    assert harness.outcome[0]["error"] == "not_connected"


def test_a_command_during_the_handshake_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    harness.run([harness.ask(setting="setpoint_celsius", value=4, source="t", reason="r")])
    assert harness.outcome[0]["error"] == "negotiating"

"""Hold the connection to the cooler: stream what it says, carry out what is asked.

One connection at a time, reconnected for as long as the gateway runs. Every
notification becomes one ``message`` event (fragments are joined first) carrying
the bytes as they arrived, the body in the clear where it could be read, and the
decoded values where the layout is known. Connection and negotiation milestones
are ``ble_*`` events. The session secret is not among them: a stream that anyone
on the Pi can read is no place for the key to the cooler.

A cooler that stops sending without dropping the link would look like a cooler
that is doing nothing; after a while of silence the link is dropped so that the
usual reconnect can prove the difference.

Commands are watched rather than assumed: one at a time, and the client is told
what the cooler did with it -- acknowledged, reflected in a state report, or
neither within the timeout -- on the stream, not in the reply it already got.
"""

import asyncio
import contextlib
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from frostlog_gateway import MODEL, ble, clock, commands, handshake
from frostlog_gateway.decoder import STATE_COMMANDS, decode_state_or_none
from frostlog_gateway.protocol import (
    PATTERN_NEGOTIATION,
    FragmentError,
    Frame,
    ProtocolError,
    Reassembler,
    parse_frame,
)
from frostlog_gateway.stream import EventStream

log = logging.getLogger(__name__)

#: Silence from a connected cooler that means the link is no longer worth keeping.
SILENCE_TIMEOUT = 900.0
#: How long a command has to be acknowledged and reflected before it is called unconfirmed.
COMMAND_TIMEOUT = 10.0
#: How long to give the cooler to answer a state request before asking again. Until a
#: state report has been read nothing can be written, however long the link stays up.
STATE_REQUEST_RETRY = 60.0


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), seconds)


@dataclass
class _Pending:
    """The command in flight, and what the cooler has said about it so far."""

    id: str
    command: commands.Command
    deadline: float
    accepted: bool = False


class Link:
    def __init__(
        self,
        stream: EventStream,
        address: str,
        scan_timeout: float = 10.0,
        reconnect_delay: float = 5.0,
        negotiation_timeout: float = 15.0,
        silence_timeout: float = SILENCE_TIMEOUT,
        command_timeout: float = COMMAND_TIMEOUT,
    ) -> None:
        self._stream = stream
        self._address = address
        self._scan_timeout = scan_timeout
        self._reconnect_delay = reconnect_delay
        self._negotiation_timeout = negotiation_timeout
        self._silence_timeout = silence_timeout
        self._command_timeout = command_timeout
        self._connections = 0
        self._session: ble.Session | None = None
        self._state: _SessionState | None = None
        self._pending: _Pending | None = None
        # One writer at a time: the session loop handling a notification, or a command
        # being written. Serialized, an acknowledgement cannot be handled while the
        # write it answers is still in flight, and the stream keeps the order sent,
        # accepted, applied.
        self._lock = asyncio.Lock()

    # --- the connection ---------------------------------------------------------

    async def run(self, stop: asyncio.Event) -> None:
        backoff = self._reconnect_delay
        reported_missing = False
        while not stop.is_set():
            try:
                device = await ble.find_device(self._address, timeout=self._scan_timeout)
            except (ble.BleakError, OSError, TimeoutError) as exc:
                # The adapter may not be up yet (boot) or may go away; keep trying.
                self._emit("ble_error", address=self._address, error=str(exc))
                device = None
            if device is None:
                if not reported_missing:
                    self._emit("ble_device_not_found", address=self._address)
                    reported_missing = True
                await _wait(stop, backoff)
                backoff = min(backoff * 2, 60.0)
                continue
            reported_missing = False
            backoff = self._reconnect_delay
            connected = False
            try:
                async with ble.Session(device) as session:
                    connected = True
                    self._connections += 1
                    self._session = session
                    self._emit("ble_connected", address=session.address, name=session.name)
                    await self._run_session(session, stop)
            except (ble.BleakError, OSError, TimeoutError) as exc:
                self._emit("ble_error", address=device.address, error=str(exc))
            if connected:
                self._emit("ble_disconnected", address=device.address)
            self._forget_session()
            await _wait(stop, self._reconnect_delay)

    def _forget_session(self) -> None:
        if self._pending is not None:
            self._unconfirmed("the link dropped")
        self._session = None
        self._state = None

    async def _run_session(self, session: ble.Session, stop: asyncio.Event) -> None:
        state = _SessionState(handshake.SolixHandshake())
        self._session, self._state = session, state
        last_seen = last_message = clock.uptime()
        for frame in state.handshake.start():
            await session.write(frame)
        while not stop.is_set() and not session.disconnected.is_set():
            self._expire_command()
            if state.state_wanted(clock.uptime()):
                await self._ask_state(session, state)
            data = await session.next_notification(timeout=1.0)
            if data is not None:
                last_seen = last_message = clock.uptime()
                async with self._lock:
                    await self._handle(data, session, state)
                continue
            if clock.uptime() - last_message >= self._silence_timeout:
                self._emit("ble_silent", address=session.address)
                break  # disconnect; the reconnect proves whether the cooler is still there
            if state.handshake.done or clock.uptime() - last_seen < self._negotiation_timeout:
                continue
            # The handshake is open and the device has said nothing for a while. A
            # device that keeps sending other messages is left alone: they are streamed
            # whether or not the negotiation ever completes.
            # An unanswered negotiation is tried again with the next variant (Prime).
            if state.negotiation_seen or not state.try_next_variant():
                self._emit("ble_negotiation_timeout", variant=state.handshake.variant)
                break  # disconnect; the next connection starts the negotiation afresh
            self._emit("ble_handshake_retry", variant=state.handshake.variant)
            for frame in state.handshake.start():
                await session.write(frame)
            last_seen = clock.uptime()
        for key, notifications in state.reassembler.pending().items():
            self._message(
                session.address,
                _hex(notifications),
                pattern=key[:3].hex(),
                cmd=key[3:].hex(),
                error="incomplete",
            )

    async def _handle(self, data: bytes, session: ble.Session, state: "_SessionState") -> None:
        try:
            frame = parse_frame(data)
        except ProtocolError as exc:
            self._message(session.address, [data.hex()], error=str(exc))
            return
        notifications = [data]
        if state.reassembler.is_fragment(frame, len(data), state.handshake.mtu):
            try:
                joined = state.reassembler.add(frame, data)
            except FragmentError as exc:
                self._message(
                    session.address, _hex(exc.notifications), **_header(frame), error=str(exc)
                )
                return
            if joined is None:
                return
            payload, notifications = joined
            frame = Frame(frame.pattern, frame.cmd, payload)
        info: dict[str, Any] = _header(frame)
        decoded = None
        plain = _plain(frame, state)
        if plain is not None:
            info["plain"] = plain.hex()
            if frame.cmd.hex() in STATE_COMMANDS:
                decoded = info["payload"] = decode_state_or_none(plain)
        self._message(session.address, _hex(notifications), **info)
        if decoded is not None:
            state.latest = decoded
        self._follow_command(frame.cmd.hex(), decoded)
        if frame.pattern == PATTERN_NEGOTIATION:
            await self._negotiate(frame, session, state)

    async def _negotiate(self, frame: Frame, session: ble.Session, state: "_SessionState") -> None:
        state.negotiation_seen = True
        was_done = state.handshake.done
        try:
            replies = state.handshake.handle(frame)
        except (handshake.HandshakeError, ProtocolError, ValueError) as exc:
            self._emit("ble_handshake_failed", cmd=frame.cmd.hex(), error=str(exc))
            return
        for reply in replies:
            await session.write(reply)
        if not state.handshake.done or was_done:
            return
        device = state.handshake.device
        self._emit(
            "ble_negotiated",
            address=session.address,
            variant=state.handshake.variant,
            mtu=state.handshake.mtu,
            chip=device.chip,
            firmware=device.firmware,
            serial=device.serial,
        )

    async def _ask_state(self, session: ble.Session, state: "_SessionState") -> None:
        """Ask for a state report; the answer comes as 4840."""
        assert state.handshake.cipher is not None
        state.state_asked_at = clock.uptime()
        await session.write(commands.state_request(state.handshake.cipher, int(time.time())))

    # --- commands ---------------------------------------------------------------

    async def command(self, request: Any) -> dict[str, Any]:
        """Carry out one request, or say why not. The outcome follows on the stream."""
        command_id = str(uuid.uuid4())
        self._emit(
            "command_requested", command_id=command_id, address=self._address, **_asked(request)
        )
        async with self._lock:
            try:
                command, session, cipher, unit = self._accept(request)
                frame = command.frame(cipher, unit, int(time.time()))
                # A write that hangs is a link that is gone, whatever bleak thinks; the
                # client is answered within the same bound it can expect for the outcome.
                await asyncio.wait_for(session.write(frame), self._command_timeout)
            except commands.Rejected as exc:
                self._emit("command_rejected", command_id=command_id, error=exc.reason)
                return {"command_id": command_id, "status": "rejected", "error": exc.reason}
            except (ble.BleakError, OSError, TimeoutError) as exc:
                # The link was there when it was checked and is not there now.
                log.warning("the command could not be written: %s", exc)
                self._emit("command_rejected", command_id=command_id, error="not_connected")
                return {"command_id": command_id, "status": "rejected", "error": "not_connected"}
            self._emit(
                "command_sent",
                command_id=command_id,
                cmd=commands.CMD_SETPOINT.hex(),
                frames=[frame.hex()],
                address=session.address,
            )
            if self._session is session:  # unless the link dropped during the write
                self._pending = _Pending(
                    id=command_id, command=command, deadline=clock.uptime() + self._command_timeout
                )
            else:
                self._emit("command_unconfirmed", command_id=command_id, error="the link dropped")
        return {"command_id": command_id, "status": "accepted"}

    def _accept(self, request: Any) -> tuple[commands.Command, ble.Session, handshake.Cipher, str]:
        """The command and what it takes to send it, or :class:`commands.Rejected`."""
        if not isinstance(request, dict):
            raise commands.Rejected("invalid_request")
        if not all(_named(request.get(field)) for field in ("source", "reason")):
            raise commands.Rejected("invalid_request")
        command = commands.parse(request.get("setting"), request.get("value"))
        session, state = self._session, self._state
        if session is None or state is None:
            raise commands.Rejected("not_connected")
        cipher = state.handshake.cipher
        if not state.handshake.done or cipher is None:
            raise commands.Rejected("negotiating")
        if state.latest is None:
            # The value is written in the unit the cooler is displaying, and only a
            # state report says which that is.
            raise commands.Rejected("state_unknown")
        if self._pending is not None:
            raise commands.Rejected("busy")
        return command, session, cipher, state.latest["display_unit"]

    def _follow_command(self, cmd: str, state: dict[str, Any] | None) -> None:
        pending = self._pending
        if pending is None:
            return
        if cmd == pending.command.ack_cmd and not pending.accepted:
            pending.accepted = True
            self._emit("command_accepted", command_id=pending.id)
        # A report showing the value proves the write only once the cooler has taken
        # it: asked for what is already set, an unrelated report would show it too.
        if pending.accepted and state is not None and pending.command.applied(state):
            self._emit("command_applied", command_id=pending.id)
            self._pending = None

    def _expire_command(self) -> None:
        pending = self._pending
        if pending is not None and clock.uptime() >= pending.deadline:
            self._unconfirmed(
                "acknowledged, but no state report showed the value"
                if pending.accepted
                else "no acknowledgement"
            )

    def _unconfirmed(self, why: str) -> None:
        assert self._pending is not None
        self._emit("command_unconfirmed", command_id=self._pending.id, error=why)
        self._pending = None

    # --- events -----------------------------------------------------------------

    def _emit(self, kind: str, **fields: Any) -> None:
        connection = self._connections if self._session is not None else 0
        self._stream.emit(kind, connection=connection, **fields)

    def _message(self, address: str, frames: list[str], **fields: Any) -> None:
        self._emit("message", model=MODEL, address=address, frames=frames, **fields)


def _named(value: Any) -> bool:
    """A request says who asked and why; both are short free text, and neither is optional."""
    return isinstance(value, str) and bool(value.strip())


def _asked(request: Any) -> dict[str, str]:
    """What the client asked for, as the ``command_requested`` event repeats it.

    Whatever was asked is repeated, including a request that is about to be refused:
    a rejection nobody can read the reason for is worse than no event at all.
    """
    if not isinstance(request, dict):
        return {}
    asked = {
        field: value
        for field in ("source", "reason", "setting")
        if isinstance(value := request.get(field), str) and value
    }
    if request.get("value") is not None:
        asked["value"] = str(request["value"])
    return asked


def _plain(frame: Frame, state: "_SessionState") -> bytes | None:
    """The message body in the clear, or ``None`` when it could not be read.

    Before the session is encrypted the body is sent as it is; afterwards it is
    decrypted, and a plaintext whose tag did not verify is not a plaintext.
    """
    cipher = state.handshake.cipher
    if cipher is None:
        return frame.payload
    try:
        plain, verified = cipher.decrypt(frame.payload)
    except (ProtocolError, ValueError):
        return None
    return plain if verified else None


def _header(frame: Frame) -> dict[str, Any]:
    return {"pattern": frame.pattern.hex(), "cmd": frame.cmd.hex()}


def _hex(notifications: list[bytes]) -> list[str]:
    return [n.hex() for n in notifications]


class _SessionState:
    def __init__(self, negotiation: handshake.SolixHandshake | handshake.PrimeHandshake) -> None:
        self.handshake = negotiation
        self.reassembler = Reassembler()
        self.negotiation_seen = False
        #: The last state report of this connection: the display unit to write in, and
        #: what a command is measured against. It does not outlive the connection.
        self.latest: dict[str, Any] | None = None
        #: When the state was last asked for, so that an unanswered request is repeated.
        self.state_asked_at: float | None = None
        self._untried: list[type[handshake.SolixHandshake | handshake.PrimeHandshake]] = [
            handshake.PrimeHandshake
        ]

    def state_wanted(self, now: float) -> bool:
        """Whether the state is still unknown and it is time to ask (again).

        Only the Solix dialect can be asked: the Prime handshake ends by asking for
        what it wants itself, and 4040 is not one of its commands.
        """
        if self.latest is not None or not self.handshake.done or self.handshake.variant != "solix":
            return False
        return self.state_asked_at is None or now - self.state_asked_at >= STATE_REQUEST_RETRY

    def try_next_variant(self) -> bool:
        """Continue the session with the next negotiation variant, if one is left;
        fragments in flight are kept."""
        if not self._untried:
            return False
        self.handshake = self._untried.pop(0)()
        self.negotiation_seen = False
        return True

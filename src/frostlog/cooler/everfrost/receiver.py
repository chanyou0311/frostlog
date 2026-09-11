"""Keep a connection to the EverFrost and record every message it sends.

Each notification becomes one ``cooler`` record (fragments are joined first).
The record keeps the raw notification bytes, the header fields, the message body
in the clear where it could be read, the decoded values of the message types
whose meaning is known, and the air around the Pi as it was measured at that
moment. The encrypted payload itself is not repeated: it is a pure function of
the notification bytes. Connection and negotiation milestones are ``event``
records. Nothing on the cooler is changed.

A cooler that stops sending without dropping the link would look like a cooler
that is doing nothing; after 15 minutes of silence the link is dropped so the
usual reconnect can prove the difference.
"""

import asyncio
import contextlib
import logging
from typing import Any

from frostlog import clock, records
from frostlog.ambient.sampler import EnvironmentSampler
from frostlog.cooler.base import Sink
from frostlog.cooler.everfrost import MODEL, ble, handshake
from frostlog.cooler.everfrost.decoder import CMD_STATE, decode_state
from frostlog.cooler.everfrost.protocol import (
    PATTERN_NEGOTIATION,
    FragmentError,
    Frame,
    ProtocolError,
    Reassembler,
    parse_frame,
)

log = logging.getLogger(__name__)

#: Silence from a connected cooler that means the link is no longer worth keeping.
SILENCE_TIMEOUT = 900.0


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), seconds)


class EverfrostReceiver:
    def __init__(
        self,
        sink: Sink,
        address: str,
        duration: float | None = None,
        environment: EnvironmentSampler | None = None,
        scan_timeout: float = 10.0,
        reconnect_delay: float = 5.0,
        negotiation_timeout: float = 15.0,
        silence_timeout: float = SILENCE_TIMEOUT,
    ) -> None:
        self._sink = sink
        self._address = address
        self._duration = duration
        self._environment = environment
        self._scan_timeout = scan_timeout
        self._reconnect_delay = reconnect_delay
        self._negotiation_timeout = negotiation_timeout
        self._silence_timeout = silence_timeout

    def _event(self, kind: str, **fields: Any) -> None:
        self._sink(records.event(kind, **fields))

    async def _message(self, address: str, frames: list[str], **fields: Any) -> None:
        environment = await self._environment.sample() if self._environment is not None else None
        self._sink(records.cooler(MODEL, address, frames, environment=environment, **fields))

    async def run(self, stop: asyncio.Event) -> None:
        deadline = clock.uptime() + self._duration if self._duration is not None else None
        backoff = self._reconnect_delay
        reported_missing = False
        while not stop.is_set() and (deadline is None or clock.uptime() < deadline):
            try:
                device = await ble.find_device(self._address, timeout=self._scan_timeout)
            except (ble.BleakError, OSError, TimeoutError) as exc:
                # The adapter may not be up yet (boot) or may go away; keep trying.
                self._event("ble_error", address=self._address, error=str(exc))
                device = None
            if device is None:
                if not reported_missing:
                    self._event("ble_device_not_found", address=self._address)
                    reported_missing = True
                await self._pause(stop, backoff, deadline)
                backoff = min(backoff * 2, 60.0)
                continue
            reported_missing = False
            backoff = self._reconnect_delay
            connected = False
            try:
                async with ble.Session(device) as session:
                    connected = True
                    self._event("ble_connected", address=session.address, name=session.name)
                    await self._session(session, stop, deadline)
            except (ble.BleakError, OSError, TimeoutError) as exc:
                self._event("ble_error", address=device.address, error=str(exc))
            if connected:
                self._event("ble_disconnected", address=device.address)
            await self._pause(stop, self._reconnect_delay, deadline)

    async def _pause(self, stop: asyncio.Event, seconds: float, deadline: float | None) -> None:
        if deadline is not None:
            seconds = min(seconds, max(0.0, deadline - clock.uptime()))
        await _wait(stop, seconds)

    async def _session(
        self, session: ble.Session, stop: asyncio.Event, deadline: float | None
    ) -> None:
        state = _SessionState(handshake.SolixHandshake())
        last_seen = last_message = clock.uptime()
        for frame in state.handshake.start():
            await session.write(frame)
        while (
            not stop.is_set()
            and not session.disconnected.is_set()
            and (deadline is None or clock.uptime() < deadline)
        ):
            data = await session.next_notification(timeout=1.0)
            if data is not None:
                last_seen = last_message = clock.uptime()
                await self._handle(data, session, state)
                continue
            if clock.uptime() - last_message >= self._silence_timeout:
                self._event("ble_silent", address=session.address)
                break  # disconnect; the reconnect proves whether the cooler is still there
            if state.handshake.done or clock.uptime() - last_seen < self._negotiation_timeout:
                continue
            # The handshake is open and the device has said nothing for a while. A
            # device that keeps sending other messages is left alone: they are
            # recorded whether or not the negotiation ever completes.
            # An unanswered negotiation is tried again with the next variant (Prime).
            if state.negotiation_seen or not state.try_next_variant():
                self._event("ble_negotiation_timeout", variant=state.handshake.variant)
                break  # disconnect; the next connection starts the negotiation afresh
            self._event("ble_handshake_retry", variant=state.handshake.variant)
            for frame in state.handshake.start():
                await session.write(frame)
            last_seen = clock.uptime()
        for key, notifications in state.reassembler.pending().items():
            await self._message(
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
            await self._message(session.address, [data.hex()], error=str(exc))
            return
        notifications = [data]
        if state.reassembler.is_fragment(frame, len(data), state.handshake.mtu):
            try:
                joined = state.reassembler.add(frame, data)
            except FragmentError as exc:
                await self._message(
                    session.address, _hex(exc.notifications), **_header(frame), error=str(exc)
                )
                return
            if joined is None:
                return
            payload, notifications = joined
            frame = Frame(frame.pattern, frame.cmd, payload)
        info: dict[str, Any] = _header(frame)
        plain = _plain(frame, state)
        if plain is not None:
            info["plain"] = plain.hex()
            if frame.cmd.hex() == CMD_STATE:
                info["payload"] = _decoded(plain)
        await self._message(session.address, _hex(notifications), **info)
        if frame.pattern == PATTERN_NEGOTIATION:
            await self._negotiate(frame, session, state)

    async def _negotiate(self, frame: Frame, session: ble.Session, state: "_SessionState") -> None:
        state.negotiation_seen = True
        was_done = state.handshake.done
        try:
            replies = state.handshake.handle(frame)
        except (handshake.HandshakeError, ProtocolError, ValueError) as exc:
            self._event("ble_handshake_failed", cmd=frame.cmd.hex(), error=str(exc))
            return
        for reply in replies:
            await session.write(reply)
        if state.handshake.done and not was_done:
            device = state.handshake.device
            self._event(
                "ble_negotiated",
                variant=state.handshake.variant,
                mtu=state.handshake.mtu,
                chip=device.chip,
                firmware=device.firmware,
                serial=device.serial,
                secret=state.handshake.secret.hex() if state.handshake.secret else None,
            )


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


def _decoded(plain: bytes) -> records.CoolerPayload | None:
    try:
        return decode_state(plain)
    except (ProtocolError, ValueError) as exc:
        # The bytes are kept; a decoder that learns to read them can be run over them later.
        log.warning("state report not decoded: %s", exc)
        return None


def _header(frame: Frame) -> dict[str, Any]:
    return {"pattern": frame.pattern.hex(), "cmd": frame.cmd.hex()}


def _hex(notifications: list[bytes]) -> list[str]:
    return [n.hex() for n in notifications]


class _SessionState:
    def __init__(self, negotiation: handshake.SolixHandshake | handshake.PrimeHandshake) -> None:
        self.handshake = negotiation
        self.reassembler = Reassembler()
        self.negotiation_seen = False
        self._untried: list[type[handshake.SolixHandshake | handshake.PrimeHandshake]] = [
            handshake.PrimeHandshake
        ]

    def try_next_variant(self) -> bool:
        """Continue the session with the next negotiation variant, if one is left;
        fragments in flight are kept."""
        if not self._untried:
            return False
        self.handshake = self._untried.pop(0)()
        self.negotiation_seen = False
        return True

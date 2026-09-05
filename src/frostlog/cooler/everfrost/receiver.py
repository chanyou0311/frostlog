"""Keep a connection to the EverFrost and record every message it sends, as is.

Each notification becomes one ``cooler`` record (fragments are joined first).
The record keeps the raw notification bytes, the header fields, and, once the
session is encrypted, the decrypted payload, so that a later decoder can work
from the files alone. Connection and negotiation milestones are ``event``
records. No meaning is assigned here and nothing on the cooler is changed.
"""

import asyncio
import contextlib
import logging
from typing import Any

from frostlog import clock, records
from frostlog.cooler.base import Sink
from frostlog.cooler.everfrost import MODEL, ble, handshake
from frostlog.cooler.everfrost.protocol import (
    PATTERN_NEGOTIATION,
    FragmentError,
    Frame,
    ProtocolError,
    Reassembler,
    parse_frame,
)

log = logging.getLogger(__name__)


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), seconds)


class EverfrostReceiver:
    def __init__(
        self,
        sink: Sink,
        address: str,
        duration: float | None = None,
        scan_timeout: float = 10.0,
        reconnect_delay: float = 5.0,
        negotiation_timeout: float = 15.0,
    ) -> None:
        self._sink = sink
        self._address = address
        self._duration = duration
        self._scan_timeout = scan_timeout
        self._reconnect_delay = reconnect_delay
        self._negotiation_timeout = negotiation_timeout

    def _event(self, kind: str, **fields: Any) -> None:
        self._sink(records.event(kind, **fields))

    def _message(self, payload: dict[str, Any]) -> None:
        self._sink(records.cooler(MODEL, payload))

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
        last_seen = clock.uptime()
        for frame in state.handshake.start():
            await session.write(frame)
        while (
            not stop.is_set()
            and not session.disconnected.is_set()
            and (deadline is None or clock.uptime() < deadline)
        ):
            data = await session.next_notification(timeout=1.0)
            if data is not None:
                last_seen = clock.uptime()
                await self._handle(data, session, state)
                continue
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
            self._message(
                {
                    "pattern": key[:3].hex(),
                    "cmd": key[3:].hex(),
                    "frames": _hex(notifications),
                    "error": "incomplete",
                }
            )
        if session.dropped:
            self._event("ble_notifications_dropped", count=session.dropped)

    async def _handle(self, data: bytes, session: ble.Session, state: "_SessionState") -> None:
        try:
            frame = parse_frame(data)
        except ProtocolError as exc:
            self._message({"frames": [data.hex()], "error": str(exc)})
            return
        notifications = [data]
        if state.reassembler.is_fragment(frame, len(data), state.handshake.mtu):
            try:
                joined = state.reassembler.add(frame, data)
            except FragmentError as exc:
                self._message(
                    _header(frame) | {"frames": _hex(exc.notifications), "error": str(exc)}
                )
                return
            if joined is None:
                return
            payload, notifications = joined
            frame = Frame(frame.pattern, frame.cmd, payload)
        info = _header(frame) | {"frames": _hex(notifications), "payload": frame.payload.hex()}
        cipher = state.handshake.cipher
        if cipher is not None:
            try:
                plain, verified = cipher.decrypt(frame.payload)
            except (ProtocolError, ValueError):
                pass
            else:
                info["plain"], info["plain_verified"] = plain.hex(), verified
        self._message(info)
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

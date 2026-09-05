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
from frostlog.cooler.everfrost import ble, handshake
from frostlog.cooler.everfrost.protocol import (
    PATTERN_NEGOTIATION,
    Frame,
    ProtocolError,
    Reassembler,
    parse_frame,
)

log = logging.getLogger(__name__)

MODEL = "everfrost"


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), seconds)


class EverfrostReceiver:
    def __init__(
        self,
        sink: Sink,
        address: str | None = None,
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
            device = await ble.find_device(self._address, timeout=self._scan_timeout)
            if device is None:
                if not reported_missing:
                    self._event("ble_device_not_found", address=self._address)
                    reported_missing = True
                await _wait(stop, backoff)
                backoff = min(backoff * 2, 60.0)
                continue
            reported_missing = False
            backoff = self._reconnect_delay
            try:
                async with ble.Session(device) as session:
                    self._event("ble_connected", address=session.address, name=session.name)
                    await self._session(session, stop, deadline)
            except (ble.BleakError, OSError, TimeoutError) as exc:
                self._event("ble_error", address=device.address, error=str(exc))
            self._event("ble_disconnected", address=device.address)
            await _wait(stop, self._reconnect_delay)

    async def _session(
        self, session: ble.Session, stop: asyncio.Event, deadline: float | None
    ) -> None:
        state = _SessionState(handshake.SolixHandshake())
        started = clock.uptime()
        for frame in state.handshake.start():
            await session.write(frame)
        while (
            not stop.is_set()
            and not session.disconnected.is_set()
            and (deadline is None or clock.uptime() < deadline)
        ):
            data = await session.next_notification(timeout=1.0)
            if data is None:
                silent = clock.uptime() - started > self._negotiation_timeout
                if silent and not state.negotiation_seen and not state.variant_switched:
                    # No answer to the Solix negotiation: try the Prime variant once.
                    state = _SessionState(handshake.PrimeHandshake(), variant_switched=True)
                    self._event("ble_handshake_retry", variant=state.handshake.variant)
                    for frame in state.handshake.start():
                        await session.write(frame)
                continue
            await self._handle(data, session, state)
        for key, frames in state.raw_fragments.items():
            self._message(
                {
                    "pattern": key[:3].hex(),
                    "cmd": key[3:].hex(),
                    "frames": frames,
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
        frames = [data.hex()]
        if state.reassembler.is_fragment(frame, len(data), state.handshake.mtu):
            frames = state.raw_fragments.setdefault(frame.key, [])
            frames.append(data.hex())
            try:
                payload = state.reassembler.add(frame)
            except ProtocolError as exc:
                del state.raw_fragments[frame.key]
                self._message(_header(frame) | {"frames": frames, "error": str(exc)})
                return
            if payload is None:
                return
            del state.raw_fragments[frame.key]
            frame = Frame(frame.pattern, frame.cmd, payload)
        info = _header(frame) | {"frames": frames, "payload": frame.payload.hex()}
        cipher = state.handshake.cipher
        if cipher is not None:
            try:
                plain, verified = cipher.decrypt(frame.payload)
            except (ProtocolError, ValueError):
                pass
            else:
                info["plain"] = plain.hex()
                if not verified:
                    info["plain_verified"] = False
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


class _SessionState:
    def __init__(
        self,
        negotiation: handshake.SolixHandshake | handshake.PrimeHandshake,
        variant_switched: bool = False,
    ) -> None:
        self.handshake = negotiation
        self.reassembler = Reassembler()
        self.raw_fragments: dict[bytes, list[str]] = {}
        self.negotiation_seen = False
        self.variant_switched = variant_switched

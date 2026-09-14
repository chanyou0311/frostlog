"""Subscribe to what the gateway hears from the cooler, and write it down.

The gateway is the one process that holds the cooler's Bluetooth link; this
product is one of its readers. Every event arrives already stamped with the
moment the notification was received, and a record keeps that stamp instead of
taking one of its own: a stamp taken here would move every message by however
long the queue and the socket happened to be, and that stamp is half of the row's
natural key.

The air around the cooler is measured here and now, though. It belongs to the
message -- how warm it was while the cooler drew the power it reported -- and the
sensor is on this side of the socket.

The stream can be lost in two ways and both are written down rather than papered
over. The gateway drops the oldest events of a reader that falls behind and says
how many; and a gateway that stops takes its sockets, and the runtime directory
they live in, with it, so a reader that finds nothing there waits and looks
again. Either way the hole is a row, and the numbering says how wide it was.
"""

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from frostlog import clock, records
from frostlog.ambient.sampler import EnvironmentSampler

log = logging.getLogger(__name__)

#: The gateway's event stream, in the socket directory every unit on the Pi shares.
EVENTS_SOCKET = "events.sock"
#: How long to wait before looking again for a gateway that is not there, and at most.
RECONNECT_DELAY = 1.0
RECONNECT_DELAY_MAX = 30.0

Sink = Callable[[records.Cooler | records.Event], None]

#: What every event carries to say where it sits in the stream. The stamp and the
#: kind become columns; the numbering is the gateway's own bookkeeping, and is
#: read here rather than recorded.
_ENVELOPE = frozenset({"seq", "connection", "ts", "uptime_seconds", "boot_id", "ts_synced", "kind"})
_STAMP = ("ts", "uptime_seconds", "boot_id", "ts_synced")
_MESSAGE = ("pattern", "cmd", "plain", "error")


def events_socket(configured: Path | None) -> Path:
    """Where the gateway is listening: the shared directory, or the runtime one."""
    if configured is None:
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        if not runtime:
            raise ValueError("set FROSTLOG_GATEWAY_SOCKET_DIR or XDG_RUNTIME_DIR")
        configured = Path(runtime) / "frostlog"
    return configured / EVENTS_SOCKET


class GatewaySubscriber:
    """Turns the gateway's stream into the records of the collection data contract."""

    def __init__(
        self,
        sink: Sink,
        socket: Path,
        environment: EnvironmentSampler | None = None,
        duration: float | None = None,
        reconnect_delay: float = RECONNECT_DELAY,
        reconnect_delay_max: float = RECONNECT_DELAY_MAX,
    ) -> None:
        self._sink = sink
        self._socket = socket
        self._environment = environment
        self._duration = duration
        self._reconnect_delay = reconnect_delay
        self._reconnect_delay_max = reconnect_delay_max
        self._next_seq: int | None = None
        self._reported_down = False

    async def run(self, stop: asyncio.Event) -> None:
        deadline = clock.uptime() + self._duration if self._duration is not None else None
        delay = self._reconnect_delay
        while self._running(stop, deadline):
            connection = await self._connect()
            if connection is not None:
                delay = self._reconnect_delay
                reader, writer = connection
                try:
                    await self._read(reader, stop, deadline)
                finally:
                    writer.close()
                    with contextlib.suppress(ConnectionError, OSError):
                        await writer.wait_closed()
                if not self._running(stop, deadline):
                    return  # this run is over, and the gateway is still there
                self._down()
            await self._pause(stop, delay, deadline)
            delay = min(delay * 2, self._reconnect_delay_max)

    def _running(self, stop: asyncio.Event, deadline: float | None) -> bool:
        return not stop.is_set() and (deadline is None or clock.uptime() < deadline)

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter] | None:
        """The stream, or nothing: a gateway that is not running has no socket, and
        while it is stopped there is no directory either."""
        try:
            connection = await asyncio.open_unix_connection(self._socket)
        except OSError as exc:
            self._down(str(exc))
            return None
        # The numbering starts over with every gateway, and what was missed while
        # this reader was away is the gap the disconnection already stands for.
        self._next_seq = None
        self._reported_down = False
        self._event("gateway_connected")
        return connection

    def _down(self, error: str | None = None) -> None:
        """That the stream is gone, said once however long it stays gone."""
        if self._reported_down:
            return
        self._reported_down = True
        self._event("gateway_disconnected", error=error)

    async def _pause(self, stop: asyncio.Event, seconds: float, deadline: float | None) -> None:
        if deadline is not None:
            seconds = min(seconds, max(0.0, deadline - clock.uptime()))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), seconds)

    async def _read(
        self, reader: asyncio.StreamReader, stop: asyncio.Event, deadline: float | None
    ) -> None:
        while self._running(stop, deadline):
            line = await self._line(reader, stop, deadline)
            if not line:
                return  # the gateway hung up, or this run is over
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                log.warning("a line on the stream that is not JSON: %s", exc)
                continue
            if isinstance(event, dict):
                await self._record(event)

    async def _line(
        self, reader: asyncio.StreamReader, stop: asyncio.Event, deadline: float | None
    ) -> bytes | None:
        """The next line, or nothing when the run ends before one arrives."""
        timeout = None if deadline is None else max(0.0, deadline - clock.uptime())
        line = asyncio.ensure_future(reader.readline())
        halt = asyncio.ensure_future(stop.wait())
        try:
            done, _ = await asyncio.wait(
                {line, halt}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            halt.cancel()
        if line not in done:
            line.cancel()
            return None
        return line.result()

    async def _record(self, event: dict[str, Any]) -> None:
        at = {field: event[field] for field in _STAMP if field in event}
        try:
            self._account_for(event, at)
            kind = event.get("kind")
            if kind == "message":
                await self._message(event, at)
            elif kind is not None and kind != "dropped":
                self._sink(records.event_at(at, kind, **_fields(event)))
        except (KeyError, TypeError, ValidationError) as exc:
            # Whatever this was, it was not an event of the shape the gateway serves.
            log.warning("an event that could not be recorded: %s", exc)

    def _account_for(self, event: dict[str, Any], at: dict[str, Any]) -> None:
        """Write down what the numbering says is missing, before what arrived."""
        seq = event.get("seq")
        if not isinstance(seq, int):
            return
        if event.get("kind") == "dropped":
            # The gateway spends no number on a reader's own loss: this event carries
            # the number and the stamp of the last event that reader lost, so the
            # stream is accounted for up to there.
            self._sink(records.event_at(at, "gateway_dropped", count=event.get("count")))
        elif self._next_seq is not None and seq != self._next_seq:
            self._sink(records.event_at(at, "gateway_gap", expected=self._next_seq, received=seq))
        self._next_seq = seq + 1

    async def _message(self, event: dict[str, Any], at: dict[str, Any]) -> None:
        fields: dict[str, Any] = {field: event[field] for field in _MESSAGE if field in event}
        payload = event.get("payload")
        if payload is not None:
            try:
                fields["payload"] = records.CoolerPayload.model_validate(payload)
            except ValidationError as exc:
                # The frames are the original and the row keeps them; a reading of
                # them that the contract does not describe is what is dropped.
                log.warning("a payload the contract does not describe: %s", exc)
        environment = await self._environment.sample() if self._environment is not None else None
        self._sink(
            records.cooler_at(
                at,
                event["model"],
                event["address"],
                event["frames"],
                environment=environment,
                **fields,
            )
        )

    def _event(self, kind: str, **fields: Any) -> None:
        """One of the collector's own events: nobody else was there to stamp it."""
        self._sink(records.event(kind, **fields))


def _fields(event: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if key not in _ENVELOPE}

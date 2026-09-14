"""The event stream: everything the gateway has to say, in the order it happened.

One JSON object per line, the same line to every subscriber. Events are numbered
process-wide so that a subscriber which lost some can see the gap, and stamped at
the moment they happened rather than at the moment they are read: a subscriber
that stamped them itself would move every message by however long its own queue
was.

Nobody is waited for. A subscriber that reads slower than the cooler talks loses
the oldest events it had not read, and is told how many -- a gateway that blocked
on one reader would stop holding the link for all of them.
"""

import asyncio
import json
import logging
from collections import deque
from pathlib import Path
from typing import Any

from frostlog_gateway import clock

log = logging.getLogger(__name__)

#: Events one subscriber may fall behind by before the oldest are dropped.
QUEUE_LIMIT = 1000


def stamp() -> dict[str, Any]:
    """When an event happened, as the Pi can tell it (see :mod:`frostlog_gateway.clock`)."""
    return {
        "ts": clock.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "uptime_seconds": clock.uptime(),
        "boot_id": clock.boot_id(),
        "ts_synced": clock.synced(),
    }


class Subscriber:
    """One reader's queue. Full means the oldest goes, not that the gateway waits."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        # Each entry is one event and the line it is sent as: encoded once by the
        # stream, however many readers there are.
        self._events: deque[tuple[dict[str, Any], bytes]] = deque()
        self._lost: dict[str, Any] | None = None
        self._lost_count = 0
        self._waiting = asyncio.Event()

    def put(self, event: dict[str, Any], encoded: bytes) -> None:
        if len(self._events) >= self._limit:
            self._lost, _ = self._events.popleft()
            self._lost_count += 1
        self._events.append((event, encoded))
        self._waiting.set()

    async def next(self) -> bytes:
        """The next line for this reader, waiting until there is one."""
        while not self._events and self._lost is None:
            self._waiting.clear()
            await self._waiting.wait()
        if self._lost is not None:
            return line(self._dropped())
        return self._events.popleft()[1]

    def _dropped(self) -> dict[str, Any]:
        """The stand-in for what this reader lost, handed to it before the next event.

        It carries the number and the stamp of the last event lost rather than a
        number of its own: the counter is process-wide, and spending a number on one
        reader would show up as a gap in every other reader's stream.
        """
        assert self._lost is not None
        lost, count = self._lost, self._lost_count
        self._lost, self._lost_count = None, 0
        return {
            "seq": lost["seq"],
            "connection": 0,
            "ts": lost["ts"],
            "uptime_seconds": lost["uptime_seconds"],
            "boot_id": lost["boot_id"],
            "ts_synced": lost["ts_synced"],
            "kind": "dropped",
            "count": count,
        }


class EventStream:
    """Numbers and stamps events, and hands them to whoever is listening."""

    def __init__(self, queue_limit: int = QUEUE_LIMIT) -> None:
        self._queue_limit = queue_limit
        self._seq = 0
        self._subscribers: set[Subscriber] = set()

    def emit(self, kind: str, connection: int = 0, **fields: Any) -> dict[str, Any]:
        """Stamp one event and broadcast it. Fields that are ``None`` are left out."""
        self._seq += 1
        event = {
            "seq": self._seq,
            "connection": connection,
            **stamp(),
            "kind": kind,
            **{key: value for key, value in fields.items() if value is not None},
        }
        encoded = line(event)
        for subscriber in self._subscribers:
            subscriber.put(event, encoded)
        log.debug("%s", event)
        return event

    @property
    def subscribers(self) -> int:
        """How many readers are listening right now."""
        return len(self._subscribers)

    def subscribe(self) -> Subscriber:
        subscriber = Subscriber(self._queue_limit)
        self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.discard(subscriber)


def line(event: dict[str, Any]) -> bytes:
    return json.dumps(event, separators=(",", ":")).encode() + b"\n"


async def serve(stream: EventStream, path: Path) -> asyncio.Server:
    """Listen on ``path`` and send every event to everyone connected there."""

    async def reader(inbound: asyncio.StreamReader, out: asyncio.StreamWriter) -> None:
        subscriber = stream.subscribe()
        # A reader that hangs up while the cooler is quiet writes nothing and reads
        # nothing, so the only sign of it leaving is the end of its own half of the
        # socket. Without watching for that, its queue would go on filling.
        hangup = asyncio.ensure_future(inbound.read())
        try:
            while True:
                event = asyncio.ensure_future(subscriber.next())
                done, _ = await asyncio.wait({event, hangup}, return_when=asyncio.FIRST_COMPLETED)
                if event not in done:
                    event.cancel()
                    break
                out.write(event.result())
                await out.drain()  # this is where a slow reader falls behind
        except (ConnectionError, OSError):
            pass
        finally:
            hangup.cancel()
            stream.unsubscribe(subscriber)
            out.close()

    path.unlink(missing_ok=True)  # a socket file left behind by a killed gateway
    return await asyncio.start_unix_server(reader, path)

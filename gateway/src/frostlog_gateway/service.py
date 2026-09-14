"""The resident gateway: the link, the two sockets, and a clean way to stop.

The sockets are unlinked on the way out and again on the way in, so that a
gateway that was killed leaves nothing a later one trips over.
"""

import asyncio
import logging
import signal
from pathlib import Path

from frostlog_gateway import endpoint, stream
from frostlog_gateway.link import Link

log = logging.getLogger(__name__)

EVENTS_SOCKET = "events.sock"
COMMANDS_SOCKET = "commands.sock"


class Gateway:
    def __init__(self, address: str, socket_dir: Path) -> None:
        self._address = address
        self._socket_dir = socket_dir

    async def run(self) -> None:
        events = stream.EventStream()
        link = Link(events, self._address)
        self._socket_dir.mkdir(parents=True, exist_ok=True)
        paths = [self._socket_dir / EVENTS_SOCKET, self._socket_dir / COMMANDS_SOCKET]
        servers = [
            await stream.serve(events, paths[0]),
            await endpoint.serve(link.command, paths[1]),
        ]
        log.info("listening on %s", " and ".join(str(path) for path in paths))
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, stop.set)
        try:
            await link.run(stop)
        finally:
            for server in servers:
                server.close()
            for path in paths:
                path.unlink(missing_ok=True)


def run(address: str, socket_dir: Path) -> None:
    asyncio.run(Gateway(address, socket_dir).run())

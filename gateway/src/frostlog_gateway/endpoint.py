"""The command endpoint: one request, one answer, then the connection is done.

Keeping no client state is the point. A client that asked and lost its connection
has no session to resume and nothing to clean up, and the gateway cannot be held
open by one. The answer says only whether the request reached the cooler; what
the cooler did with it arrives on the event stream, where everyone can see it.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Enough for any request on the allow-list; a client that sends more is not one.
MAX_REQUEST = 64 * 1024

Handler = Callable[[Any], Awaitable[dict[str, Any]]]


async def serve(command: Handler, path: Path) -> asyncio.Server:
    """Listen on ``path`` and hand each request line to ``command``."""

    async def client(inbound: asyncio.StreamReader, out: asyncio.StreamWriter) -> None:
        try:
            line = await inbound.readline()
            if not line.strip():
                return  # a client that connected and said nothing
            try:
                request = json.loads(line)
            except ValueError as exc:  # not JSON, or not even UTF-8
                log.warning("a request that is not JSON: %s", exc)
                request = None  # refused by the link, so that the stream carries it too
            out.write(json.dumps(await command(request), separators=(",", ":")).encode() + b"\n")
            await out.drain()
        except (ConnectionError, OSError, ValueError) as exc:
            log.warning("the request was not answered: %s", exc)
        finally:
            out.close()

    path.unlink(missing_ok=True)  # a socket file left behind by a killed gateway
    return await asyncio.start_unix_server(client, path, limit=MAX_REQUEST)

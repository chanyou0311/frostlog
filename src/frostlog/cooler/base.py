"""What every cooler implementation provides."""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Protocol

from frostlog import records


@dataclass(frozen=True)
class Found:
    """One device seen while scanning."""

    address: str
    name: str | None
    rssi: int
    cooler: bool


Sink = Callable[[records.Cooler | records.Event], None]


class Receiver(Protocol):
    """Connects to the cooler and hands every message and connection event to the sink."""

    async def run(self, stop: asyncio.Event) -> None: ...


class Decoder(Protocol):
    """Splits one cooler record into its parameters, for reading by hand (development-time use)."""

    model: str

    def decode(self, record: records.Cooler) -> dict[str, Any]: ...


Scanner = Callable[[float], Coroutine[Any, Any, list[Found]]]

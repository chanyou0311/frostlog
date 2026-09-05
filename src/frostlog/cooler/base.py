"""What every cooler implementation provides, and the normalized reading the analysis wants."""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel

from frostlog import records


class Reading(BaseModel):
    """The values the goal needs, once the raw messages are understood.

    Filled in by a decoder only when the mapping from bytes to meaning is known;
    until then the analysis works from the raw parameters.
    """

    input_w: float | None = None
    consumption_w: float | None = None
    usb_out_w: float | None = None
    battery_pct: float | None = None
    powered: bool | None = None
    set_temp_c: float | None = None
    box_temp_c: float | None = None


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
    """Adds meaning to the ``payload`` of one cooler record (development-time use)."""

    model: str

    def decode(self, payload: dict[str, Any]) -> dict[str, Any]: ...


Scanner = Callable[[float], Coroutine[Any, Any, list[Found]]]

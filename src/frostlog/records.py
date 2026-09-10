"""The records frostlog writes, one JSON object per line.

A record is one thing that happened at one source: one sensor reading, one
message received from the cooler, or one event. All records share ``ts``
(wall clock, UTC), ``uptime``, ``boot_id`` and ``clock_synced`` (see
:mod:`frostlog.clock`) and ``type``, which selects the stream the record
belongs to. ``clock_synced`` is ``None`` only in records converted from the
first days, before it was recorded.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from frostlog import clock


class _Common(BaseModel):
    ts: datetime
    uptime: float
    boot_id: str
    clock_synced: bool | None = None


class Ambient(_Common):
    type: Literal["ambient"] = "ambient"
    sensor: str
    temp_c: float
    humidity_pct: float


class Cooler(_Common):
    type: Literal["cooler"] = "cooler"
    model: str
    address: str
    payload: dict[str, Any]


class Event(_Common):
    model_config = ConfigDict(extra="allow")

    type: Literal["event"] = "event"
    kind: str


Record = Annotated[Ambient | Cooler | Event, Field(discriminator="type")]

STREAM_DIRS: dict[str, str] = {"ambient": "ambient", "cooler": "cooler", "event": "events"}

_adapter: TypeAdapter[Record] = TypeAdapter(Record)


def stamp() -> dict[str, Any]:
    """The common fields for a record created right now."""
    return {
        "ts": clock.now(),
        "uptime": clock.uptime(),
        "boot_id": clock.boot_id(),
        "clock_synced": clock.synced(),
    }


def ambient(sensor: str, temp_c: float, humidity_pct: float) -> Ambient:
    return Ambient(sensor=sensor, temp_c=temp_c, humidity_pct=humidity_pct, **stamp())


def cooler(model: str, address: str, payload: dict[str, Any]) -> Cooler:
    return Cooler(model=model, address=address, payload=payload, **stamp())


def event(kind: str, **fields: Any) -> Event:
    return Event(kind=kind, **fields, **stamp())


def to_json(record: Record) -> str:
    return record.model_dump_json()


def line(record: Record) -> str:
    """One record as one line of a JSONL file."""
    return to_json(record) + "\n"


def from_json(line: str | bytes) -> Record:
    return _adapter.validate_json(line)


def stream_dir(record: Record) -> str:
    return STREAM_DIRS[record.type]

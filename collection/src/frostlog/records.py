"""The records frostlog writes, one JSON object per line.

A record is one thing that happened at one source: one message received from
the cooler, or one event of the collector itself. All records share ``ts``
(wall clock, UTC), ``uptime_seconds``, ``boot_id`` and ``ts_synced`` (see
:mod:`frostlog.clock`) and ``type``, which selects the stream the record
belongs to. ``ts_synced`` is ``None`` only in records migrated from before it
was recorded.

The shapes here are the ones the ``frostlog-collection`` data contract describes:
what is written is what the bucket, and therefore the semantic data product,
receives. Optional fields are left out of the JSON rather than written as
``null``.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from frostlog import clock


class _Common(BaseModel):
    ts: datetime
    uptime_seconds: float
    boot_id: str
    ts_synced: bool | None = None


class Environment(BaseModel):
    """The air around the Pi at the moment a record was made."""

    sensor: str
    temperature_celsius: float
    humidity_percent: float


class CoolerPayload(BaseModel):
    """The decoded body of a cooler state report (cmd 4402).

    Temperatures are in °C whatever the cooler was displaying; ``display_unit``
    keeps what it displayed, because the setpoint is a whole number in that unit.
    """

    setpoint_celsius: int
    interior_temperature_celsius: int
    display_unit: Literal["C", "F"]
    input_watts: int
    usb_a_output_watts: int
    usb_c_output_watts: int
    charge_watts: int
    discharge_watts: int
    battery_state: Literal["idle", "charging", "discharging", "full", "absent"]
    state_of_charge_percent: int
    protection_level: Literal["L", "M", "H"]
    brightness: Literal["low", "mid", "high"]
    serial_number: str
    battery_serial_number: str | None = None


class Ambient(_Common):
    """One sensor reading on its own; written by ``frostlog read ambient`` only.

    Production data carries the environment on the cooler record it belongs to
    (:class:`Environment`); this record exists for checking the wiring by hand.
    """

    type: Literal["ambient"] = "ambient"
    sensor: str
    temperature_celsius: float
    humidity_percent: float


class Cooler(_Common):
    """One message received from the cooler, with the environment at that moment."""

    type: Literal["cooler"] = "cooler"
    model: str
    address: str
    pattern: str | None = None
    cmd: str | None = None
    frames: list[str]
    plain: str | None = None
    payload: CoolerPayload | None = None
    environment: Environment | None = None
    error: str | None = None


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
        "uptime_seconds": clock.uptime(),
        "boot_id": clock.boot_id(),
        "ts_synced": clock.synced(),
    }


def ambient(sensor: str, temperature_celsius: float, humidity_percent: float) -> Ambient:
    return Ambient(
        sensor=sensor,
        temperature_celsius=temperature_celsius,
        humidity_percent=humidity_percent,
        **stamp(),
    )


def cooler_at(
    at: dict[str, Any], model: str, address: str, frames: list[str], **fields: Any
) -> Cooler:
    """One message, stamped when it happened rather than when it was written down.

    What the cooler said is heard by the gateway, and a record made from it keeps
    the gateway's stamp: the two are the same moment only when nothing is queued.
    """
    return Cooler(model=model, address=address, frames=frames, **fields, **at)


def event_at(at: dict[str, Any], kind: str, **fields: Any) -> Event:
    """One event of someone else's, stamped where it happened (see :func:`cooler_at`)."""
    return Event(kind=kind, **fields, **at)


def cooler(model: str, address: str, frames: list[str], **fields: Any) -> Cooler:
    return cooler_at(stamp(), model, address, frames, **fields)


def event(kind: str, **fields: Any) -> Event:
    return event_at(stamp(), kind, **fields)


def to_json(record: Record) -> str:
    return record.model_dump_json(exclude_none=True)


def line(record: Record) -> str:
    """One record as one line of a JSONL file."""
    return to_json(record) + "\n"


def from_json(line: str | bytes) -> Record:
    return _adapter.validate_json(line)


def stream_dir(record: Record) -> str:
    return STREAM_DIRS[record.type]

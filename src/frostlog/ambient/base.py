"""What every ambient sensor implementation provides."""

from abc import ABC, abstractmethod
from typing import ClassVar

from pydantic import BaseModel

from frostlog.ambient.i2c import Bus


class Reading(BaseModel):
    temp_c: float
    humidity_pct: float


class SensorError(Exception):
    """A read failed (bus error, bad checksum, sensor busy); the next one may work."""


class Sensor(ABC):
    name: ClassVar[str]
    default_address: ClassVar[int]
    #: Shortest interval between reads the sensor tolerates, in seconds.
    min_interval: ClassVar[float] = 0.0

    def __init__(self, bus: Bus, address: int | None = None) -> None:
        self.bus = bus
        self.address = self.default_address if address is None else address

    @abstractmethod
    def read(self) -> Reading:
        """Take one measurement, raising :class:`SensorError` if it cannot be trusted."""

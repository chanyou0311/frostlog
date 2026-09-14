"""Sensor name (as given to ``--sensor``) to implementation."""

from frostlog.ambient.am2320 import AM2320
from frostlog.ambient.base import Sensor
from frostlog.ambient.dht20 import DHT20
from frostlog.ambient.i2c import Bus

SENSORS: dict[str, type[Sensor]] = {DHT20.name: DHT20, AM2320.name: AM2320}


def create(name: str, bus: Bus, address: int | None = None) -> Sensor:
    try:
        sensor_class = SENSORS[name]
    except KeyError:
        raise ValueError(f"unknown ambient sensor {name!r}; known: {sorted(SENSORS)}") from None
    return sensor_class(bus, address)

"""Read a sensor at a fixed interval and turn the readings into records."""

import logging
import time
from collections.abc import Callable, Iterator

from frostlog import records
from frostlog.ambient.base import Sensor, SensorError

log = logging.getLogger(__name__)


def read_loop(
    sensor: Sensor,
    interval: float,
    count: int | None = None,
    sleep: Callable[[float], object] = time.sleep,
) -> Iterator[records.Ambient | records.Event]:
    """Yield one ``ambient`` record per read, plus an ``event`` record for each failed read.

    Reads are ``interval`` seconds apart. Ends after ``count`` reads, or never.
    """
    if interval < sensor.min_interval:
        log.warning(
            "interval %.1fs is shorter than %s tolerates (%.1fs)",
            interval,
            sensor.name,
            sensor.min_interval,
        )
    done = 0
    while count is None or done < count:
        if done:
            sleep(interval)
        try:
            reading = sensor.read()
        except SensorError as exc:
            log.warning("%s", exc)
            yield records.event("ambient_read_failed", sensor=sensor.name, error=str(exc))
        else:
            yield records.ambient(sensor.name, reading.temp_c, reading.humidity_pct)
        done += 1

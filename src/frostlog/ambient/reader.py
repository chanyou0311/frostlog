"""Read a sensor at a fixed interval and turn the readings into records."""

import logging
import time
from collections.abc import Callable, Iterator

from frostlog import clock, records
from frostlog.ambient.base import Sensor, SensorError

log = logging.getLogger(__name__)


def read_loop(
    sensor: Sensor,
    interval: float,
    count: int | None = None,
    sleep: Callable[[float], object] = time.sleep,
) -> Iterator[records.Ambient | records.Event]:
    """Yield one ``ambient`` record per read, plus ``event`` records for failures and clock jumps.

    Reads are scheduled on a fixed grid (start + n * interval) so that a slow
    read does not shift the following ones. Ends after ``count`` reads, or never.
    """
    if interval < sensor.min_interval:
        log.warning(
            "interval %.1fs is shorter than %s tolerates (%.1fs)",
            interval,
            sensor.name,
            sensor.min_interval,
        )
    jumps = clock.JumpDetector()
    start = clock.uptime()
    done = 0
    while count is None or done < count:
        try:
            reading = sensor.read()
        except SensorError as exc:
            log.warning("%s", exc)
            yield records.event("ambient_read_failed", sensor=sensor.name, error=str(exc))
        else:
            record = records.ambient(sensor.name, reading.temp_c, reading.humidity_pct)
            jump = jumps.check(record.ts, record.uptime)
            if jump is not None:
                yield records.event("clock_jump", delta_s=jump.delta)
            yield record
        done += 1
        if count is not None and done >= count:
            return
        next_at = start + done * interval
        sleep(max(0.0, next_at - clock.uptime()))

"""Measure the air around the Pi at the moment a cooler message arrives.

The environment belongs to the message it was measured with: what matters is
how warm it was around the cooler while it drew the power it reported, not a
reading taken at some other second. The I2C read blocks for about 0.1 s, so it
runs in a thread and the event loop keeps taking notifications meanwhile.

A sensor that has come loose must not fill the events file: failures are
reported at most once a minute, and the message is recorded without an
environment.
"""

import asyncio
import logging
from collections.abc import Callable

from frostlog import clock, records
from frostlog.ambient.base import Sensor, SensorError

log = logging.getLogger(__name__)

#: Seconds between two ``environment_read_failed`` events while the sensor keeps failing.
REPORT_INTERVAL = 60.0


class EnvironmentSampler:
    def __init__(
        self,
        sensor: Sensor,
        report: Callable[[records.Event], None],
        report_interval: float = REPORT_INTERVAL,
    ) -> None:
        self._sensor = sensor
        self._report = report
        self._report_interval = report_interval
        self._reported_at: float | None = None

    async def sample(self) -> records.Environment | None:
        """One reading, or ``None`` when the sensor could not be read."""
        try:
            reading = await asyncio.to_thread(self._sensor.read)
        except SensorError as exc:
            self._on_failure(exc)
            return None
        return records.Environment(
            sensor=self._sensor.name,
            temperature_celsius=reading.temperature_celsius,
            humidity_percent=reading.humidity_percent,
        )

    def _on_failure(self, exc: SensorError) -> None:
        log.warning("%s", exc)
        now = clock.uptime()
        if self._reported_at is not None and now - self._reported_at < self._report_interval:
            return
        self._reported_at = now
        self._report(
            records.event("environment_read_failed", sensor=self._sensor.name, error=str(exc))
        )

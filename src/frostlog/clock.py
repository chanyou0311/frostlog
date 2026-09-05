"""Time as seen from the Pi: uptime, boot identity and wall-clock jumps.

The Pi has no RTC and is offline in the car, so the wall clock may be wrong or
jump when NTP catches up. Every record therefore also carries the seconds since
boot (``CLOCK_BOOTTIME``, which keeps counting through suspend) and an
identifier of the boot, which together order records exactly. When the wall
clock jumps, the reader emits an event so the offset can be reconstructed later.
"""

import functools
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

_BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
_BOOTTIME = getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC)


def now() -> datetime:
    return datetime.now(UTC)


def uptime() -> float:
    return time.clock_gettime(_BOOTTIME)


@functools.cache
def boot_id() -> str:
    try:
        return _BOOT_ID_PATH.read_text().strip()
    except OSError:
        # Not Linux (development machine): a per-process identifier is enough.
        return str(uuid.uuid4())


class JumpDetector:
    """Notice when wall time minus uptime changes by more than ``threshold`` seconds."""

    def __init__(self, threshold: float = 2.0) -> None:
        self._threshold = threshold
        self._offset: float | None = None

    def check(self, wall: datetime, up: float) -> float | None:
        """The seconds the wall clock jumped since the previous check, if it did."""
        offset = wall.timestamp() - up
        previous, self._offset = self._offset, offset
        if previous is None or abs(offset - previous) < self._threshold:
            return None
        return offset - previous

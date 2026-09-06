"""Time as seen from the Pi: wall clock, uptime and boot identity.

The Pi has no RTC and is offline in the car, so the wall clock may be wrong
until NTP catches up. Every record therefore also carries the seconds since
boot (``CLOCK_BOOTTIME``, which keeps counting through suspend) and an
identifier of the boot. Together they order records exactly, and the true
time of every record of a boot can be recovered later from any record that
was written after the clock had been set.
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

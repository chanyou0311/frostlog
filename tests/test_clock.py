from datetime import UTC, datetime

import pytest

from frostlog import clock


def test_uptime_and_boot_id() -> None:
    assert clock.uptime() > 0
    assert clock.boot_id() == clock.boot_id()


def test_jump_detector() -> None:
    detector = clock.JumpDetector(threshold=2.0)
    base = datetime(2026, 9, 6, tzinfo=UTC)
    assert detector.check(base, 100.0) is None
    assert detector.check(base.replace(second=10), 110.5) is None
    jump = detector.check(base.replace(minute=5), 120.0)
    assert jump == pytest.approx(280.5)

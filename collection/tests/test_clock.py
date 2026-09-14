from frostlog import clock


def test_uptime_and_boot_id() -> None:
    assert clock.uptime() > 0
    assert clock.boot_id() == clock.boot_id()

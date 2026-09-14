from datetime import UTC, date, datetime

from frostlog_notifier.clock import (
    next_morning,
    to_jst,
)


def test_jst_is_nine_hours_ahead() -> None:
    assert to_jst(datetime(2026, 9, 11, 12, 3, tzinfo=UTC)).hour == 21


def test_next_morning_is_the_coming_seven_oclock_jst() -> None:
    # 21:03 JST on the 11th -> 07:00 JST on the 12th, i.e. 22:00 UTC on the 11th.
    evening = datetime(2026, 9, 11, 12, 3, tzinfo=UTC)
    assert next_morning(evening) == datetime(2026, 9, 11, 22, 0, tzinfo=UTC)


def test_next_morning_from_before_dawn_is_the_same_day() -> None:
    # 05:30 JST on the 12th -> 07:00 JST the same day.
    dawn = datetime(2026, 9, 11, 20, 30, tzinfo=UTC)
    assert to_jst(next_morning(dawn)).date() == date(2026, 9, 12)

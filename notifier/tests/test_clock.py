from datetime import UTC, date, datetime

from frostlog_notifier.clock import (
    iso_week_bounds,
    iso_week_key,
    jst_dates,
    next_morning,
    previous_iso_week,
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


def test_a_week_runs_from_monday_midnight_jst_to_the_next() -> None:
    start, end = iso_week_bounds(2026, 37)
    assert to_jst(start) == datetime(2026, 9, 7, tzinfo=to_jst(start).tzinfo)
    assert (end - start).days == 7
    assert iso_week_key(2026, 37) == "2026-W37"


def test_the_week_to_summarise_is_the_one_before() -> None:
    assert previous_iso_week(datetime(2026, 9, 14, 12, 3, tzinfo=UTC)) == (2026, 37)


def test_the_dates_of_a_week_are_its_seven_days() -> None:
    start, end = iso_week_bounds(2026, 37)
    days = jst_dates(start, end)
    assert days[0] == date(2026, 9, 7)
    assert days[-1] == date(2026, 9, 13)
    assert len(days) == 7

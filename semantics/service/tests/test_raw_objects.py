"""How a JST date becomes the key the dimensional model uses."""

from datetime import date

from frostlog_semantics import raw_objects


def test_every_stream_has_a_raw_table() -> None:
    assert raw_objects.TABLES == {"cooler": "raw_cooler", "events": "raw_events"}


def test_a_date_key_is_the_date_as_yyyymmdd() -> None:
    assert raw_objects.date_key(date(2026, 9, 6)) == 20260906

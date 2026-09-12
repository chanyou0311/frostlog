"""Which objects in the raw bucket are ours, and which JST dates they touch."""

from datetime import date

import pytest

from frostlog_semantics import raw_objects


def test_a_chunk_is_recognised_by_its_key() -> None:
    chunk = raw_objects.parse(
        "frostlog-collection", "v1/cooler/dt=2026-09-06/000000122880.jsonl.gz"
    )
    assert chunk is not None
    assert chunk.stream == "cooler"
    assert chunk.table == "raw_cooler"
    assert chunk.dt == date(2026, 9, 6)
    assert chunk.offset == 122880
    assert chunk.uri == "gs://frostlog-collection/v1/cooler/dt=2026-09-06/000000122880.jsonl.gz"


def test_the_events_stream_has_its_own_table() -> None:
    chunk = raw_objects.parse(
        "frostlog-collection", "v1/events/dt=2026-09-06/000000000000.jsonl.gz"
    )
    assert chunk is not None
    assert chunk.table == "raw_events"


@pytest.mark.parametrize(
    "name",
    [
        "v1/ambient/dt=2026-09-06/000000000000.jsonl.gz",  # the retired stream
        "v2/cooler/dt=2026-09-06/000000000000.jsonl.gz",  # a future layout
        "v1/cooler/dt=2026-09-06/122880.jsonl.gz",  # offset not padded
        "v1/cooler/dt=2026-09-06/000000000000.jsonl",  # not compressed
        "v1/cooler/2026-09-06/000000000000.jsonl.gz",  # no dt= prefix
        "logs/upload.txt",
    ],
)
def test_anything_else_in_the_bucket_is_ignored(name: str) -> None:
    assert raw_objects.parse("frostlog-collection", name) is None


def test_a_utc_day_of_records_falls_on_two_jst_dates() -> None:
    # UTC 2026-09-06 runs from 09:00 JST that day to 09:00 JST the next.
    assert raw_objects.target_dates(date(2026, 9, 6)) == [date(2026, 9, 6), date(2026, 9, 7)]


def test_the_jst_date_at_the_end_of_a_month_is_the_first_of_the_next() -> None:
    assert raw_objects.target_dates(date(2026, 12, 31)) == [
        date(2026, 12, 31),
        date(2027, 1, 1),
    ]


def test_a_date_key_is_the_date_as_yyyymmdd() -> None:
    assert raw_objects.date_key(date(2026, 9, 6)) == 20260906

from datetime import UTC, datetime
from pathlib import Path

from frostlog import records
from frostlog.store import Store, list_files, read_lines


def _ambient_at(ts: datetime) -> records.Ambient:
    return records.Ambient(
        ts=ts, uptime=1.0, boot_id="b", sensor="dht20", temp_c=1.0, humidity_pct=2.0
    )


def test_append_writes_per_stream_and_day(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        store.append(_ambient_at(datetime(2026, 9, 6, 23, 59, tzinfo=UTC)))
        store.append(_ambient_at(datetime(2026, 9, 7, 0, 0, tzinfo=UTC)))
        store.append(
            records.Event(ts=datetime(2026, 9, 6, tzinfo=UTC), uptime=1.0, boot_id="b", kind="k")
        )
    assert [p.relative_to(tmp_path).as_posix() for p in list_files(tmp_path)] == [
        "ambient/2026-09-06.jsonl",
        "ambient/2026-09-07.jsonl",
        "events/2026-09-06.jsonl",
    ]
    lines = list(read_lines(tmp_path / "ambient/2026-09-06.jsonl"))
    assert len(lines) == 1
    assert records.from_json(lines[0]).type == "ambient"


def test_flush_makes_data_visible(tmp_path: Path) -> None:
    store = Store(tmp_path, flush_interval=3600)
    store.append(_ambient_at(datetime(2026, 9, 6, tzinfo=UTC)))
    store.flush()
    assert len(list(read_lines(tmp_path / "ambient/2026-09-06.jsonl"))) == 1
    store.close()


def test_read_lines_drops_torn_last_line(tmp_path: Path) -> None:
    path = tmp_path / "x.jsonl"
    path.write_bytes(b'{"a":1}\n{"a":2}\n{"a"')
    assert list(read_lines(path)) == ['{"a":1}', '{"a":2}']

import json
from datetime import UTC, datetime
from pathlib import Path

from frostlog import records
from frostlog.store import Store, append_line, list_files, read_lines


def _ambient_at(ts: datetime) -> records.Ambient:
    return records.Ambient(
        ts=ts,
        uptime_seconds=1.0,
        boot_id="b",
        sensor="dht20",
        temperature_celsius=1.0,
        humidity_percent=2.0,
    )


def _event_at(ts: datetime, kind: str = "k") -> records.Event:
    return records.Event(ts=ts, uptime_seconds=1.0, boot_id="b", kind=kind)


def test_append_writes_per_stream_and_day(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        store.append(_ambient_at(datetime(2026, 9, 6, 23, 59, tzinfo=UTC)))
        store.append(_ambient_at(datetime(2026, 9, 7, 0, 0, tzinfo=UTC)))
        store.append(_event_at(datetime(2026, 9, 6, tzinfo=UTC)))
    assert [p.relative_to(tmp_path).as_posix() for p in list_files(tmp_path)] == [
        "ambient/2026-09-06.jsonl",
        "ambient/2026-09-07.jsonl",
        "events/2026-09-06.jsonl",
    ]
    lines = list(read_lines(tmp_path / "ambient/2026-09-06.jsonl"))
    assert len(lines) == 1
    assert records.from_json(lines[0]).type == "ambient"


def test_lines_are_visible_before_close(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.append(_ambient_at(datetime(2026, 9, 6, tzinfo=UTC)))
    assert len(list(read_lines(tmp_path / "ambient/2026-09-06.jsonl"))) == 1
    store.close()


def test_reopen_after_torn_line_starts_a_fresh_line(tmp_path: Path) -> None:
    # Power was cut while a record was being written; the next run must not weld
    # its first record onto the torn one.
    path = tmp_path / "ambient/2026-09-06.jsonl"
    path.parent.mkdir()
    path.write_bytes(b'{"ts":"2026-09-06T00:00:00Z","typ')
    with Store(tmp_path) as store:
        store.append(_ambient_at(datetime(2026, 9, 6, 1, tzinfo=UTC)))
    lines = list(read_lines(path))
    assert lines[0] == '{"ts":"2026-09-06T00:00:00Z","typ'
    assert records.from_json(lines[1]).type == "ambient"
    with Store(tmp_path) as store:  # a file that ends properly is left alone
        store.append(_ambient_at(datetime(2026, 9, 6, 2, tzinfo=UTC)))
    assert len(list(read_lines(path))) == 3


def test_read_lines_drops_torn_last_line(tmp_path: Path) -> None:
    path = tmp_path / "x.jsonl"
    path.write_bytes(b'{"a":1}\n{"a":2}\n{"a"')
    assert list(read_lines(path)) == ['{"a":1}', '{"a":2}']


def test_append_line_adds_to_the_day_file_of_the_record(tmp_path: Path) -> None:
    append_line(tmp_path, _event_at(datetime(2026, 9, 6, tzinfo=UTC), "upload_started"))
    append_line(tmp_path, _event_at(datetime(2026, 9, 6, tzinfo=UTC), "upload_done"))
    path = tmp_path / "events/2026-09-06.jsonl"
    kinds = [json.loads(line)["kind"] for line in read_lines(path)]
    assert kinds == ["upload_started", "upload_done"]


def test_append_line_does_not_weld_itself_onto_a_torn_line(tmp_path: Path) -> None:
    path = tmp_path / "events/2026-09-06.jsonl"
    path.parent.mkdir()
    path.write_bytes(b'{"ts":"2026-09-06T00:00:00Z","ki')
    append_line(tmp_path, _event_at(datetime(2026, 9, 6, tzinfo=UTC), "upload_started"))
    lines = list(read_lines(path))
    assert lines[0] == '{"ts":"2026-09-06T00:00:00Z","ki'
    assert json.loads(lines[1])["kind"] == "upload_started"


def test_append_line_goes_to_the_end_while_the_recorder_writes(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        store.append(_event_at(datetime(2026, 9, 6, tzinfo=UTC), "ble_connected"))
        append_line(tmp_path, _event_at(datetime(2026, 9, 6, tzinfo=UTC), "upload_started"))
        store.append(_event_at(datetime(2026, 9, 6, tzinfo=UTC), "ble_disconnected"))
    kinds = [json.loads(line)["kind"] for line in read_lines(tmp_path / "events/2026-09-06.jsonl")]
    assert kinds == ["ble_connected", "upload_started", "ble_disconnected"]

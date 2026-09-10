"""The uploader against an in-memory bucket: chunks, resuming, pruning, and the command."""

import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from frostlog import cli
from frostlog.upload import healthcheck, s3
from frostlog.upload import sync as sync_module
from frostlog.upload.cache import OffsetCache
from frostlog.upload.run import Upload
from frostlog.upload.s3 import Offline
from frostlog.upload.sync import sync


class FakeStore:
    """What the bucket does: list a prefix, read an object's metadata, put an object."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.metadata: dict[str, dict[str, str]] = {}
        self.puts: list[str] = []
        self.lists: list[str] = []
        self.offline = False
        self.fail_on: set[str] = set()

    def list(self, prefix: str) -> list[str]:
        if self.offline:
            raise Offline("no route to host")
        self.lists.append(prefix)
        return [key for key in sorted(self.objects) if key.startswith(prefix)]

    def head(self, key: str) -> dict[str, str] | None:
        if self.offline:
            raise Offline("no route to host")
        return self.metadata.get(key)

    def put(self, key: str, data: bytes, metadata: dict[str, str]) -> None:
        if self.offline:
            raise Offline("no route to host")
        if key in self.fail_on:
            raise PermissionError("access denied")
        self.puts.append(key)
        self.objects[key] = data
        self.metadata[key] = dict(metadata)


def _lines(store: FakeStore, prefix: str) -> list[str]:
    """Everything the bucket holds under a prefix, in offset order."""
    keys = sorted(key for key in store.objects if key.startswith(prefix))
    joined = b"".join(gzip.decompress(store.objects[key]) for key in keys)
    return joined.decode("utf-8").splitlines()


def _write(root: Path, stream: str, day: str, text: str) -> Path:
    path = root / stream / f"{day}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _populate(root: Path, day: str = "2026-09-06") -> None:
    _write(root, "cooler", day, '{"a":1}\n')
    _write(root, "events", day, '{"kind":"x"}\n')


COOLER_PREFIX = "v1/cooler/dt=2026-09-06/"


def test_first_run_uploads_one_chunk_per_file(tmp_path: Path) -> None:
    _populate(tmp_path)
    store = FakeStore()
    actions = list(sync(tmp_path, store))
    assert [(action.key, action.action) for action in actions] == [
        (f"{COOLER_PREFIX}000000000000.jsonl.gz", "upload"),
        ("v1/events/dt=2026-09-06/000000000000.jsonl.gz", "upload"),
    ]
    assert _lines(store, COOLER_PREFIX) == ['{"a":1}']
    metadata = store.metadata[f"{COOLER_PREFIX}000000000000.jsonl.gz"]
    assert metadata["start"] == "0" and metadata["end"] == "8"
    assert metadata["uploaded-at"].startswith("20")
    assert actions[0].line_count == 1


def test_a_second_run_ships_only_what_was_added(tmp_path: Path) -> None:
    _populate(tmp_path)
    store = FakeStore()
    list(sync(tmp_path, store))
    path = tmp_path / "cooler/2026-09-06.jsonl"
    with path.open("a", encoding="utf-8") as file:
        file.write('{"a":2}\n')
    actions = {action.key: action.action for action in sync(tmp_path, store)}
    assert actions[f"{COOLER_PREFIX}000000000008.jsonl.gz"] == "upload"
    assert actions["v1/events/dt=2026-09-06/"] == "skip"
    assert _lines(store, COOLER_PREFIX) == ['{"a":1}', '{"a":2}']
    assert store.metadata[f"{COOLER_PREFIX}000000000008.jsonl.gz"]["end"] == "16"


def test_re_uploading_the_same_bytes_is_the_same_object(tmp_path: Path) -> None:
    _populate(tmp_path)
    store = FakeStore()
    list(sync(tmp_path, store))
    before = dict(store.objects)
    # The bucket lost its record of the last chunk (or the run was killed after the PUT).
    store.metadata[f"{COOLER_PREFIX}000000000000.jsonl.gz"] = {"start": "0", "end": "0"}
    list(sync(tmp_path, store))
    assert store.objects == before
    assert _lines(store, COOLER_PREFIX) == ['{"a":1}']


def test_a_torn_final_line_is_never_shipped(tmp_path: Path) -> None:
    _write(tmp_path, "cooler", "2026-09-06", '{"a":1}\n{"a":2}\n{"a"')
    store = FakeStore()
    list(sync(tmp_path, store))
    assert _lines(store, COOLER_PREFIX) == ['{"a":1}', '{"a":2}']
    # Once the recorder has started a fresh line, the torn one stays behind for good.
    with (tmp_path / "cooler/2026-09-06.jsonl").open("a", encoding="utf-8") as file:
        file.write('\n{"a":3}\n')
    list(sync(tmp_path, store))
    assert _lines(store, COOLER_PREFIX) == ['{"a":1}', '{"a":2}', '{"a"', '{"a":3}']


def test_a_long_backlog_is_split_at_line_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sync_module, "MAX_CHUNK", 32)
    _write(tmp_path, "cooler", "2026-09-06", "".join(f'{{"a":{n}}}\n' for n in range(10)))
    store = FakeStore()
    actions = [action for action in sync(tmp_path, store) if action.action == "upload"]
    assert len(actions) == 3
    assert all(action.size <= 32 for action in actions)
    assert _lines(store, COOLER_PREFIX) == [f'{{"a":{n}}}' for n in range(10)]
    starts = [int(key.rsplit("/", 1)[-1].removesuffix(".jsonl.gz")) for key in store.puts]
    assert starts == [0, 32, 64]


def test_a_local_file_shorter_than_the_bucket_is_left_alone(tmp_path: Path) -> None:
    _populate(tmp_path)
    store = FakeStore()
    list(sync(tmp_path, store))
    _write(tmp_path, "cooler", "2026-09-06", '{"a"')  # the SD card lost the file
    actions = {action.key: action.action for action in sync(tmp_path, store)}
    assert actions[COOLER_PREFIX] == "conflict"
    assert _lines(store, COOLER_PREFIX) == ['{"a":1}']


def test_a_failure_is_reported_and_the_other_files_continue(tmp_path: Path) -> None:
    _populate(tmp_path)
    store = FakeStore()
    store.fail_on = {f"{COOLER_PREFIX}000000000000.jsonl.gz"}
    actions = {action.key: action.action for action in sync(tmp_path, store)}
    assert actions[COOLER_PREFIX] == "failed"
    assert actions["v1/events/dt=2026-09-06/000000000000.jsonl.gz"] == "upload"


def test_offline_ends_the_run(tmp_path: Path) -> None:
    _populate(tmp_path)
    store = FakeStore()
    store.offline = True
    actions = [(action.key, action.action) for action in sync(tmp_path, store)]
    assert actions == [(COOLER_PREFIX, "offline")]


def test_only_the_streams_the_contract_describes_are_shipped(tmp_path: Path) -> None:
    _populate(tmp_path)
    _write(tmp_path, "ambient", "2026-09-06", '{"temp_c":25}\n')  # retired, still on the Pi
    store = FakeStore()
    prefixes = {action.key.split("/dt=")[0] for action in sync(tmp_path, store)}
    assert prefixes == {"v1/cooler", "v1/events"}
    assert (tmp_path / "ambient/2026-09-06.jsonl").exists()


# --- the cache: a listing costs money, so it is only paid for when there is a reason ---


def test_an_unchanged_file_is_not_listed_again(tmp_path: Path) -> None:
    _populate(tmp_path)
    store = FakeStore()
    cache = OffsetCache.load(tmp_path)
    list(sync(tmp_path, store, cache))
    assert len(store.lists) == 2
    store.lists.clear()
    assert [action.action for action in sync(tmp_path, store, cache)] == ["cached", "cached"]
    assert store.lists == []
    assert (tmp_path / ".upload-cache.json").exists()


def test_a_file_that_grew_is_listed_again(tmp_path: Path) -> None:
    _populate(tmp_path)
    store = FakeStore()
    cache = OffsetCache.load(tmp_path)
    list(sync(tmp_path, store, cache))
    store.lists.clear()
    with (tmp_path / "cooler/2026-09-06.jsonl").open("a", encoding="utf-8") as file:
        file.write('{"a":2}\n')
    actions = {
        action.key.split("/dt=")[0]: action.action for action in sync(tmp_path, store, cache)
    }
    assert actions["v1/cooler"] == "upload"
    assert store.lists == [COOLER_PREFIX]


def test_a_cache_that_lies_costs_a_listing_but_not_a_gap(tmp_path: Path) -> None:
    _populate(tmp_path)
    store = FakeStore()
    cache = OffsetCache.load(tmp_path)
    list(sync(tmp_path, store, cache))
    (tmp_path / ".upload-cache.json").write_text("not json at all", encoding="utf-8")
    reloaded = OffsetCache.load(tmp_path)
    actions = [action.action for action in sync(tmp_path, store, reloaded)]
    assert actions == ["skip", "skip"]  # the bucket was asked and knew better
    assert store.puts == store.puts[:2]


def test_the_cache_forgets_files_that_are_gone(tmp_path: Path) -> None:
    _populate(tmp_path)
    store = FakeStore()
    cache = OffsetCache.load(tmp_path)
    list(sync(tmp_path, store, cache))
    (tmp_path / "cooler/2026-09-06.jsonl").unlink()
    list(sync(tmp_path, store, cache))
    content = json.loads((tmp_path / ".upload-cache.json").read_text(encoding="utf-8"))
    assert list(content) == ["events/2026-09-06.jsonl"]


# --- keeping the SD card in shape -----------------------------------------------------


def _day(days_ago: int) -> str:
    return (datetime.now(UTC).date() - timedelta(days=days_ago)).isoformat()


def test_uploaded_files_are_kept_for_a_month_then_deleted(tmp_path: Path) -> None:
    _populate(tmp_path, _day(sync_module.KEEP_DAYS + 1))
    _populate(tmp_path, _day(1))
    _populate(tmp_path, _day(0))  # today is never deleted
    store = FakeStore()
    deleted = [action.key for action in sync(tmp_path, store) if action.action == "delete"]
    assert deleted == [
        f"cooler/{_day(sync_module.KEEP_DAYS + 1)}.jsonl",
        f"events/{_day(sync_module.KEEP_DAYS + 1)}.jsonl",
    ]
    assert (tmp_path / f"cooler/{_day(1)}.jsonl").exists()
    assert (tmp_path / f"cooler/{_day(0)}.jsonl").exists()


def test_a_low_disk_deletes_the_oldest_uploaded_files_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _populate(tmp_path, _day(2))
    _populate(tmp_path, _day(1))
    _populate(tmp_path, _day(0))  # today, never deleted
    free = iter([0, 0, 1 << 40, 1 << 40])  # room again after the first two deletions
    monkeypatch.setattr(
        sync_module.shutil, "disk_usage", lambda _path: SimpleNamespace(free=next(free))
    )
    store = FakeStore()
    deleted = [action.key for action in sync(tmp_path, store) if action.action == "delete"]
    assert deleted == [f"cooler/{_day(2)}.jsonl", f"events/{_day(2)}.jsonl"]
    assert (tmp_path / f"cooler/{_day(1)}.jsonl").exists()


# --- the run around sync: its own events, the exit status, the watchdog ----------------


def _events(root: Path) -> list[dict[str, object]]:
    """The events file the run writes into: today's."""
    path = root / "events" / f"{_day(0)}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_run_records_that_it_happened(tmp_path: Path) -> None:
    _populate(tmp_path, _day(0))
    store = FakeStore()
    run = Upload(tmp_path, store)
    list(run.run())
    kinds = [event["kind"] for event in _events(tmp_path)]
    assert kinds == ["x", "upload_started", "upload_done"]
    done = _events(tmp_path)[-1]
    assert done["uploaded_chunk_count"] == 2
    # The cooler line, the events line, and the upload_started this run just wrote.
    assert done["uploaded_line_count"] == 3
    assert run.clean and run.failed == 0


def test_a_run_that_never_reached_the_bucket_records_nothing(tmp_path: Path) -> None:
    _populate(tmp_path, _day(0))
    store = FakeStore()
    store.offline = True
    run = Upload(tmp_path, store)
    list(run.run())
    assert [event["kind"] for event in _events(tmp_path)] == ["x"]
    assert not run.clean


def test_the_upload_events_are_shipped_by_the_run_after(tmp_path: Path) -> None:
    _populate(tmp_path, _day(0))
    store = FakeStore()
    list(Upload(tmp_path, store).run())
    list(Upload(tmp_path, store).run())
    shipped = _lines(store, f"v1/events/dt={_day(0)}/")
    assert [json.loads(line).get("kind") for line in shipped] == [
        "x",
        "upload_started",
        "upload_done",
    ]


runner = CliRunner()


@pytest.fixture
def bucket(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeStore, list[str]]:
    monkeypatch.setenv("FROSTLOG_S3_ENDPOINT", "https://bucket.invalid")
    monkeypatch.setenv("FROSTLOG_S3_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("FROSTLOG_S3_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("FROSTLOG_HEALTHCHECK_URL", "https://hc-ping.com/x")
    store = FakeStore()
    monkeypatch.setattr(s3, "S3ObjectStore", lambda *_: store)
    pings: list[str] = []
    monkeypatch.setattr(healthcheck, "ping", pings.append)
    return store, pings


def _actions(stdout: str) -> list[str]:
    return [json.loads(line)["action"] for line in stdout.splitlines() if line.startswith("{")]


def test_clean_run_pings(bucket: tuple[FakeStore, list[str]], tmp_path: Path) -> None:
    _populate(tmp_path)
    _, pings = bucket
    result = runner.invoke(cli.app, ["upload", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert _actions(result.stdout) == ["upload", "upload"]
    assert pings == ["https://hc-ping.com/x"]


def test_offline_exits_zero_without_ping(
    bucket: tuple[FakeStore, list[str]], tmp_path: Path
) -> None:
    _populate(tmp_path)
    store, pings = bucket
    store.offline = True
    result = runner.invoke(cli.app, ["upload", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert _actions(result.stdout) == ["offline"]
    assert pings == []


def test_failed_file_exits_one_without_ping(
    bucket: tuple[FakeStore, list[str]], tmp_path: Path
) -> None:
    _populate(tmp_path)
    store, pings = bucket
    store.fail_on = {f"{COOLER_PREFIX}000000000000.jsonl.gz"}
    result = runner.invoke(cli.app, ["upload", str(tmp_path)])
    assert result.exit_code == 1
    assert _actions(result.stdout) == ["failed", "upload"]
    assert pings == []


def test_conflict_does_not_ping(bucket: tuple[FakeStore, list[str]], tmp_path: Path) -> None:
    _populate(tmp_path)
    store, pings = bucket
    key = f"{COOLER_PREFIX}000000000000.jsonl.gz"
    store.objects[key] = gzip.compress(b"x" * 1000)
    store.metadata[key] = {"start": "0", "end": "1000"}
    result = runner.invoke(cli.app, ["upload", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert _actions(result.stdout) == ["conflict", "upload"]
    assert pings == []


def test_empty_directory_does_not_ping(bucket: tuple[FakeStore, list[str]], tmp_path: Path) -> None:
    _, pings = bucket
    result = runner.invoke(cli.app, ["upload", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert pings == []


def test_a_missing_directory_is_an_error(
    bucket: tuple[FakeStore, list[str]], tmp_path: Path
) -> None:
    result = runner.invoke(cli.app, ["upload", str(tmp_path / "nope")])
    assert result.exit_code == 1


def test_upload_needs_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in ("FROSTLOG_S3_ACCESS_KEY_ID", "FROSTLOG_S3_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)
    result = runner.invoke(cli.app, ["upload", str(tmp_path)])
    assert result.exit_code == 1

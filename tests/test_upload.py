"""The uploader against an in-memory bucket: ``sync`` and the ``upload`` command around it."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from frostlog import cli
from frostlog.upload import healthcheck, s3
from frostlog.upload.s3 import Offline
from frostlog.upload.sync import sync


class FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts: list[str] = []
        self.offline = False
        self.fail_on: set[str] = set()

    def head(self, key: str) -> int | None:
        if self.offline:
            raise Offline("no route to host")
        data = self.objects.get(key)
        return None if data is None else len(data)

    def put(self, key: str, data: bytes) -> None:
        if key in self.fail_on:
            raise PermissionError("access denied")
        self.puts.append(key)
        self.objects[key] = data


def _populate(root: Path) -> None:
    (root / "ambient").mkdir()
    (root / "ambient/2026-09-06.jsonl").write_text('{"a":1}\n')
    (root / "events").mkdir()
    (root / "events/2026-09-06.jsonl").write_text('{"kind":"x"}\n')


def test_upload_then_second_run_is_noop(tmp_path: Path) -> None:
    _populate(tmp_path)
    store = FakeStore()
    first = list(sync(tmp_path, store))
    assert [(a.key, a.action) for a in first] == [
        ("ambient/2026-09-06.jsonl", "upload"),
        ("events/2026-09-06.jsonl", "upload"),
    ]
    second = list(sync(tmp_path, store))
    assert {a.action for a in second} == {"skip"}
    assert store.puts == ["ambient/2026-09-06.jsonl", "events/2026-09-06.jsonl"]


def test_grown_file_is_uploaded_again_in_full(tmp_path: Path) -> None:
    _populate(tmp_path)
    store = FakeStore()
    list(sync(tmp_path, store))
    (tmp_path / "ambient/2026-09-06.jsonl").write_text('{"a":1}\n{"a":2}\n')
    actions = {a.key: a.action for a in sync(tmp_path, store)}
    assert actions == {"ambient/2026-09-06.jsonl": "upload", "events/2026-09-06.jsonl": "skip"}
    assert store.objects["ambient/2026-09-06.jsonl"] == b'{"a":1}\n{"a":2}\n'


def test_failure_is_reported_and_others_continue(tmp_path: Path) -> None:
    _populate(tmp_path)
    store = FakeStore()
    store.fail_on = {"ambient/2026-09-06.jsonl"}
    actions = {a.key: a.action for a in sync(tmp_path, store)}
    assert actions == {"ambient/2026-09-06.jsonl": "failed", "events/2026-09-06.jsonl": "upload"}


def test_shorter_local_file_does_not_shrink_the_uploaded_copy(tmp_path: Path) -> None:
    _populate(tmp_path)
    store = FakeStore()
    list(sync(tmp_path, store))
    (tmp_path / "ambient/2026-09-06.jsonl").write_text('{"a"')  # torn after a power cut
    actions = {a.key: a.action for a in sync(tmp_path, store)}
    assert actions["ambient/2026-09-06.jsonl"] == "conflict"
    assert store.puts.count("ambient/2026-09-06.jsonl") == 1


def test_offline_ends_the_run(tmp_path: Path) -> None:
    _populate(tmp_path)
    store = FakeStore()
    store.offline = True
    actions = [(a.key, a.action) for a in sync(tmp_path, store)]
    assert actions == [("ambient/2026-09-06.jsonl", "offline")]


# --- the upload command around sync ---------------------------------------------------

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
    store.fail_on = {"ambient/2026-09-06.jsonl"}
    result = runner.invoke(cli.app, ["upload", str(tmp_path)])
    assert result.exit_code == 1
    assert _actions(result.stdout) == ["failed", "upload"]
    assert pings == []


def test_conflict_does_not_ping(bucket: tuple[FakeStore, list[str]], tmp_path: Path) -> None:
    _populate(tmp_path)
    store, pings = bucket
    store.objects["ambient/2026-09-06.jsonl"] = b"x" * 1000
    result = runner.invoke(cli.app, ["upload", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert _actions(result.stdout) == ["conflict", "upload"]
    assert pings == []


def test_empty_directory_does_not_ping(bucket: tuple[FakeStore, list[str]], tmp_path: Path) -> None:
    _, pings = bucket
    result = runner.invoke(cli.app, ["upload", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert pings == []

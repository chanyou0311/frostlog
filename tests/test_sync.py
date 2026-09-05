from pathlib import Path

from frostlog.upload.s3 import RemoteObject
from frostlog.upload.sync import sha256_of, sync


class FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, RemoteObject] = {}
        self.puts: list[str] = []
        self.fail_on: set[str] = set()

    def head(self, key: str) -> RemoteObject | None:
        return self.objects.get(key)

    def put(self, key: str, path: Path, sha256: str) -> None:
        if key in self.fail_on:
            raise OSError("network down")
        self.puts.append(key)
        self.objects[key] = RemoteObject(size=path.stat().st_size, sha256=sha256)


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


def test_changed_file_is_uploaded_again(tmp_path: Path) -> None:
    _populate(tmp_path)
    store = FakeStore()
    list(sync(tmp_path, store))
    (tmp_path / "ambient/2026-09-06.jsonl").write_text('{"a":1}\n{"a":2}\n')
    actions = {a.key: a.action for a in sync(tmp_path, store)}
    assert actions == {"ambient/2026-09-06.jsonl": "upload", "events/2026-09-06.jsonl": "skip"}


def test_dry_run_does_not_put(tmp_path: Path) -> None:
    _populate(tmp_path)
    store = FakeStore()
    actions = list(sync(tmp_path, store, dry_run=True))
    assert {a.action for a in actions} == {"upload"}
    assert store.puts == []


def test_failure_is_reported_and_others_continue(tmp_path: Path) -> None:
    _populate(tmp_path)
    store = FakeStore()
    store.fail_on = {"ambient/2026-09-06.jsonl"}
    actions = {a.key: a.action for a in sync(tmp_path, store)}
    assert actions == {"ambient/2026-09-06.jsonl": "failed", "events/2026-09-06.jsonl": "upload"}


def test_sha256_of(tmp_path: Path) -> None:
    path = tmp_path / "x"
    path.write_bytes(b"abc")
    assert sha256_of(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"

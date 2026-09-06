"""The uploader against an in-memory bucket."""

from pathlib import Path

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

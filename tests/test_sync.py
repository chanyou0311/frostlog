import hashlib
import io
from pathlib import Path

from frostlog.upload.s3 import RemoteObject
from frostlog.upload.sync import FilePrefix, sync


class FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, RemoteObject] = {}
        self.puts: list[tuple[str, bytes]] = []
        self.fail_on: set[str] = set()

    def head(self, key: str) -> RemoteObject | None:
        return self.objects.get(key)

    def put(self, key: str, body: io.RawIOBase, size: int, sha256: str) -> None:
        if key in self.fail_on:
            raise OSError("network down")
        data = body.read()
        assert data is not None and len(data) == size
        self.puts.append((key, data))
        self.objects[key] = RemoteObject(size=size, sha256=sha256)


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
    assert [key for key, _ in store.puts] == ["ambient/2026-09-06.jsonl", "events/2026-09-06.jsonl"]


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


def test_lines_appended_during_upload_wait_for_the_next_run(tmp_path: Path) -> None:
    _populate(tmp_path)

    class GrowingStore(FakeStore):
        def head(self, key: str) -> RemoteObject | None:
            # Another process appends between hashing and sending.
            with (tmp_path / key).open("a") as file:
                file.write('{"late":true}\n')
            return super().head(key)

    store = GrowingStore()
    list(sync(tmp_path, store))
    key, data = store.puts[0]
    assert key == "ambient/2026-09-06.jsonl" and data == b'{"a":1}\n'
    assert store.objects[key].sha256 == hashlib.sha256(data).hexdigest()
    # The next run sees the file has grown and sends it again, in full.
    actions = {a.key: a.action for a in sync(tmp_path, store)}
    assert actions["ambient/2026-09-06.jsonl"] == "upload"


def test_file_prefix_is_a_seekable_stream_of_the_first_bytes(tmp_path: Path) -> None:
    path = tmp_path / "x"
    path.write_bytes(b"abcdef")
    with path.open("rb") as file:
        prefix = FilePrefix(file, 3)
        assert prefix.seek(0, io.SEEK_END) == 3 and prefix.tell() == 3
        prefix.seek(0)
        assert (
            hashlib.file_digest(prefix, "sha256").hexdigest() == hashlib.sha256(b"abc").hexdigest()
        )
        prefix.seek(1)
        assert prefix.read() == b"bc"
        assert prefix.read() == b""

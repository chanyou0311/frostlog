"""Append records to ``<root>/<stream>/<YYYY-MM-DD>.jsonl``.

Every line is handed to the kernel as soon as it is written, so readers (and
``tail -f``) see it at once and the OS puts it on disk within seconds. A power
cut can lose those seconds and tear the final line; the next run continues on
a fresh line, so a torn line stays a line of its own that does not parse, and
readers skip it (see :func:`read_lines`).
"""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import IO

from frostlog import records
from frostlog.records import Record


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._files: dict[Path, IO[str]] = {}

    def path_for(self, record: Record) -> Path:
        day = record.ts.strftime("%Y-%m-%d")
        return self.root / records.stream_dir(record) / f"{day}.jsonl"

    def append(self, record: Record) -> None:
        path = self.path_for(record)
        file = self._files.get(path)
        if file is None:
            self._close_stream(path.parent)
            file = self._files[path] = _open_for_append(path)
        file.write(records.line(record))
        file.flush()

    def close(self) -> None:
        for file in self._files.values():
            file.close()
        self._files.clear()

    def _close_stream(self, directory: Path) -> None:
        # A new day started for this stream: release the previous day's file.
        for path in [p for p in self._files if p.parent == directory]:
            self._files.pop(path).close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _open_for_append(path: Path) -> IO[str]:
    """Open ``path`` for appending, starting on a fresh line if the last one was torn."""
    path.parent.mkdir(parents=True, exist_ok=True)
    file = path.open("a+", encoding="utf-8")
    size = os.fstat(file.fileno()).st_size
    if size and os.pread(file.fileno(), 1, size - 1) != b"\n":
        file.write("\n")
    return file


def list_files(root: Path) -> list[Path]:
    """All record files under ``root``, in a stable order."""
    return sorted(p for p in root.rglob("*.jsonl") if p.is_file())


def read_lines(path: Path) -> Iterator[str]:
    """The complete lines of a record file; a torn final line is dropped."""
    with path.open("rb") as file:
        for raw in file:
            if raw.endswith(b"\n"):
                yield raw[:-1].decode("utf-8", errors="replace")

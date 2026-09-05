"""Append records to ``<root>/<stream>/<YYYY-MM-DD>.jsonl``.

Writes are buffered and flushed to disk every few seconds so that a power cut
loses at most the last few seconds, and only the final line can be torn.
Readers must skip a broken last line (see :func:`read_lines`).
"""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import IO

from frostlog import clock, records
from frostlog.records import Ambient, Cooler, Event


class Store:
    def __init__(self, root: Path, flush_interval: float = 5.0) -> None:
        self.root = root
        self._flush_interval = flush_interval
        self._files: dict[Path, IO[str]] = {}
        self._dirty = False
        self._last_flush = clock.uptime()

    def path_for(self, record: Ambient | Cooler | Event) -> Path:
        day = record.ts.strftime("%Y-%m-%d")
        return self.root / records.stream_dir(record) / f"{day}.jsonl"

    def append(self, record: Ambient | Cooler | Event) -> None:
        path = self.path_for(record)
        file = self._files.get(path)
        if file is None:
            self._close_stream(path.parent)
            path.parent.mkdir(parents=True, exist_ok=True)
            file = self._files[path] = path.open("a", encoding="utf-8")
        file.write(records.to_json(record))
        file.write("\n")
        self._dirty = True
        if clock.uptime() - self._last_flush >= self._flush_interval:
            self.flush()

    def flush(self) -> None:
        if self._dirty:
            for file in self._files.values():
                file.flush()
                os.fsync(file.fileno())
            self._dirty = False
        self._last_flush = clock.uptime()

    def close(self) -> None:
        self.flush()
        for file in self._files.values():
            file.close()
        self._files.clear()

    def _close_stream(self, directory: Path) -> None:
        # A new day started for this stream: release the previous day's file.
        for path in [p for p in self._files if p.parent == directory]:
            file = self._files.pop(path)
            file.flush()
            os.fsync(file.fileno())
            file.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def list_files(root: Path) -> list[Path]:
    """All record files under ``root``, in a stable order."""
    return sorted(p for p in root.rglob("*.jsonl") if p.is_file())


def read_lines(path: Path) -> Iterator[str]:
    """The complete lines of a record file; a torn final line is dropped."""
    with path.open("rb") as file:
        for raw in file:
            if raw.endswith(b"\n"):
                yield raw[:-1].decode("utf-8", errors="replace")

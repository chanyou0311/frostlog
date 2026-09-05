"""Append records to ``<root>/<stream>/<YYYY-MM-DD>.jsonl``.

Every line is handed to the kernel as soon as it is written, so readers (and
``tail -f``) see it at once; files are fsynced every few seconds so that a
power cut loses at most the last few seconds, and only the final line can be
torn. Readers must skip a broken last line (see :func:`read_lines`).
"""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import IO

from frostlog import clock, records
from frostlog.records import Ambient, Cooler, Event


class Store:
    def __init__(self, root: Path, sync_interval: float = 5.0) -> None:
        self.root = root
        self._sync_interval = sync_interval
        self._files: dict[Path, IO[str]] = {}
        self._dirty = False
        self._last_sync = clock.uptime()

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
        file.flush()
        self._dirty = True
        if clock.uptime() - self._last_sync >= self._sync_interval:
            self.sync()

    def sync(self) -> None:
        """Force what has been written onto the disk."""
        if self._dirty:
            for file in self._files.values():
                os.fsync(file.fileno())
            self._dirty = False
        self._last_sync = clock.uptime()

    def close(self) -> None:
        self.sync()
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

"""Ship the new lines of every record file to the bucket as immutable chunks.

A local file ``<stream>/<YYYY-MM-DD>.jsonl`` maps to the prefix
``v1/<stream>/dt=<YYYY-MM-DD>/``. Each run sends the bytes that follow what is
already there as one object per chunk, named after the byte offset the chunk
starts at, e.g. ``v1/cooler/dt=2026-09-06/000000122880.jsonl.gz``. An object
records the offsets it covers in its metadata (``start``, ``end``) and is never
rewritten with different bytes: the same start always means the same lines, so
resending after a failure just puts the same object again. What an object holds
are the whole lines of that byte range that a reader can use: a line torn by a
power cut is never in a range to begin with, and one left empty or filled with
NUL bytes is dropped from the object while the range keeps covering it, because
a single NUL line fails the load of the whole chunk downstream.

The bucket is the record of what has been uploaded. Nothing that matters is kept
locally: a run lists the prefix, reads the ``end`` of the last chunk and continues
from there, so a re-imaged SD card or a lost cache cannot cause a gap or a
duplicate (see :mod:`frostlog.upload.cache` for why a run may skip the listing).

Once the bucket has confirmed a file in this run and its day is old enough the
file is deleted locally, and the oldest confirmed files go first when the disk
runs low. A file the cache alone calls complete is never deleted: the cache is a
hint about where to resume, not evidence that any bucket holds the bytes. When
the bucket cannot be reached at all the run stops with an ``offline`` action and
touches nothing.
"""

import gzip
import logging
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from frostlog.store import list_files, readable
from frostlog.upload.cache import OffsetCache
from frostlog.upload.s3 import ObjectStore, Offline

log = logging.getLogger(__name__)

PREFIX = "v1"
SUFFIX = ".jsonl.gz"
#: Uncompressed bytes per object at most; a long backlog is sent as several objects.
MAX_CHUNK = 4 * 1024 * 1024
#: Days a fully uploaded file stays on the Pi (for `tail`, and as a second copy).
KEEP_DAYS = 30
#: Below this much free space, uploaded files are deleted oldest first.
MIN_FREE_BYTES = 256 * 1024 * 1024

#: The collector's own stream: a run that carries nothing else is not worth recording.
EVENTS_STREAM = "events"
#: The streams the raw data contract describes. Anything else under the root is local
#: (the retired ``ambient`` stream until the migration removes it, a stream a newer
#: collector writes that this uploader predates) and stays local.
UPLOADED_STREAMS = frozenset({"cooler", EVENTS_STREAM})

#: What happened to one file or one chunk. ``cached`` is a file the cache says is
#: complete, the one outcome that does not prove any bucket holds it.
ActionName = Literal["upload", "skip", "cached", "conflict", "failed", "offline", "delete"]


@dataclass(frozen=True)
class Action:
    key: str
    action: ActionName
    size: int = 0
    line_count: int = 0
    error: str | None = None


@dataclass(frozen=True)
class Pending:
    """A file whose last complete line the bucket does not hold yet."""

    path: Path
    stream: str
    day: date
    prefix: str
    #: Bytes of the file the bucket holds, and bytes it could hold (up to the last newline).
    uploaded: int
    size: int


class Sync:
    """One pass over the record files: ask the bucket, send what is missing, prune.

    Planning is a phase of its own so that the caller knows what the run will
    carry before the first PUT (:mod:`frostlog.upload.run` records only the runs
    that carry records).
    """

    def __init__(self, root: Path, store: ObjectStore, cache: OffsetCache | None = None) -> None:
        self.root = root
        self.pending: list[Pending] = []
        self._store = store
        self._cache = cache
        self._files = [path for path in list_files(root) if path.parent.name in UPLOADED_STREAMS]
        self._confirmed: list[tuple[Path, date]] = []

    @property
    def ships_records(self) -> bool:
        """Does the plan carry anything but the collector's own events?"""
        return any(item.stream != EVENTS_STREAM for item in self.pending)

    def run(self) -> Iterator[Action]:
        yield from self.plan()
        yield from self.ship()
        yield from self.prune()
        self.save_cache()

    def plan(self) -> Iterator[Action]:
        """Compare every file with the bucket; nothing is sent and nothing is deleted."""
        for path in self._files:
            stream, day = path.parent.name, path.stem
            prefix = f"{PREFIX}/{stream}/dt={day}/"
            try:
                size = _complete_size(path)
                if self._cache is not None and self._cache.get(self.root, path) == size:
                    yield Action(prefix, "cached", size)
                    continue
                uploaded = _uploaded_end(self._store, prefix)
                if uploaded > size:
                    log.warning(
                        "%s: local %d bytes < uploaded %d bytes; left alone", path, size, uploaded
                    )
                    yield Action(prefix, "conflict", size, error=f"uploaded up to byte {uploaded}")
                    continue
                if uploaded == size:
                    self._confirm(path, date.fromisoformat(day), size)
                    yield Action(prefix, "skip", size)
                else:
                    self.pending.append(
                        Pending(path, stream, date.fromisoformat(day), prefix, uploaded, size)
                    )
            except Offline as exc:
                log.info("%s: bucket not reachable, stopping: %s", prefix, exc)
                self.pending.clear()
                yield Action(prefix, "offline", error=str(exc))
                return
            except Exception as exc:
                log.warning("%s: %s", prefix, exc)
                yield Action(prefix, "failed", error=str(exc))

    def ship(self) -> Iterator[Action]:
        """Send the chunks the plan found missing, oldest file first."""
        for item in self.pending:
            try:
                with item.path.open("rb") as file:
                    file.seek(item.uploaded)
                    data = file.read(item.size - item.uploaded)
                for start, chunk in _chunks(data, item.uploaded):
                    key = f"{item.prefix}{start:012d}{SUFFIX}"
                    body, dropped = _shippable(chunk)
                    if dropped:
                        log.warning("%s: %d unreadable line(s) left out", key, dropped)
                    # mtime=0: the same byte range must compress to the same object.
                    self._store.put(
                        key, gzip.compress(body, mtime=0), _metadata(start, start + len(chunk))
                    )
                    yield Action(key, "upload", len(chunk), body.count(b"\n"))
            except Offline as exc:
                log.info("%s: bucket not reachable, stopping: %s", item.prefix, exc)
                yield Action(item.prefix, "offline", error=str(exc))
                return
            except Exception as exc:
                log.warning("%s: %s", item.prefix, exc)
                yield Action(item.prefix, "failed", error=str(exc))
            else:
                self._confirm(item.path, item.day, item.size)

    def prune(self) -> Iterator[Action]:
        """Delete files the bucket confirmed that are old, and more while the disk is low."""
        today = datetime.now(UTC).date()
        for path, day in sorted((p for p in self._confirmed if p[1] < today), key=lambda p: p[1]):
            low = shutil.disk_usage(self.root).free < MIN_FREE_BYTES
            if (today - day).days <= KEEP_DAYS and not low:
                continue
            path.unlink()
            log.info("%s: deleted locally (%s)", path, "disk low" if low else "uploaded, old")
            yield Action(path.relative_to(self.root).as_posix(), "delete")

    def save_cache(self) -> None:
        if self._cache is not None:
            self._cache.save(self.root, (path for path in self._files if path.exists()))

    def _confirm(self, path: Path, day: date, size: int) -> None:
        """The bucket holds this file up to its last complete line, as of this run."""
        self._confirmed.append((path, day))
        if self._cache is not None:
            self._cache.set(self.root, path, size)


def sync(root: Path, store: ObjectStore, cache: OffsetCache | None = None) -> Iterator[Action]:
    """Plan, ship and prune in one pass; :class:`Sync` for a run that records itself."""
    return Sync(root, store, cache).run()


def _uploaded_end(store: ObjectStore, prefix: str) -> int:
    """How many bytes of the local file the bucket already holds under ``prefix``."""
    keys = [k for k in store.list(prefix) if k.endswith(SUFFIX)]
    if not keys:
        return 0
    last = max(keys, key=_start_of)
    metadata = store.head(last) or {}
    try:
        return int(metadata["end"])
    except (KeyError, ValueError):
        raise RuntimeError(f"{last}: no end offset in its metadata") from None


def _start_of(key: str) -> int:
    return int(key.rsplit("/", 1)[-1].removesuffix(SUFFIX))


def _complete_size(path: Path) -> int:
    """Bytes up to and including the last newline (a torn final line is left out)."""
    size = path.stat().st_size
    with path.open("rb") as file:
        position = size
        while position > 0:
            block = min(64 * 1024, position)
            file.seek(position - block)
            index = file.read(block).rfind(b"\n")
            if index >= 0:
                return position - block + index + 1
            position -= block
    return 0


def _chunks(data: bytes, start: int) -> Iterator[tuple[int, bytes]]:
    """Split ``data`` (whole lines) into pieces of at most ``MAX_CHUNK`` bytes at line ends."""
    position = 0
    while position < len(data):
        end = min(position + MAX_CHUNK, len(data))
        if end < len(data):
            cut = data.rfind(b"\n", position, end)
            end = cut + 1 if cut >= position else data.find(b"\n", end) + 1  # one long line
        yield start + position, data[position:end]
        position = end


def _shippable(chunk: bytes) -> tuple[bytes, int]:
    """The lines of a chunk that a reader can use, and how many were left out.

    One unreadable line (store.readable) fails the load of the whole chunk. Which
    lines go depends only on the bytes of the range, so re-uploading the range
    produces the same object; the offsets in the metadata keep counting local
    bytes, dropped lines included.
    """
    lines = chunk.splitlines(keepends=True)
    kept = [line for line in lines if readable(line)]
    return b"".join(kept), len(lines) - len(kept)


def _metadata(start: int, end: int) -> dict[str, str]:
    return {
        "start": str(start),
        "end": str(end),
        "uploaded-at": datetime.now(UTC).isoformat(timespec="seconds"),
    }

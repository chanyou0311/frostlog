"""Ship the new lines of every record file to the bucket as immutable chunks.

A local file ``<stream>/<YYYY-MM-DD>.jsonl`` maps to the prefix
``v1/<stream>/dt=<YYYY-MM-DD>/``. Each run sends the bytes that follow what is
already there as one object per chunk, named after the byte offset the chunk
starts at, e.g. ``v1/cooler/dt=2026-09-06/000000122880.jsonl.gz``. An object
records the offsets it covers in its metadata (``start``, ``end``) and is never
rewritten with different bytes: the same start always means the same lines, so
resending after a failure just puts the same object again.

The bucket is the record of what has been uploaded. Nothing is kept locally:
a run lists the prefix, reads the ``end`` of the last chunk and continues from
there, so a re-imaged SD card or a lost state file cannot cause a gap or a
duplicate. A torn final line (power cut) is never sent; the recorder starts a
fresh line after it, so it is skipped for good.

Once a file has been fully uploaded and its day is old enough it is deleted
locally, and the oldest uploaded files go first when the disk runs low. When
the bucket cannot be reached at all the run stops with an ``offline`` action
and touches nothing.
"""

import gzip
import logging
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from frostlog.store import list_files
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


@dataclass(frozen=True)
class Action:
    key: str
    action: Literal["upload", "skip", "conflict", "failed", "offline", "delete"]
    size: int = 0
    error: str | None = None


def sync(root: Path, store: ObjectStore) -> Iterator[Action]:
    today = datetime.now(UTC).date()
    done: list[tuple[Path, date]] = []  # fully uploaded files, oldest first
    for path in list_files(root):
        stream, day = path.parent.name, path.stem
        prefix = f"{PREFIX}/{stream}/dt={day}/"
        try:
            uploaded = _uploaded_end(store, prefix)
            size = _complete_size(path)
            if uploaded > size:
                log.warning(
                    "%s: local %d bytes < uploaded %d bytes; left alone", path, size, uploaded
                )
                yield Action(prefix, "conflict", size, error=f"uploaded up to byte {uploaded}")
                continue
            if uploaded == size:
                yield Action(prefix, "skip", size)
            else:
                with path.open("rb") as file:
                    file.seek(uploaded)
                    data = file.read(size - uploaded)
                for start, chunk in _chunks(data, uploaded):
                    key = f"{prefix}{start:012d}{SUFFIX}"
                    store.put(key, gzip.compress(chunk), _metadata(start, start + len(chunk)))
                    yield Action(key, "upload", len(chunk))
        except Offline as exc:
            log.info("%s: bucket not reachable, stopping: %s", prefix, exc)
            yield Action(prefix, "offline", error=str(exc))
            return
        except Exception as exc:
            log.warning("%s: %s", prefix, exc)
            yield Action(prefix, "failed", error=str(exc))
        else:
            done.append((path, date.fromisoformat(day)))
    yield from _prune(root, done, today)


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


def _metadata(start: int, end: int) -> dict[str, str]:
    return {
        "start": str(start),
        "end": str(end),
        "uploaded-at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def _prune(root: Path, done: list[tuple[Path, date]], today: date) -> Iterator[Action]:
    """Delete fully uploaded files that are old, and more of them while the disk is low."""
    done = sorted((p for p in done if p[1] < today), key=lambda p: p[1])
    for path, day in done:
        low = shutil.disk_usage(root).free < MIN_FREE_BYTES
        if (today - day).days <= KEEP_DAYS and not low:
            continue
        path.unlink()
        log.info("%s: deleted locally (%s)", path, "disk low" if low else "uploaded, old")
        yield Action(path.relative_to(root).as_posix(), "delete")

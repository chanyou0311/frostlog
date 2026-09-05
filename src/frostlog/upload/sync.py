"""Upload every record file whose remote copy is missing or different.

The object key is the file's path relative to the root, so the bucket mirrors
the local layout. A file is skipped when the remote object has the same size
and SHA-256 (kept as object metadata), which makes re-running a no-op. Files
that fail are reported and left for the next run; nothing is deleted.

Today's files are still being appended to while they are uploaded: what is
hashed and what is sent is the same prefix of the file, taken when it is
opened, and whatever arrives after that waits for the next run.
"""

import hashlib
import io
import logging
import os
from collections.abc import Buffer, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Literal

from frostlog.store import list_files
from frostlog.upload.s3 import ObjectStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Action:
    key: str
    action: Literal["upload", "skip", "failed"]
    size: int
    error: str | None = None


class FilePrefix(io.RawIOBase):
    """The first ``size`` bytes of an open file, as a seekable binary stream."""

    def __init__(self, file: IO[bytes], size: int) -> None:
        super().__init__()
        self._file = file
        self._size = size

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._file.tell()

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_END:
            offset, whence = self._size + offset, io.SEEK_SET
        return self._file.seek(offset, whence)

    def readinto(self, buffer: Buffer, /) -> int:
        view = memoryview(buffer)
        chunk = self._file.read(max(0, min(len(view), self._size - self._file.tell())))
        view[: len(chunk)] = chunk
        return len(chunk)


def sync(root: Path, store: ObjectStore, dry_run: bool = False) -> Iterator[Action]:
    for path in list_files(root):
        key = path.relative_to(root).as_posix()
        size = 0
        try:
            with path.open("rb") as file:
                size = os.fstat(file.fileno()).st_size
                body = FilePrefix(file, size)
                digest = hashlib.file_digest(body, "sha256").hexdigest()
                remote = store.head(key)
                if remote is not None and remote.size == size and remote.sha256 == digest:
                    yield Action(key, "skip", size)
                    continue
                if not dry_run:
                    body.seek(0)
                    store.put(key, body, size, digest)
        except Exception as exc:
            log.warning("%s: %s", key, exc)
            yield Action(key, "failed", size, error=str(exc))
            continue
        yield Action(key, "upload", size)

"""Upload every record file whose remote copy is missing or different.

The object key is the file's path relative to the root, so the bucket mirrors
the local layout. A file is skipped when the remote object has the same size
and SHA-256 (kept as object metadata), which makes re-running a no-op. Files
that fail are reported and left for the next run; nothing is deleted.
"""

import hashlib
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from frostlog.store import list_files
from frostlog.upload.s3 import ObjectStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Action:
    key: str
    action: Literal["upload", "skip", "failed"]
    size: int
    error: str | None = None


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sync(root: Path, store: ObjectStore, dry_run: bool = False) -> Iterator[Action]:
    for path in list_files(root):
        key = path.relative_to(root).as_posix()
        size = path.stat().st_size
        digest = sha256_of(path)
        try:
            remote = store.head(key)
            if remote is not None and remote.size == size and remote.sha256 == digest:
                yield Action(key, "skip", size)
                continue
            if not dry_run:
                store.put(key, path, digest)
        except Exception as exc:
            log.warning("%s: %s", key, exc)
            yield Action(key, "failed", size, error=str(exc))
            continue
        yield Action(key, "upload", size)

"""Upload every record file whose remote copy is missing or shorter.

The object key is the file's path relative to the root, so the bucket mirrors
the local layout. A file is skipped when the remote object has the same size,
which makes re-running a no-op; today's files grow between runs and are sent
again in full. A local file that is shorter than its uploaded copy (a torn
tail after a power cut) is reported as a conflict and left alone. Files that
fail are reported and left for the next run; nothing is deleted. When the
bucket cannot be reached at all, the run ends with an ``offline`` action.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from frostlog.store import list_files
from frostlog.upload.s3 import ObjectStore, Offline

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Action:
    key: str
    action: Literal["upload", "skip", "conflict", "failed", "offline"]
    size: int = 0
    error: str | None = None


def sync(root: Path, store: ObjectStore) -> Iterator[Action]:
    for path in list_files(root):
        key = path.relative_to(root).as_posix()
        try:
            size = path.stat().st_size
            remote_size = store.head(key)
            if remote_size == size:
                yield Action(key, "skip", size)
                continue
            if remote_size is not None and remote_size > size:
                # A torn tail after a power cut must not shrink what is already safe.
                log.warning(
                    "%s: local %d bytes < uploaded %d bytes; left alone", key, size, remote_size
                )
                yield Action(key, "conflict", size, error=f"uploaded copy has {remote_size} bytes")
                continue
            data = path.read_bytes()  # may have grown since the stat: send what is there now
            store.put(key, data)
        except Offline as exc:
            log.info("%s: bucket not reachable, stopping: %s", key, exc)
            yield Action(key, "offline", error=str(exc))
            return
        except Exception as exc:
            log.warning("%s: %s", key, exc)
            yield Action(key, "failed", error=str(exc))
        else:
            yield Action(key, "upload", len(data))

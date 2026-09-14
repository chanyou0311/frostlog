"""A hint about how far each day file has been uploaded, so that a quiet run is free.

The bucket is the record of what has been uploaded; this file only says which
files are worth asking the bucket about. Listing a prefix is a billed operation
and most runs have nothing new for most files, so a run lists a day's prefix
only when the file has grown past what the cache remembers. The cache is
advisory: if it is missing, stale or wrong, the run lists and believes the
bucket, and a wrong entry can only cost an extra listing, never a gap.

Two things keep that true. The entries belong to one bucket (endpoint and name),
so pointing the uploader at another bucket starts without a cache instead of
believing what the old one held; and a file the cache alone calls complete is
never deleted locally, because only the bucket can say that it holds it (see
:mod:`frostlog.upload.sync`).
"""

import json
import logging
import os
from collections.abc import Iterable
from pathlib import Path

log = logging.getLogger(__name__)

NAME = ".upload-cache.json"


class OffsetCache:
    def __init__(self, path: Path, location: str) -> None:
        self.path = path
        self.location = location
        self._ends: dict[str, int] = {}

    @classmethod
    def load(cls, root: Path, location: str) -> "OffsetCache":
        """The entries written for ``location``; anything else is not this bucket's state."""
        cache = cls(root / NAME, location)
        try:
            content = json.loads(cache.path.read_text(encoding="utf-8"))
            if content["location"] != location:
                raise ValueError(f"written for {content['location']}")
            cache._ends = {str(k): int(v) for k, v in content["ends"].items()}
        except (OSError, ValueError, LookupError, TypeError) as exc:
            log.debug("%s: starting without a cache (%s)", cache.path, exc)
        return cache

    def get(self, root: Path, path: Path) -> int | None:
        return self._ends.get(_key(root, path))

    def set(self, root: Path, path: Path, end: int) -> None:
        self._ends[_key(root, path)] = end

    def save(self, root: Path, keep: Iterable[Path]) -> None:
        """Write the entries of the files that are still there; a failure is not fatal."""
        wanted = {_key(root, path) for path in keep}
        ends = {key: end for key, end in self._ends.items() if key in wanted}
        temporary = self.path.with_suffix(".tmp")
        try:
            temporary.write_text(
                json.dumps({"location": self.location, "ends": ends}), encoding="utf-8"
            )
            os.replace(temporary, self.path)
        except OSError as exc:
            log.warning("%s: cache not written (%s)", self.path, exc)


def _key(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()

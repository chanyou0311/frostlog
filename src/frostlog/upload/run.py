"""One upload run: ship what is new and leave a record that it happened.

The run itself is part of the data: ``upload_started`` and ``upload_done`` go
into the events file, so the collection data product can be asked when it last
reached the bucket and how much it carried. They are written only once the
bucket has answered, because a Pi away from home would otherwise fill the file
with runs that did nothing.
"""

import logging
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from frostlog import records, store
from frostlog.upload.cache import OffsetCache
from frostlog.upload.s3 import ObjectStore
from frostlog.upload.sync import Action, sync

log = logging.getLogger(__name__)

#: Outcomes that prove the bucket answered this run (a cached skip does not).
REACHED = frozenset({"upload", "skip", "conflict", "failed"})


class Upload:
    def __init__(self, root: Path, object_store: ObjectStore) -> None:
        self._root = root
        self._store = object_store
        self.counts: Counter[str] = Counter()
        self.chunk_count = 0
        self.line_count = 0
        self.reached = False

    def run(self) -> Iterator[Action]:
        cache = OffsetCache.load(self._root)
        for action in sync(self._root, self._store, cache):
            if not self.reached and action.action in REACHED:
                self.reached = True
                self._record("upload_started")
            self.counts[action.action] += 1
            if action.action == "upload":
                self.chunk_count += 1
                self.line_count += action.line_count
            yield action
        if self.reached:
            self._record(
                "upload_done",
                uploaded_chunk_count=self.chunk_count,
                uploaded_line_count=self.line_count,
            )

    @property
    def failed(self) -> int:
        return self.counts["failed"]

    @property
    def clean(self) -> bool:
        """The bucket answered and nothing was left behind: worth telling the watchdog."""
        return self.reached and not (self.failed or self.counts["conflict"])

    @property
    def summary(self) -> str:
        return ", ".join(f"{name} {count}" for name, count in sorted(self.counts.items())) or "-"

    def _record(self, kind: str, **fields: object) -> None:
        try:
            store.append_line(self._root, records.event(kind, **fields))
        except OSError as exc:
            # Recording the run must never be what makes the run fail.
            log.warning("%s not recorded: %s", kind, exc)

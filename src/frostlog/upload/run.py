"""One upload run: ship what is new and leave a record that it happened.

The run itself is part of the data: ``upload_started`` and ``upload_done`` go
into the events file, so the collection data product can be asked when it last
reached the bucket and how much it carried.

Only a run that carries records is worth that. Writing the pair on every run
would give the events file new bytes every five minutes, and each of those runs
would then pay a listing and a PUT for the events prefix alone — some 8,600
billed operations a month against the 5,000 that are free. So the plan decides
first: a run that ships a chunk of any stream but ``events`` writes the pair
around its PUTs, a run that ships only events chunks ships them silently, and a
run that ships nothing writes nothing. The pair a run writes therefore travels
in the next run, which carries events only and stays quiet: the chain ends
there.
"""

import logging
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from frostlog import records, store
from frostlog.upload.cache import OffsetCache
from frostlog.upload.s3 import ObjectStore
from frostlog.upload.sync import Action, Sync

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
        cache = OffsetCache.load(self._root, self._store.location)
        pass_over_files = Sync(self._root, self._store, cache)
        yield from self._counted(pass_over_files.plan())
        # The line lands in the events file the plan has already measured, so this
        # run ships the file as it was and the next run carries the pair.
        recording = pass_over_files.ships_records
        if recording:
            self._record("upload_started")
        yield from self._counted(pass_over_files.ship())
        yield from self._counted(pass_over_files.prune())
        pass_over_files.save_cache()
        if recording:
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

    def _counted(self, actions: Iterator[Action]) -> Iterator[Action]:
        for action in actions:
            self.counts[action.action] += 1
            if action.action in REACHED:
                self.reached = True
            if action.action == "upload":
                self.chunk_count += 1
                self.line_count += action.line_count
            yield action

    def _record(self, kind: str, **fields: object) -> None:
        try:
            store.append_line(self._root, records.event(kind, **fields))
        except OSError as exc:
            # Recording the run must never be what makes the run fail.
            log.warning("%s not recorded: %s", kind, exc)

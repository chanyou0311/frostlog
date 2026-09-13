"""Where the transform remembers how far it has built.

A build is due when raw holds rows a build has not been given yet, so the run has to
remember where it stood last time. Cloud Run keeps nothing between requests, and the
warehouse is the wrong place to put it -- reading a row back is a query, and a query
costs ten mebibytes whatever it reads, which is the charge this whole design exists
to avoid. A small object in the collection bucket costs nothing to read or write.

It holds a row count per raw table, taken from table metadata after a build that
passed. Raw is append-only, so a count that has not moved means nothing arrived.
Losing this object is harmless: an empty mark reads as "nothing has been built",
which builds once more than it had to.
"""

import json
import logging
from typing import Any, Protocol

log = logging.getLogger(__name__)

#: Where in the bucket the mark lives. Under a prefix of its own, so nothing here can
#: be mistaken for a chunk -- the transfer's path template would not match it either.
OBJECT = "_control/built_through.json"


class BuiltThrough(Protocol):
    def read(self) -> dict[str, int]: ...

    def write(self, counts: dict[str, int]) -> None: ...


class BucketBuiltThrough:
    """:class:`BuiltThrough` over a JSON object in the collection bucket."""

    def __init__(self, client: Any, bucket: str) -> None:
        self._blob = client.bucket(bucket).blob(OBJECT)

    def read(self) -> dict[str, int]:
        """The counts the last passing build was given, or nothing if there is no mark."""
        from google.api_core.exceptions import NotFound

        try:
            return {str(k): int(v) for k, v in json.loads(self._blob.download_as_bytes()).items()}
        except NotFound:
            log.info("%s: no mark yet; treating everything as new", OBJECT)
            return {}
        except (ValueError, TypeError):
            # A mark we cannot read is worse than none: it would silently hold the
            # build back. Rebuilding once is the cheaper mistake.
            log.warning("%s: unreadable; treating everything as new", OBJECT)
            return {}

    def write(self, counts: dict[str, int]) -> None:
        """Record where raw stood, after a build that passed and published."""
        self._blob.upload_from_string(json.dumps(counts), content_type="application/json")

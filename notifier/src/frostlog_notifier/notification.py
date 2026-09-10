"""One thing to say, once."""

from dataclasses import dataclass

#: Kinds, which together with the key identify a notification in the state table.
HOMECOMING = "homecoming"
PULLDOWN = "pulldown"
WEEKLY = "weekly"
QUALITY = "quality"


@dataclass(frozen=True)
class Notification:
    kind: str
    #: Idempotency key within the kind; a notification is posted at most once per key.
    key: str
    text: str
    image: bytes | None = None
    filename: str = "chart.png"

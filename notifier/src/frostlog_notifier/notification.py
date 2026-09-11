"""One thing to say, once."""

from dataclasses import dataclass
from datetime import datetime

#: Kinds, which together with the key identify a notification in the state table.
HOMECOMING = "homecoming"
PULLDOWN = "pulldown"
WEEKLY = "weekly"
QUALITY = "quality"
FAILURE = "failure"


@dataclass(frozen=True)
class Notification:
    kind: str
    #: Idempotency key within the kind; a notification is posted at most once per key.
    key: str
    text: str
    image: bytes | None = None
    filename: str = "chart.png"
    #: How far this notification's period reached, kept so the next one starts there.
    coverage_end: datetime | None = None

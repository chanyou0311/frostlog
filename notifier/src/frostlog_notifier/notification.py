"""One thing to say, once."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

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
    #: What the message says, and what a client that cannot draw the blocks falls
    #: back to: the notification list, a screen reader, a search result.
    text: str
    #: The message as Block Kit. Empty means the text is the whole message.
    blocks: list[dict[str, Any]] = field(default_factory=list)
    #: How far this notification's period reached, kept so the next one starts there.
    coverage_end: datetime | None = None

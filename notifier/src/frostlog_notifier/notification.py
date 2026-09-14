"""One thing to say."""

from dataclasses import dataclass, field
from typing import Any

#: What a notification is about. Nothing keys off these any more -- each kind is sent
#: by a schedule of its own, or by an event that arrives once -- but a message still
#: says which it is, in logs and in the endpoints' answers.
DAILY = "daily"
WEEKLY = "weekly"
QUALITY = "quality"
FAILURE = "failure"


@dataclass(frozen=True)
class Notification:
    kind: str
    #: What the message says, and what a client that cannot draw the blocks falls
    #: back to: the notification list, a screen reader, a search result.
    text: str
    #: The message as Block Kit. Empty means the text is the whole message.
    blocks: list[dict[str, Any]] = field(default_factory=list)

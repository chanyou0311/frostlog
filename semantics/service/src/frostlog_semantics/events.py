"""What the transform says when it is done, as contracts/signals.odcs.yaml declares it.

Several kinds of event share the topic, so the message attribute ``event``
carries which one this is; the contract's schemas describe the body itself.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class SemanticUpdated(BaseModel):
    """Published after a transform run that rebuilt the warehouse tables.

    The consumer keeps its own copy of these models (notifier/src/frostlog_notifier/
    events.py): the two services deploy separately, and the contract in
    contracts/signals.odcs.yaml is what both must match.
    """

    #: The contract's discriminator: consumers branch on this, not on field presence.
    event: Literal["semantic_updated"] = "semantic_updated"
    run_id: str
    published_at: datetime
    rows_arrived: int
    #: Always true: a run whose build failed publishes nothing (reserved field).
    build_passed: bool

    @property
    def name(self) -> str:
        return "semantic_updated"

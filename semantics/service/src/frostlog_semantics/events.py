"""What the transform says when it is done, as contracts/signals.odcs.yaml declares it.

Several kinds of event share the topic, so the message attribute ``event``
carries which one this is; the contract's schemas describe the body itself.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class UploadRun(BaseModel):
    """One upload run of the collector, reported by an arrived events chunk.

    The gap between ``previous_finished_at`` and ``started_at`` is how long the
    collector was away from the home network: a run that starts two hours or more
    after the previous one ended is the car coming back.
    """

    finished_at: datetime
    started_at: datetime | None = None
    previous_finished_at: datetime | None = None
    chunk_count: int
    line_count: int


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
    date_keys: list[int]
    raw_loaded_since: datetime
    #: Always true: a run whose build failed publishes nothing (reserved field).
    build_passed: bool
    #: Filled when the run was triggered by an events chunk; empty otherwise.
    upload_runs: list[UploadRun] = Field(default_factory=list)

    @property
    def name(self) -> str:
        return "semantic_updated"

"""The events the semantic data product publishes, as declared in its contract.

Both events go to the same Pub/Sub topic, so the message attribute ``event``
carries which one it is (``semantic_updated`` or ``quality_report``); the
contract's schemas describe the message body itself.
"""

import logging
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


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
    """Published after a transform run that rebuilt date partitions.

    The consumer keeps its own copy of these models (notifier/src/frostlog_notifier/
    events.py): the two services deploy separately, and the contract in
    contracts/semantic-events.odcs.yaml is what both must match.
    """

    #: The contract's discriminator: consumers branch on this, not on field presence.
    event: Literal["semantic_updated"] = "semantic_updated"
    run_id: str
    published_at: datetime
    date_keys: list[int]
    raw_uploaded_at_max: datetime
    #: Always true: a run whose build failed publishes nothing (reserved field).
    build_passed: bool
    #: Filled when the run was triggered by an events chunk; empty otherwise.
    upload_runs: list[UploadRun] = Field(default_factory=list)

    @property
    def name(self) -> str:
        return "semantic_updated"


class QualityReport(BaseModel):
    """Published once per contract by the daily contract test run."""

    event: Literal["quality_report"] = "quality_report"
    run_id: str
    published_at: datetime
    contract_id: str
    passed: bool
    failed_checks: list[str]

    @property
    def name(self) -> str:
        return "quality_report"


Event = SemanticUpdated | QualityReport


class Publisher(Protocol):
    def publish(self, event: Event) -> None: ...


class PubSubPublisher:
    """:class:`Publisher` over google-cloud-pubsub."""

    def __init__(self, client: Any, topic: str) -> None:
        self._client = client
        self._topic = topic

    def publish(self, event: Event) -> None:
        body = event.model_dump_json().encode()
        self._client.publish(self._topic, body, event=event.name).result()
        log.info("published %s to %s", event.name, self._topic)


class NoPublisher:
    """Used when no topic is configured: the event is logged and goes nowhere."""

    def publish(self, event: Event) -> None:
        log.info("no topic configured; %s not published: %s", event.name, event.model_dump_json())

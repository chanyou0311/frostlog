"""The events the semantic data product publishes, as declared in its contract.

Both events go to the same Pub/Sub topic, so the message attribute ``event``
carries which one it is (``semantic_updated`` or ``quality_report``); the
contract's schemas describe the message body itself.
"""

import logging
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel

log = logging.getLogger(__name__)


class SemanticUpdated(BaseModel):
    """Published after a transform run that rebuilt date partitions."""

    run_id: str
    published_at: datetime
    date_keys: list[int]
    raw_uploaded_at_max: datetime
    build_passed: bool

    @property
    def name(self) -> str:
        return "semantic_updated"


class QualityReport(BaseModel):
    """Published once per contract by the daily contract test run."""

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

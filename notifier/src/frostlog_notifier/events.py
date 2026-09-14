"""The Pub/Sub push envelope and the events of contracts/signals.odcs.yaml.

The topic carries more than one kind of event; the contract's ``event`` field says
which one a message is, and parsing branches on it rather than on which fields
happen to be present.
"""

import base64
import binascii
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class SemanticUpdated(BaseModel):
    """The producer keeps its own copy (semantics/service/src/frostlog_semantics/events.py);
    the contract in contracts/signals.odcs.yaml is what both must match."""

    model_config = ConfigDict(extra="ignore")

    event: Literal["semantic_updated"] = "semantic_updated"
    run_id: str
    published_at: datetime
    #: How many rows reached raw since the build before this one. Nothing reads it:
    #: the summaries are scheduled and go to the warehouse themselves. It is parsed
    #: because the contract says the event carries it, and a consumer that cannot
    #: read the contract's own shape would not notice when the shape changed.
    rows_arrived: int
    build_passed: bool


class QualityReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event: Literal["quality_report"] = "quality_report"
    run_id: str
    published_at: datetime
    contract_id: str
    passed: bool
    failed_checks: list[str]


Event = SemanticUpdated | QualityReport


class _Message(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data: str = ""
    message_id: str = Field(default="", alias="messageId")
    attributes: dict[str, str] = Field(default_factory=dict)


class Envelope(BaseModel):
    """The body Pub/Sub pushes to the endpoint."""

    model_config = ConfigDict(extra="ignore")

    message: _Message
    subscription: str = ""


class Undecodable(ValueError):
    """The body is not a Pub/Sub push of a known event; retrying cannot help."""


def parse(body: Any) -> Event:
    """The event carried by a push envelope, or :class:`Undecodable`."""
    try:
        envelope = Envelope.model_validate(body)
        payload = json.loads(base64.b64decode(envelope.message.data, validate=True))
    except (ValidationError, ValueError, binascii.Error) as exc:
        raise Undecodable(f"not a Pub/Sub push envelope: {exc}") from exc
    if not isinstance(payload, dict):
        raise Undecodable(f"event is {type(payload).__name__}, not an object")
    kind = payload.get("event")
    model = {"semantic_updated": SemanticUpdated, "quality_report": QualityReport}.get(kind)
    if model is None:
        raise Undecodable(f"unknown event {kind!r}")
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise Undecodable(f"event does not match the contract: {exc}") from exc

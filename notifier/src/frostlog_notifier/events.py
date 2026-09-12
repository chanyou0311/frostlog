"""The Pub/Sub push envelope and the two events of the semantic data contract.

The topic carries both events; they are told apart by their fields, as the
contract gives them no discriminator.
"""

import base64
import binascii
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class UploadRun(BaseModel):
    """One upload run of the collector, as its own events chunk reported it.

    A report that the run ended, and no more: the contract does not promise that
    the run's other chunks are loaded, or that the run shipped everything it had.
    A summary built from it covers what the model held at the time.
    """

    model_config = ConfigDict(extra="ignore")

    finished_at: datetime
    started_at: datetime | None = None
    previous_finished_at: datetime | None = None
    chunk_count: int = 0
    line_count: int = 0

    @property
    def began_at(self) -> datetime:
        """When the run started, or when it finished if the start was not recorded."""
        return self.started_at or self.finished_at


class SemanticUpdated(BaseModel):
    """The producer keeps its own copy (semantics/service/src/frostlog_semantics/events.py);
    the contract in contracts/signals.odcs.yaml is what both must match."""

    model_config = ConfigDict(extra="ignore")

    #: The contract's discriminator; parse() branches on it.
    event: Literal["semantic_updated"] = "semantic_updated"
    run_id: str
    published_at: datetime
    date_keys: list[int]
    raw_uploaded_at_max: datetime
    build_passed: bool
    #: Present when this run loaded an `events` chunk; empty otherwise.
    upload_runs: list[UploadRun] = Field(default_factory=list)


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

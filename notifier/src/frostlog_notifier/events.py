"""The Pub/Sub push envelope and the two events of the semantic data contract.

The topic carries both events; they are told apart by their fields, as the
contract gives them no discriminator.
"""

import base64
import binascii
import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class SemanticUpdated(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_id: str
    published_at: datetime
    date_keys: list[int]
    raw_uploaded_at_max: datetime
    build_passed: bool


class QualityReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

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
    try:
        if "contract_id" in payload:
            return QualityReport.model_validate(payload)
        if "build_passed" in payload:
            return SemanticUpdated.model_validate(payload)
    except ValidationError as exc:
        raise Undecodable(f"event does not match the contract: {exc}") from exc
    raise Undecodable(f"unknown event with fields {sorted(payload)}")

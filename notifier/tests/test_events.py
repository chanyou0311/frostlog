import base64
import json
from typing import Any

import pytest

from frostlog_notifier.events import QualityReport, SemanticUpdated, Undecodable, parse

SEMANTIC_UPDATED = {
    "event": "semantic_updated",
    "run_id": "2026-09-11T12:03:00Z/17",
    "published_at": "2026-09-11T12:03:07Z",
    "rows_arrived": 412,
    "build_passed": True,
}

QUALITY_REPORT = {
    "event": "quality_report",
    "run_id": "2026-09-11T21:00:00Z",
    "published_at": "2026-09-11T21:00:41Z",
    "contract_id": "frostlog-semantics",
    "passed": False,
    "failed_checks": ["hours_are_dense", "every_report_is_reflected"],
}


def envelope(payload: dict[str, Any]) -> dict[str, Any]:
    data = base64.b64encode(json.dumps(payload).encode()).decode()
    return {
        "message": {"data": data, "messageId": "12345", "attributes": {}},
        "subscription": "projects/frostlog-chanyou/subscriptions/frostlog-notifier",
    }


def test_a_semantic_updated_event_is_read() -> None:
    event = parse(envelope(SEMANTIC_UPDATED))
    assert isinstance(event, SemanticUpdated)
    assert event.rows_arrived == 412
    assert event.build_passed is True


def test_a_quality_report_is_told_apart_by_its_event_field() -> None:
    event = parse(envelope(QUALITY_REPORT))
    assert isinstance(event, QualityReport)
    assert event.failed_checks == ["hours_are_dense", "every_report_is_reflected"]


def test_unknown_fields_are_ignored() -> None:
    event = parse(envelope(SEMANTIC_UPDATED | {"added_later": 1}))
    assert isinstance(event, SemanticUpdated)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"message": {"data": "not base64 at all !!"}},
        {"message": {"data": base64.b64encode(b"[1, 2]").decode()}},
        {"message": {"data": base64.b64encode(b"{}").decode()}},
        envelope({"build_passed": True}),  # no event field
        envelope(SEMANTIC_UPDATED | {"event": "something_else"}),
    ],
)
def test_a_message_that_is_not_a_known_event_is_refused(body: dict[str, Any]) -> None:
    with pytest.raises(Undecodable):
        parse(body)

import base64
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from frostlog_notifier.events import QualityReport, SemanticUpdated, Undecodable, parse

SEMANTIC_UPDATED = {
    "event": "semantic_updated",
    "run_id": "2026-09-11T12:03:00Z/17",
    "published_at": "2026-09-11T12:03:07Z",
    "date_keys": [20260910, 20260911],
    "raw_uploaded_at_max": "2026-09-11T12:02:55Z",
    "build_passed": True,
}

QUALITY_REPORT = {
    "event": "quality_report",
    "run_id": "2026-09-11T21:00:00Z",
    "published_at": "2026-09-11T21:00:41Z",
    "contract_id": "frostlog-semantic",
    "passed": False,
    "failed_checks": ["hours_are_dense", "fresh_within_a_day"],
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
    assert event.date_keys == [20260910, 20260911]
    assert event.build_passed is True


def test_the_upload_runs_of_an_events_chunk_are_read() -> None:
    event = parse(
        envelope(
            SEMANTIC_UPDATED
            | {
                "upload_runs": [
                    {
                        "finished_at": "2026-09-11T12:03:02Z",
                        "started_at": "2026-09-11T12:03:00Z",
                        "previous_finished_at": "2026-09-11T03:01:00Z",
                        "chunk_count": 3,
                        "line_count": 412,
                    }
                ]
            }
        )
    )
    assert isinstance(event, SemanticUpdated)
    [run] = event.upload_runs
    assert run.began_at == datetime(2026, 9, 11, 12, 3, tzinfo=UTC)
    assert run.previous_finished_at == datetime(2026, 9, 11, 3, 1, tzinfo=UTC)


def test_an_event_that_carries_no_upload_run_has_an_empty_list() -> None:
    event = parse(envelope(SEMANTIC_UPDATED))
    assert isinstance(event, SemanticUpdated)
    assert event.upload_runs == []


def test_an_upload_run_without_a_start_falls_back_to_its_end() -> None:
    event = parse(
        envelope(SEMANTIC_UPDATED | {"upload_runs": [{"finished_at": "2026-09-11T12:03:02Z"}]})
    )
    assert isinstance(event, SemanticUpdated)
    assert event.upload_runs[0].began_at == datetime(2026, 9, 11, 12, 3, 2, tzinfo=UTC)


def test_a_quality_report_is_told_apart_by_its_event_field() -> None:
    event = parse(envelope(QUALITY_REPORT))
    assert isinstance(event, QualityReport)
    assert event.failed_checks == ["hours_are_dense", "fresh_within_a_day"]


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

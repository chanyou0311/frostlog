"""The endpoints, end to end against the fakes."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from frostlog_semantics.app import create_app, transform_due
from frostlog_semantics.events import SemanticUpdated, UploadRun
from frostlog_semantics.warehouse import Arrivals
from tests.conftest import Fakes


def test_a_build_runs_and_announces_the_dates_whose_chunks_arrived(fakes: Fakes) -> None:
    result = transform_due(fakes.services)

    assert result["status"] == "built"
    assert fakes.transform.builds == 1


def test_the_update_is_announced_with_what_it_rebuilt(fakes: Fakes) -> None:
    transform_due(fakes.services)

    (event,) = fakes.publisher.published
    assert isinstance(event, SemanticUpdated)
    assert event.rows_arrived == 3
    assert event.build_passed is True


def test_the_upload_runs_window_reaches_past_a_single_build(fakes: Fakes) -> None:
    """It reports runs, not arrivals: one finishing between builds must still be seen."""
    transform_due(fakes.services)

    (since,) = fakes.warehouse.asked_since
    assert datetime.now(UTC) - since > timedelta(hours=24)


def test_nothing_arrived_means_nothing_is_built_or_announced(fakes: Fakes) -> None:
    fakes.warehouse.arrived = Arrivals(rows=0, counts={})

    result = transform_due(fakes.services)

    assert result["status"] == "idle"
    assert fakes.transform.builds == 0
    assert fakes.publisher.published == []


def test_rows_without_a_day_are_still_a_reason_to_build(fakes: Fakes) -> None:
    """A record the collector could not stamp has no JST day and is still an arrival.

    Deciding on the days instead of the rows would leave the build undone until
    something stampable happened to turn up.
    """
    fakes.warehouse.arrived = Arrivals(rows=2, counts={"raw_cooler": 2})

    result = transform_due(fakes.services)

    assert result["status"] == "built"
    assert fakes.transform.builds == 1
    assert result["rows_arrived"] == 2


def test_a_failed_build_asks_for_another_run_and_announces_nothing(fakes: Fakes) -> None:
    # Every retry would otherwise publish another event about data that is not there.
    fakes.transform.passed = False

    with pytest.raises(HTTPException) as raised:
        transform_due(fakes.services)

    assert raised.value.status_code == 500
    assert fakes.publisher.published == []


def test_the_runs_of_every_events_chunk_in_the_window_are_announced(fakes: Fakes) -> None:
    """One transform covers many chunks, so it carries every run they reported."""
    fakes.warehouse.runs = [
        UploadRun(
            finished_at=datetime(2026, 9, 6, 11, 4, 12, tzinfo=UTC),
            started_at=datetime(2026, 9, 6, 11, 4, 10, tzinfo=UTC),
            previous_finished_at=datetime(2026, 9, 6, 2, 0, tzinfo=UTC),
            chunk_count=2,
            line_count=311,
        ),
        UploadRun(
            finished_at=datetime(2026, 9, 6, 11, 9, 14, tzinfo=UTC),
            started_at=datetime(2026, 9, 6, 11, 9, 12, tzinfo=UTC),
            previous_finished_at=datetime(2026, 9, 6, 11, 4, 12, tzinfo=UTC),
            chunk_count=1,
            line_count=88,
        ),
    ]

    result = transform_due(fakes.services)

    (event,) = fakes.publisher.published
    assert isinstance(event, SemanticUpdated)
    assert [run.line_count for run in event.upload_runs] == [311, 88]
    assert result["upload_runs"] == 2


def test_the_routes_are_wired(fakes: Fakes) -> None:
    """Two of them now: the service stopped being the way chunks get in."""
    client = TestClient(create_app(fakes.services))

    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.post("/jobs/transform").json()["status"] == "built"
    assert client.post("/events/gcs", json={}).status_code == 404


def test_a_failed_build_leaves_the_mark_where_it_was(fakes: Fakes) -> None:
    """The mark is a promise that a build was given those rows. A failure made none."""
    fakes.transform.passed = False

    with pytest.raises(HTTPException):
        transform_due(fakes.services)

    assert fakes.built_through.written == []


def test_an_unannounced_build_leaves_the_mark_where_it_was(fakes: Fakes) -> None:
    """A build nobody was told about must be redone; moving the mark would hide it."""
    fakes.publisher.fails = True

    with pytest.raises(RuntimeError):
        transform_due(fakes.services)

    assert fakes.built_through.written == []


def test_the_mark_records_what_the_build_was_given(fakes: Fakes) -> None:
    """Not the rows that arrived -- the counts the tables stood at when it ran."""
    transform_due(fakes.services)

    assert fakes.built_through.written == [fakes.warehouse.arrived.counts]

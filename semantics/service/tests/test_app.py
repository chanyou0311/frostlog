"""The endpoints, end to end against the fakes: loading, and transforming."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from frostlog_semantics.app import chunk_arrived, create_app, transform_due
from frostlog_semantics.events import SemanticUpdated, UploadRun
from tests.conftest import COLLECTION_BUCKET, Fakes


def test_a_chunk_is_loaded_and_nothing_more(fakes: Fakes, finalized: dict) -> None:
    """Arrival is a load. Rebuilding on every one of them is what this schedule avoids."""
    result = chunk_arrived(fakes.services, finalized)

    assert result["status"] == "loaded"
    assert result["table"] == "raw_cooler"
    assert fakes.warehouse.loaded[0][0] == finalized["name"]
    assert fakes.transform.builds == 0
    assert fakes.publisher.published == []


def test_a_build_runs_and_announces_the_dates_whose_chunks_arrived(fakes: Fakes) -> None:
    result = transform_due(fakes.services)

    assert result["status"] == "built"
    assert result["date_keys"] == [20260906, 20260907]
    assert fakes.transform.builds == 1


def test_the_update_is_announced_with_what_it_rebuilt(fakes: Fakes) -> None:
    transform_due(fakes.services)

    (event,) = fakes.publisher.published
    assert isinstance(event, SemanticUpdated)
    assert event.date_keys == [20260906, 20260907]
    assert event.build_passed is True


def test_the_window_reaches_back_further_than_the_schedule_steps(fakes: Fakes) -> None:
    """A missed run is made good by the next one rather than leaving a date unbuilt."""
    transform_due(fakes.services)

    (since, _) = fakes.warehouse.asked_since
    lookback = datetime.now(UTC) - since
    assert lookback > timedelta(hours=24)


def test_nothing_arrived_means_nothing_is_built_or_announced(fakes: Fakes) -> None:
    """An empty date list would otherwise fall back to the macro's no-op date."""
    fakes.warehouse.arrived = []

    result = transform_due(fakes.services)

    assert result["status"] == "idle"
    assert fakes.transform.builds == 0
    assert fakes.publisher.published == []


def test_the_upload_time_comes_from_the_object_metadata(fakes: Fakes, finalized: dict) -> None:
    fakes.metadata.uploaded = datetime(2026, 9, 6, 11, 30, tzinfo=UTC)

    chunk_arrived(fakes.services, finalized)

    assert fakes.warehouse.loaded[0][1] == datetime(2026, 9, 6, 11, 30, tzinfo=UTC)


def test_the_event_time_stands_in_when_the_object_has_no_metadata(
    fakes: Fakes, finalized: dict
) -> None:
    del finalized["timeCreated"]

    chunk_arrived(fakes.services, finalized, ce_time="2026-09-06T12:34:56+00:00")

    assert fakes.warehouse.loaded[0][1] == datetime(2026, 9, 6, 12, 34, 56, tzinfo=UTC)


def test_a_structured_cloudevent_is_understood_too(fakes: Fakes, finalized: dict) -> None:
    result = chunk_arrived(fakes.services, {"time": "2026-09-06T12:00:00+00:00", "data": finalized})

    assert result["status"] == "loaded"


def test_an_object_that_is_not_a_chunk_is_left_alone(fakes: Fakes) -> None:
    result = chunk_arrived(fakes.services, {"bucket": COLLECTION_BUCKET, "name": "logs/upload.txt"})

    assert result["status"] == "ignored"
    assert fakes.warehouse.loaded == []
    assert fakes.transform.builds == 0
    assert fakes.publisher.published == []


def test_an_event_about_another_bucket_is_ignored(fakes: Fakes, finalized: dict) -> None:
    finalized["bucket"] = "somebody-elses-bucket"

    result = chunk_arrived(fakes.services, finalized)

    assert result["status"] == "ignored"
    assert fakes.warehouse.loaded == []
    assert fakes.transform.builds == 0


def test_an_event_without_an_object_is_a_bad_request(fakes: Fakes) -> None:
    with pytest.raises(HTTPException) as raised:
        chunk_arrived(fakes.services, {"kind": "storage#object"})

    assert raised.value.status_code == 400


def test_a_redelivery_is_still_a_load(fakes: Fakes, finalized: dict) -> None:
    fakes.warehouse.already_loaded = True

    result = chunk_arrived(fakes.services, finalized)

    assert result["already_loaded"] is True


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


def test_the_routes_are_wired(fakes: Fakes, finalized: dict) -> None:
    client = TestClient(create_app(fakes.services))

    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.post("/events/gcs", json=finalized).json()["status"] == "loaded"
    assert client.post("/jobs/transform").json()["status"] == "built"

"""The two endpoints, end to end against the fakes."""

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from frostlog_semantics.app import chunk_arrived, create_app
from frostlog_semantics.events import SemanticUpdated, UploadRun
from tests.conftest import COLLECTION_BUCKET, Fakes


def test_a_chunk_is_loaded_and_its_jst_dates_rebuilt(fakes: Fakes, finalized: dict) -> None:
    result = chunk_arrived(fakes.services, finalized)

    assert result["status"] == "built"
    assert result["table"] == "raw_cooler"
    assert result["date_keys"] == [20260906, 20260907]
    assert fakes.warehouse.loaded[0][0] == finalized["name"]
    assert fakes.transform.builds == [["2026-09-06", "2026-09-07"]]


def test_the_update_is_announced_with_what_it_rebuilt(fakes: Fakes, finalized: dict) -> None:
    chunk_arrived(fakes.services, finalized)

    (event,) = fakes.publisher.published
    assert isinstance(event, SemanticUpdated)
    assert event.date_keys == [20260906, 20260907]
    assert event.build_passed is True
    assert event.raw_uploaded_at_max == datetime(2026, 9, 6, 12, tzinfo=UTC)


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

    assert result["status"] == "built"


def test_an_object_that_is_not_a_chunk_is_left_alone(fakes: Fakes) -> None:
    result = chunk_arrived(fakes.services, {"bucket": COLLECTION_BUCKET, "name": "logs/upload.txt"})

    assert result["status"] == "ignored"
    assert fakes.warehouse.loaded == []
    assert fakes.transform.builds == []
    assert fakes.publisher.published == []


def test_an_event_about_another_bucket_is_ignored(fakes: Fakes, finalized: dict) -> None:
    finalized["bucket"] = "somebody-elses-bucket"

    result = chunk_arrived(fakes.services, finalized)

    assert result["status"] == "ignored"
    assert fakes.warehouse.loaded == []
    assert fakes.transform.builds == []


def test_an_event_without_an_object_is_a_bad_request(fakes: Fakes) -> None:
    with pytest.raises(HTTPException) as raised:
        chunk_arrived(fakes.services, {"kind": "storage#object"})

    assert raised.value.status_code == 400


def test_a_redelivery_still_rebuilds(fakes: Fakes, finalized: dict) -> None:
    # The append is refused as already done, but the build has to run: the first
    # delivery may have failed exactly between the two.
    fakes.warehouse.already_loaded = True

    result = chunk_arrived(fakes.services, finalized)

    assert result["already_loaded"] is True
    assert fakes.transform.builds == [["2026-09-06", "2026-09-07"]]


def test_a_failed_build_asks_eventarc_to_try_again_and_announces_nothing(
    fakes: Fakes, finalized: dict
) -> None:
    # Every retry would otherwise publish another event about data that is not there.
    fakes.transform.passed = False

    with pytest.raises(HTTPException) as raised:
        chunk_arrived(fakes.services, finalized)

    assert raised.value.status_code == 500
    assert fakes.publisher.published == []


def test_an_events_chunk_announces_the_upload_runs_it_carried(
    fakes: Fakes, finalized: dict
) -> None:
    finalized["name"] = "v1/events/dt=2026-09-06/000000000000.jsonl.gz"
    fakes.warehouse.runs = [
        UploadRun(
            finished_at=datetime(2026, 9, 6, 11, 4, 12, tzinfo=UTC),
            started_at=datetime(2026, 9, 6, 11, 4, 10, tzinfo=UTC),
            previous_finished_at=datetime(2026, 9, 6, 2, 0, tzinfo=UTC),
            chunk_count=2,
            line_count=311,
        )
    ]

    chunk_arrived(fakes.services, finalized)

    (event,) = fakes.publisher.published
    assert isinstance(event, SemanticUpdated)
    assert [run.line_count for run in event.upload_runs] == [311]
    assert fakes.warehouse.asked_for_runs == [finalized["name"]]


def test_a_cooler_chunk_carries_no_upload_runs(fakes: Fakes, finalized: dict) -> None:
    chunk_arrived(fakes.services, finalized)

    (event,) = fakes.publisher.published
    assert isinstance(event, SemanticUpdated)
    assert event.upload_runs == []
    assert fakes.warehouse.asked_for_runs == []


def test_the_routes_are_wired(fakes: Fakes, finalized: dict) -> None:
    client = TestClient(create_app(fakes.services))

    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.post("/events/gcs", json=finalized).json()["status"] == "built"

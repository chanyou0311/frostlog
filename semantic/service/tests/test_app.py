"""The two endpoints, end to end against the fakes."""

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from frostlog_semantic.app import chunk_arrived, contract_test, create_app
from frostlog_semantic.contracts import ContractTestResult
from frostlog_semantic.events import QualityReport, SemanticUpdated
from tests.conftest import Fakes


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
    result = chunk_arrived(fakes.services, {"bucket": "frostlog-raw", "name": "logs/upload.txt"})

    assert result["status"] == "ignored"
    assert fakes.warehouse.loaded == []
    assert fakes.transform.builds == []
    assert fakes.publisher.published == []


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


def test_a_failed_build_asks_eventarc_to_try_again(fakes: Fakes, finalized: dict) -> None:
    fakes.transform.passed = False

    with pytest.raises(HTTPException) as raised:
        chunk_arrived(fakes.services, finalized)

    assert raised.value.status_code == 500
    (event,) = fakes.publisher.published
    assert isinstance(event, SemanticUpdated)
    assert event.build_passed is False


def test_a_clean_contract_test_reports_both_contracts_and_pings(fakes: Fakes) -> None:
    result = contract_test(fakes.services)

    assert result["passed"] is True
    assert [report["contract_id"] for report in result["contracts"]] == [
        "frostlog-raw",
        "frostlog-semantic",
    ]
    assert fakes.tester.tested == [
        ("raw.odcs.yaml", "gcs"),
        ("semantic.odcs.yaml", "production"),
    ]
    assert fakes.pinged == ["https://hc.example/uuid"]
    assert all(isinstance(event, QualityReport) for event in fakes.publisher.published)


def test_a_failing_check_is_reported_and_the_switch_is_not_pinged(fakes: Fakes) -> None:
    fakes.tester.results["frostlog-semantic"] = ContractTestResult(
        "frostlog-semantic", passed=False, failed_checks=["hours_are_dense"]
    )

    result = contract_test(fakes.services)

    assert result["passed"] is False
    assert result["contracts"][1]["failed_checks"] == ["hours_are_dense"]
    assert fakes.pinged == []
    # Both reports are published: a consumer sees the failure without reading logs.
    assert len(fakes.publisher.published) == 2


def test_the_routes_are_wired(fakes: Fakes, finalized: dict) -> None:
    client = TestClient(create_app(fakes.services))

    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.post("/events/gcs", json=finalized).json()["status"] == "built"
    assert client.post("/jobs/contract-test").json()["passed"] is True

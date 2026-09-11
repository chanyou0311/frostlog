"""The two endpoints, end to end against the fakes."""

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from frostlog_semantic.app import chunk_arrived, contract_test, create_app
from frostlog_semantic.contracts import (
    S3_ACCESS_KEY_VARIABLE,
    S3_SECRET_KEY_VARIABLE,
    ContractTestResult,
)
from frostlog_semantic.events import QualityReport, SemanticUpdated, UploadRun
from tests.conftest import RAW_BUCKET, Fakes


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
    result = chunk_arrived(fakes.services, {"bucket": RAW_BUCKET, "name": "logs/upload.txt"})

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
    # The raw contract is read through the bucket's S3 API, which needs the HMAC key.
    assert fakes.tester.environments[0] == {
        S3_ACCESS_KEY_VARIABLE: "GOOG1",
        S3_SECRET_KEY_VARIABLE: "s3cret",
    }


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


def test_tables_that_fell_behind_the_raw_arrivals_fail_the_semantic_contract(
    fakes: Fakes,
) -> None:
    fakes.warehouse.lag_hours = 49.5

    result = contract_test(fakes.services)

    assert result["passed"] is False
    assert result["contracts"][1]["failed_checks"] == ["fresh_within_a_day"]
    assert fakes.pinged == []


def test_an_empty_warehouse_is_not_fresh_yet(fakes: Fakes) -> None:
    fakes.warehouse.lag_hours = None

    result = contract_test(fakes.services)

    assert result["contracts"][1]["failed_checks"] == ["fresh_within_a_day"]


def test_a_contract_test_that_crashes_is_a_finding_not_a_broken_request(fakes: Fakes) -> None:
    # A timeout used to escape as a 500, and the second contract was never tested.
    fakes.tester.raises["frostlog-raw"] = TimeoutError("datacontract test took too long")

    result = contract_test(fakes.services)

    assert result["passed"] is False
    assert result["contracts"][0]["failed_checks"] == ["not tested: TimeoutError"]
    assert result["contracts"][1]["passed"] is True
    assert len(fakes.publisher.published) == 2
    assert fakes.pinged == []


def test_a_publisher_that_fails_does_not_stop_the_run(fakes: Fakes) -> None:
    fakes.publisher.fails = True

    result = contract_test(fakes.services)

    assert result["passed"] is True
    assert fakes.pinged == ["https://hc.example/uuid"]


def test_without_the_hmac_secret_the_raw_contract_is_reported_as_untested(fakes: Fakes) -> None:
    fakes.secrets.payloads = {}

    result = contract_test(fakes.services)

    assert result["passed"] is False
    assert result["contracts"][0]["failed_checks"] == ["not tested: no raw HMAC secret"]
    assert fakes.tester.tested == [("semantic.odcs.yaml", "production")]


def test_the_healthcheck_url_can_come_from_a_secret(fakes: Fakes) -> None:
    fakes.services.settings.contract_test_healthcheck_url = None
    fakes.services.settings.contract_test_healthcheck_url_secret = "frostlog-healthcheck"
    fakes.secrets.payloads["frostlog-healthcheck"] = "https://hc.example/from-secret"

    contract_test(fakes.services)

    assert fakes.pinged == ["https://hc.example/from-secret"]


def test_without_a_healthcheck_url_a_clean_run_simply_reports_nothing(fakes: Fakes) -> None:
    fakes.services.settings.contract_test_healthcheck_url = None

    result = contract_test(fakes.services)

    assert result["passed"] is True
    assert fakes.pinged == []


def test_the_routes_are_wired(fakes: Fakes, finalized: dict) -> None:
    client = TestClient(create_app(fakes.services))

    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.post("/events/gcs", json=finalized).json()["status"] == "built"
    assert client.post("/jobs/contract-test").json()["passed"] is True

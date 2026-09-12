"""The daily contract-test job: what it tests, what it says, and when it stays quiet."""

from frostlog_contracts.__main__ import run
from frostlog_contracts.events import QualityReport
from frostlog_contracts.tester import (
    S3_ACCESS_KEY_VARIABLE,
    S3_SECRET_KEY_VARIABLE,
    ContractTestResult,
)
from tests.conftest import ContractFakes


def _reports(job: ContractFakes) -> list[QualityReport]:
    reports = [event for event in job.publisher.published if isinstance(event, QualityReport)]
    assert len(reports) == len(job.publisher.published)
    return reports


def test_a_clean_run_reports_both_contracts_and_pings(job: ContractFakes) -> None:
    assert run(job.services) is True

    assert [report.contract_id for report in _reports(job)] == [
        "frostlog-collection",
        "frostlog-semantics",
    ]
    assert job.tester.tested == [
        ("collection.odcs.yaml", "gcs"),
        ("semantics.odcs.yaml", "production"),
    ]
    assert job.pinged == ["https://hc.example/uuid"]
    # The collection contract is read through the bucket's S3 API, which needs the HMAC key.
    assert job.tester.environments[0] == {
        S3_ACCESS_KEY_VARIABLE: "GOOG1",
        S3_SECRET_KEY_VARIABLE: "s3cret",
    }


def test_one_contract_can_be_asked_about_on_its_own(job: ContractFakes) -> None:
    """The two products go live at different times; the first is held to its word first."""
    assert run(job.services, ["collection"]) is True

    assert job.tester.tested == [("collection.odcs.yaml", "gcs")]
    assert [report.contract_id for report in _reports(job)] == ["frostlog-collection"]


def test_a_narrowed_run_leaves_the_dead_mans_switch_alone(job: ContractFakes) -> None:
    """The switch says every contract passed; a run that looked at one cannot say that."""
    assert run(job.services, ["collection"]) is True

    assert job.pinged == []


def test_a_failing_check_is_reported_and_the_switch_is_not_pinged(job: ContractFakes) -> None:
    job.tester.results["frostlog-semantics"] = ContractTestResult(
        "frostlog-semantics", passed=False, failed_checks=["hours_are_dense"]
    )

    assert run(job.services) is False

    reports = _reports(job)
    # Both are published: a consumer sees the failure without reading logs.
    assert len(reports) == 2
    assert reports[1].failed_checks == ["hours_are_dense"]
    assert job.pinged == []


def test_a_test_that_crashes_is_a_finding_not_a_stack_trace(job: ContractFakes) -> None:
    # A timeout used to escape, and the second contract was never tested at all.
    job.tester.raises["frostlog-collection"] = TimeoutError("datacontract test took too long")

    assert run(job.services) is False

    reports = _reports(job)
    assert reports[0].failed_checks == ["not tested: TimeoutError"]
    assert reports[1].passed is True
    assert job.pinged == []


def test_a_publisher_that_fails_does_not_stop_the_run(job: ContractFakes) -> None:
    job.publisher.fails = True

    assert run(job.services) is True
    assert job.pinged == ["https://hc.example/uuid"]


def test_without_the_hmac_secret_the_collection_contract_is_untested(job: ContractFakes) -> None:
    job.secrets.payloads = {}

    assert run(job.services) is False

    assert _reports(job)[0].failed_checks == ["not tested: no collection HMAC secret"]
    assert job.tester.tested == [("semantics.odcs.yaml", "production")]


def test_the_healthcheck_url_can_come_from_a_secret(job: ContractFakes) -> None:
    job.services.settings.contract_test_healthcheck_url = None
    job.services.settings.contract_test_healthcheck_url_secret = "frostlog-healthcheck"
    job.secrets.payloads["frostlog-healthcheck"] = "https://hc.example/from-secret"

    run(job.services)

    assert job.pinged == ["https://hc.example/from-secret"]


def test_without_a_healthcheck_url_a_clean_run_simply_reports_nothing(job: ContractFakes) -> None:
    job.services.settings.contract_test_healthcheck_url = None

    assert run(job.services) is True
    assert job.pinged == []

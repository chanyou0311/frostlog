"""Reading a datacontract-cli report, and handing it the bucket's HMAC key."""

import json

from frostlog_contracts.tester import (
    S3_ACCESS_KEY_VARIABLE,
    S3_SECRET_KEY_VARIABLE,
    failed_checks,
    s3_credentials,
)


def _report(*checks: tuple[str, str]) -> str:
    return json.dumps(
        {
            "runId": "8b1f",
            "result": "failed",
            "checks": [{"name": name, "result": result} for name, result in checks],
        }
    )


def test_a_clean_report_names_nothing() -> None:
    assert failed_checks(_report(("hours_are_dense", "passed"))) == []


def test_failed_and_errored_checks_are_both_findings() -> None:
    report = _report(
        ("one_row_per_hour", "passed"),
        ("hours_are_dense", "failed"),
        ("fresh_within_a_day", "error"),
        ("ratios_in_range", "skipped"),
    )

    assert failed_checks(report) == ["hours_are_dense", "fresh_within_a_day"]


def test_a_check_without_a_name_is_reported_by_its_type() -> None:
    report = json.dumps({"checks": [{"type": "field_is_present", "result": "failed"}]})

    assert failed_checks(report) == ["field_is_present"]


def test_a_report_without_checks_names_nothing() -> None:
    assert failed_checks(json.dumps({"runId": "8b1f", "result": "passed"})) == []


def test_the_hmac_secret_becomes_the_two_variables_datacontract_reads() -> None:
    assert s3_credentials('{"access_id": "GOOG1EX", "secret": "s3cret"}') == {
        S3_ACCESS_KEY_VARIABLE: "GOOG1EX",
        S3_SECRET_KEY_VARIABLE: "s3cret",
    }


def test_a_secret_of_another_shape_is_no_credentials_at_all() -> None:
    assert s3_credentials("not json") is None
    assert s3_credentials('{"access_id": "GOOG1EX"}') is None

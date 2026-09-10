"""Reading a datacontract-cli report."""

import json

from frostlog_semantic.contracts import failed_checks


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

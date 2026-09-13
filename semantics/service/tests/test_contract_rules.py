"""The contract's SQL rules, run against small tables (DuckDB) to pin their edge cases.

BigQuery is not available here; the rules are transpiled with sqlglot. What this
pins is the logic of the rule, not BigQuery's own behaviour.
"""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")
sqlglot = pytest.importorskip("sqlglot")
yaml = pytest.importorskip("yaml")

CONTRACT = Path(__file__).resolve().parents[3] / "contracts" / "semantics.odcs.yaml"
FACT = "fact_cooler_state_update"
NOW = datetime.now(UTC)
LANDED = NOW - timedelta(hours=6)  # arrived well past the two hours the rule waits
JUST_LANDED = NOW - timedelta(minutes=10)  # still inside the wait

PAYLOAD = (
    "{'serial_number': 'S1', 'setpoint_celsius': -20, 'interior_temperature_celsius': -18, "
    "'state_of_charge_percent': 80, 'input_watts': 0, 'charge_watts': 0, 'discharge_watts': 30, "
    "'usb_a_output_watts': 0, 'usb_c_output_watts': 0, 'battery_state': 'discharging', "
    "'display_unit': 'C', 'protection_level': 'medium', 'brightness': 'low'}"
)


def rule(name: str) -> str:
    contract = yaml.safe_load(CONTRACT.read_text())
    for table in contract["schema"]:
        for quality in table.get("quality", []):
            if quality.get("name") == name:
                # The contract qualifies every table with the server's project and
                # dataset, because BigQuery will not resolve a bare name. DuckDB has
                # one schema here, so the prefix is dropped rather than reproduced.
                bigquery_sql = re.sub(r"\{project}\.\{dataset}\.", "", quality["query"]).replace(
                    "{model}", FACT
                )
                return sqlglot.transpile(bigquery_sql, read="bigquery", write="duckdb")[0]
    raise KeyError(name)


def warehouse(raw: list[tuple], fact: list[tuple]) -> duckdb.DuckDBPyConnection:
    """raw rows: (boot_id, uptime, landed_at, decodable); fact rows: (boot_id, uptime).

    ``_PARTITIONTIME`` is a pseudo-column in BigQuery and an ordinary one here; what
    the test pins is that the rule waits on when the row landed, not on when the
    cooler spoke (``ts``, which every row here shares).
    """
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE raw_cooler (boot_id VARCHAR, uptime_seconds DOUBLE, ts TIMESTAMP, "
        "cmd VARCHAR, _PARTITIONTIME TIMESTAMPTZ, payload STRUCT("
        "serial_number VARCHAR, setpoint_celsius INTEGER, interior_temperature_celsius INTEGER, "
        "state_of_charge_percent INTEGER, input_watts INTEGER, charge_watts INTEGER, "
        "discharge_watts INTEGER, usb_a_output_watts INTEGER, usb_c_output_watts INTEGER, "
        "battery_state VARCHAR, display_unit VARCHAR, protection_level VARCHAR, "
        "brightness VARCHAR))"
    )
    con.execute(f"CREATE TABLE {FACT} (boot_id VARCHAR, uptime_seconds DOUBLE)")
    for boot_id, uptime, landed_at, decodable in raw:
        payload = PAYLOAD if decodable else "NULL"
        con.execute(
            "INSERT INTO raw_cooler VALUES (?, ?, TIMESTAMP '2026-09-01 00:00:00', '4402', ?, "
            f"{payload})",
            [boot_id, uptime, landed_at],
        )
    for boot_id, uptime in fact:
        con.execute(f"INSERT INTO {FACT} VALUES (?, ?)", [boot_id, uptime])
    return con


def unreflected(raw: list[tuple], fact: list[tuple]) -> int:
    return warehouse(raw, fact).execute(rule("every_report_is_reflected")).fetchone()[0]


def test_a_chunk_that_is_fully_reflected_passes() -> None:
    assert unreflected([("b", 1, LANDED, True), ("b", 2, LANDED, True)], [("b", 1), ("b", 2)]) == 0


def test_a_chunk_that_was_never_built_fails() -> None:
    assert unreflected([("b", 1, LANDED, True), ("b", 2, LANDED, True)], []) == 2


def test_a_chunk_of_which_only_some_reports_were_loaded_fails() -> None:
    assert unreflected([("b", 1, LANDED, True), ("b", 2, LANDED, True)], [("b", 1)]) == 1


def test_a_report_delivered_twice_counts_once() -> None:
    # A re-cut chunk brings the same report again, in a later load. The staging
    # model keeps one copy, so one row in the fact settles both.
    raw = [("b", 1, LANDED, True), ("b", 1, LANDED + timedelta(hours=1), True)]
    assert unreflected(raw, [("b", 1)]) == 0


def test_a_report_that_only_just_landed_is_not_due_yet() -> None:
    # The transfer runs every fifteen minutes and the build every hour, so a row
    # this new has not had a build to be in. This is the case that would fail the
    # nightly check if the rule had no wait. Its `ts` is old, as every row here
    # has: the wait is owed from when it landed, not from when it was recorded.
    assert unreflected([("b", 1, JUST_LANDED, True)], []) == 0


def test_a_report_the_staging_model_drops_is_not_expected_in_the_table() -> None:
    assert unreflected([("b", 1, LANDED, False)], []) == 0

"""The contract's SQL rules, run against small tables (DuckDB) to pin their edge cases.

BigQuery is not available here; the rules are transpiled with sqlglot. What this
pins is the logic of the rule, not BigQuery's own behaviour.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")
sqlglot = pytest.importorskip("sqlglot")
yaml = pytest.importorskip("yaml")

CONTRACT = Path(__file__).resolve().parents[3] / "contracts" / "semantics.odcs.yaml"
FACT = "fact_cooler_state_update"
NOW = datetime.now(UTC)
OLD = NOW - timedelta(hours=48)  # arrived well beyond the 24 h the rule allows
RECENT = NOW - timedelta(hours=1)

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
                bigquery_sql = quality["query"].replace("{model}", FACT)
                return sqlglot.transpile(bigquery_sql, read="bigquery", write="duckdb")[0]
    raise KeyError(name)


def warehouse(raw: list[tuple], fact: list[tuple]) -> duckdb.DuckDBPyConnection:
    """raw rows: (boot_id, uptime, source_key, uploaded_at, decodable); fact: (boot_id, uptime)."""
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE raw_cooler (boot_id VARCHAR, uptime_seconds DOUBLE, ts TIMESTAMP, "
        "cmd VARCHAR, source_key VARCHAR, uploaded_at TIMESTAMPTZ, payload STRUCT("
        "serial_number VARCHAR, setpoint_celsius INTEGER, interior_temperature_celsius INTEGER, "
        "state_of_charge_percent INTEGER, input_watts INTEGER, charge_watts INTEGER, "
        "discharge_watts INTEGER, usb_a_output_watts INTEGER, usb_c_output_watts INTEGER, "
        "battery_state VARCHAR, display_unit VARCHAR, protection_level VARCHAR, "
        "brightness VARCHAR))"
    )
    con.execute(f"CREATE TABLE {FACT} (boot_id VARCHAR, uptime_seconds DOUBLE, source_key VARCHAR)")
    for boot_id, uptime, source_key, uploaded_at, decodable in raw:
        payload = PAYLOAD if decodable else "NULL"
        con.execute(
            "INSERT INTO raw_cooler VALUES (?, ?, TIMESTAMP '2026-09-01 00:00:00', '4402', ?, ?, "
            f"{payload})",
            [boot_id, uptime, source_key, uploaded_at],
        )
    for boot_id, uptime in fact:
        con.execute(f"INSERT INTO {FACT} VALUES (?, ?, 'any')", [boot_id, uptime])
    return con


def unreflected(raw: list[tuple], fact: list[tuple]) -> int:
    return warehouse(raw, fact).execute(rule("fresh_within_a_day")).fetchone()[0]


def test_an_old_chunk_that_is_fully_reflected_passes() -> None:
    assert (
        unreflected([("b", 1, "A", OLD, True), ("b", 2, "A", OLD, True)], [("b", 1), ("b", 2)]) == 0
    )


def test_an_old_chunk_that_was_never_loaded_fails() -> None:
    assert unreflected([("b", 1, "A", OLD, True), ("b", 2, "A", OLD, True)], []) == 2


def test_a_chunk_of_which_only_some_reports_were_loaded_fails() -> None:
    assert unreflected([("b", 1, "A", OLD, True), ("b", 2, "A", OLD, True)], [("b", 1)]) == 1


def test_a_report_superseded_by_a_later_chunk_counts_as_reflected() -> None:
    # The same report in chunks A and B; the staging model keeps B's copy.
    raw = [("b", 1, "A", OLD, True), ("b", 1, "B", OLD + timedelta(hours=1), True)]
    assert unreflected(raw, [("b", 1)]) == 0


def test_a_recent_arrival_is_not_due_yet() -> None:
    assert unreflected([("b", 1, "A", RECENT, True)], []) == 0


def test_a_report_the_staging_model_drops_is_not_expected_in_the_table() -> None:
    assert unreflected([("b", 1, "A", OLD, False)], []) == 0

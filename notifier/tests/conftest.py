"""A warehouse that answers by statement name, and rows that look like real days."""

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from frostlog_notifier.settings import Settings
from frostlog_notifier.slack import Posted
from frostlog_notifier.state import PostedNotifications
from frostlog_notifier.warehouse import Parameters, Row

#: Canned rows for one statement, or a function of the statement's parameters.
Answer = list[dict[str, Any]] | Callable[[dict[str, Any]], list[dict[str, Any]]]

_NAME = re.compile(r"--\s*name:\s*(\w+)")


def statement_name(sql: str) -> str:
    match = _NAME.search(sql)
    assert match, f"statement has no -- name: comment:\n{sql}"
    return match.group(1)


class FakeWarehouse:
    """Answers the notifier's queries from canned rows, and keeps the posted table itself."""

    def __init__(self, answers: dict[str, Any] | None = None) -> None:
        self.answers: dict[str, Any] = answers or {}
        self.posted: list[dict[str, Any]] = []
        self.queried: list[tuple[str, dict[str, Any]]] = []
        self.executed: list[tuple[str, dict[str, Any]]] = []

    def table(self, name: str) -> str:
        return f"`frostlog-test.frostlog.{name}`"

    def rows(self, sql: str, parameters: Parameters | None = None) -> list[Row]:
        name = statement_name(sql)
        given = dict(parameters or {})
        self.queried.append((name, given))
        if name == "is_posted":
            matched = [
                row
                for row in self.posted
                if row["kind"] == given["kind"] and row["key"] == given["key"]
            ]
            return [{"posted_count": len(matched)}]
        if name == "posted_keys":
            return [
                {"key": row["key"]}
                for row in self.posted
                if row["kind"] == given["kind"] and row["key"] in given["keys"]
            ]
        if name == "latest_coverage_end":
            ends = [
                row["coverage_end"]
                for row in self.posted
                if row["kind"] == given["kind"] and row.get("coverage_end") is not None
            ]
            return [{"coverage_end": max(ends)}] if ends else []
        answer: Any = self.answers.get(name, [])
        rows: list[Row] = answer(given) if callable(answer) else answer
        return rows

    def execute(self, sql: str, parameters: Parameters | None = None) -> None:
        name = statement_name(sql)
        given = dict(parameters or {})
        self.executed.append((name, given))
        if name == "record_posted":
            self.posted.append(given)


class FakeSlack:
    """Records what would have been posted; without a token it only counts dry runs."""

    def __init__(self, enabled: bool = True, fail: Exception | None = None) -> None:
        self.enabled = enabled
        self.fail = fail
        self.messages: list[tuple[str, bytes | None, str]] = []
        self.dry_runs: list[str] = []

    def post(self, text: str, image: bytes | None = None, filename: str = "chart.png") -> Posted:
        if self.fail is not None:
            raise self.fail
        if not self.enabled:
            self.dry_runs.append(text)
            return Posted(slack_timestamp=None, dry_run=True)
        self.messages.append((text, image, filename))
        return Posted(slack_timestamp=f"170000000.{len(self.messages):06d}", dry_run=False)


@pytest.fixture
def warehouse() -> FakeWarehouse:
    return FakeWarehouse()


@pytest.fixture
def posted(warehouse: FakeWarehouse) -> PostedNotifications:
    return PostedNotifications(warehouse, "notifier_posted")


@pytest.fixture
def slack() -> FakeSlack:
    return FakeSlack()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        bigquery_dataset="frostlog",
        slack_bot_token=None,
        slack_channel="#fumo",
    )


@pytest.fixture(autouse=True)
def _quiet_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)


# ------------------------------------------------------------------ rows


def at(day: str, hour: int, minute: int = 0) -> datetime:
    """A UTC timestamp, written the way the warehouse stores it."""
    year, month, day_of_month = (int(part) for part in day.split("-"))
    return datetime(year, month, day_of_month, hour, minute, tzinfo=UTC)


def state_update(updated_at: datetime, **overrides: Any) -> dict[str, Any]:
    row = {
        "updated_at": updated_at,
        "setpoint_celsius": -20,
        "interior_temperature_celsius": -18,
        "state_of_charge_percent": 62,
        "battery_state": "discharging",
        "external_input": False,
        "input_watts": 0,
        "charge_watts": 0,
        "discharge_watts": 32,
        "ambient_temperature_celsius": 24.5,
    }
    return row | overrides


def hourly(
    hour_started_at: datetime,
    state_of_charge_end_percent: int,
    delta: int = -1,
    **overrides: Any,
) -> dict[str, Any]:
    row = {
        "hour_started_at": hour_started_at,
        "covered_seconds": 3600.0,
        "state_of_charge_start_percent": state_of_charge_end_percent - delta,
        "state_of_charge_end_percent": state_of_charge_end_percent,
        "state_of_charge_delta_percent": delta,
        "discharged_watt_hours": 28.0,
        "charged_watt_hours": 0.0,
        "interior_temperature_celsius": -18.4,
        "setpoint_celsius": -20.0,
        "ambient_temperature_celsius": 26.0,
        "external_input_ratio": 0.0,
        "charging_ratio": 0.0,
    }
    return row | overrides


def empty_hour(hour_started_at: datetime) -> dict[str, Any]:
    return {
        "hour_started_at": hour_started_at,
        "covered_seconds": 0.0,
        "state_of_charge_start_percent": None,
        "state_of_charge_end_percent": None,
        "state_of_charge_delta_percent": None,
        "discharged_watt_hours": None,
        "charged_watt_hours": None,
        "interior_temperature_celsius": None,
        "setpoint_celsius": None,
        "ambient_temperature_celsius": None,
        "external_input_ratio": None,
        "charging_ratio": None,
    }


def pulldown_row(started_at: datetime, **overrides: Any) -> dict[str, Any]:
    """A pull-down like the real one: 12 °C down to -19 °C in 46 minutes, 100 % to 88 %."""
    row = {
        "pulldown_key": f"pd-{started_at:%Y%m%d%H%M}",
        "started_at": started_at,
        "reached_at": started_at + timedelta(minutes=46),
        "ended_at": started_at + timedelta(minutes=46),
        "trigger": "start_up",
        "outcome": "reached",
        "interior_temperature_start_celsius": 12,
        "setpoint_celsius": -20,
        "state_of_charge_start_percent": 100,
        "state_of_charge_delta_percent": -12,
        "duration_seconds": 46 * 60.0,
        "elapsed_seconds": 46 * 60.0,
        "discharged_watt_hours": 52.1,
        "charged_watt_hours": 0.0,
        "ambient_temperature_celsius": 27.3,
        "external_input_ratio": 0.0,
    }
    return row | overrides


def upload_run(
    finished_at: datetime,
    started_at: datetime | None = None,
    previous_finished_at: datetime | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """One entry of the semantic_updated event's upload_runs array."""
    row = {
        "finished_at": finished_at,
        "started_at": started_at,
        "previous_finished_at": previous_finished_at,
        "chunk_count": 3,
        "line_count": 412,
    }
    return row | overrides


def ambient_band_rows() -> list[dict[str, Any]]:
    """The seeded cabin temperature bands: 5 °C steps from < 5 to >= 40."""
    bands = [{"band_key": 1, "label": "< 5 °C", "lower_celsius": None, "upper_celsius": 5.0}]
    for index, lower in enumerate(range(5, 40, 5), start=2):
        bands.append(
            {
                "band_key": index,
                "label": f"{lower}..{lower + 5} °C",
                "lower_celsius": float(lower),
                "upper_celsius": float(lower + 5),
            }
        )
    bands.append({"band_key": 9, "label": ">= 40 °C", "lower_celsius": 40.0, "upper_celsius": None})
    return [band | {"sort_order": band["band_key"]} for band in bands]

"""A warehouse that answers by statement name, and rows that look like real days."""

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from frostlog_notifier.settings import Settings
from frostlog_notifier.slack import Posted
from frostlog_notifier.warehouse import Parameters, Row

#: Canned rows for one statement, or a function of the statement's parameters.
Answer = list[dict[str, Any]] | Callable[[dict[str, Any]], list[dict[str, Any]]]

_NAME = re.compile(r"--\s*name:\s*(\w+)")


def statement_name(sql: str) -> str:
    match = _NAME.search(sql)
    assert match, f"statement has no -- name: comment:\n{sql}"
    return match.group(1)


class FakeWarehouse:
    """Answers the notifier's queries from canned rows.

    It has no writes to record: the notifier reads the warehouse and says what it
    finds, and remembers nothing between runs.
    """

    def __init__(self, answers: dict[str, Any] | None = None) -> None:
        self.answers: dict[str, Any] = answers or {}
        self.queried: list[tuple[str, dict[str, Any]]] = []

    def table(self, name: str) -> str:
        return f"`frostlog-test.frostlog.{name}`"

    def rows(self, sql: str, parameters: Parameters | None = None) -> list[Row]:
        name = statement_name(sql)
        # The notifier only reads. A protocol without a write method cannot stop a
        # DELETE handed to rows(), so the fake does.
        first_word = next(
            (
                line.split()[0].upper()
                for line in sql.splitlines()
                if line.strip() and not line.lstrip().startswith("--")
            ),
            "",
        )
        assert first_word in {"SELECT", "WITH"}, (
            f"the notifier must not write to the warehouse: {name}"
        )
        given = dict(parameters or {})
        self.queried.append((name, given))
        answer: Any = self.answers.get(name, [])
        rows: list[Row] = answer(given) if callable(answer) else answer
        return rows


class FakeSlack:
    """Records what would have been posted; without a token it only counts dry runs."""

    def __init__(self, enabled: bool = True, fail: Exception | None = None) -> None:
        self.enabled = enabled
        self.fail = fail
        self.messages: list[tuple[str, list[dict] | None]] = []
        self.dry_runs: list[str] = []

    def post(self, text: str, blocks: list[dict] | None = None) -> Posted:
        if self.fail is not None:
            raise self.fail
        if not self.enabled:
            self.dry_runs.append(text)
            return Posted(slack_timestamp=None, dry_run=True)
        self.messages.append((text, blocks))
        return Posted(slack_timestamp=f"170000000.{len(self.messages):06d}", dry_run=False)


@pytest.fixture
def warehouse() -> FakeWarehouse:
    return FakeWarehouse()


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


def slot(
    slot_started_at: datetime,
    state_of_charge_end_percent: int,
    delta: int = -1,
    **overrides: Any,
) -> dict[str, Any]:
    """One quarter hour of fact_cooler_snapshot."""
    row = {
        "slot_started_at": slot_started_at,
        "covered_seconds": 900.0,
        "state_of_charge_start_percent": state_of_charge_end_percent - delta,
        "state_of_charge_end_percent": state_of_charge_end_percent,
        "state_of_charge_delta_percent": delta,
        "discharged_watt_hours": 7.0,
        "charged_watt_hours": 0.0,
        "interior_temperature_celsius": -18.4,
        "ambient_temperature_celsius": 26.0,
        "external_input_ratio": 0.0,
    }
    return row | overrides


def empty_slot(slot_started_at: datetime) -> dict[str, Any]:
    return {
        "slot_started_at": slot_started_at,
        "covered_seconds": 0.0,
        "state_of_charge_start_percent": None,
        "state_of_charge_end_percent": None,
        "state_of_charge_delta_percent": None,
        "discharged_watt_hours": None,
        "charged_watt_hours": None,
        "interior_temperature_celsius": None,
        "ambient_temperature_celsius": None,
        "external_input_ratio": None,
    }


def quarters(
    hour_started_at: datetime,
    state_of_charge_end_percent: int,
    delta: int = -1,
    **overrides: Any,
) -> list[dict[str, Any]]:
    """An hour as the four slots it is made of, so that folding them gives it back.

    The watt-hours are quartered and the change is carried by the first slot; what
    matters to a reader of the folded hour is that the seconds, the watt-hours and the
    ends agree with the hour it stands for.
    """
    per_slot = {
        key: (value / 4 if key.endswith("watt_hours") and value is not None else value)
        for key, value in overrides.items()
    }
    rows = [
        slot(
            hour_started_at + timedelta(minutes=15 * index),
            state_of_charge_end_percent,
            delta=delta if index == 0 else 0,
            **per_slot,
        )
        for index in range(4)
    ]
    rows[0]["state_of_charge_start_percent"] = state_of_charge_end_percent - delta
    return rows


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


def ambient_band_rows() -> list[dict[str, Any]]:
    """The seeded ambient temperature bands: 5 °C steps from < 5 to >= 40."""
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

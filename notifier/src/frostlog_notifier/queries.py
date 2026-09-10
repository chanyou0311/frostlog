"""The queries the notifications are built from, and the rows they return.

Every statement opens with a ``-- name:`` comment; that name identifies the
query in logs and in the tests' fake warehouse. Columns and tables are those of
contracts/semantic.odcs.yaml, except ``raw_events``, which is the raw
collector event stream as the transform loads it.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from frostlog_notifier.warehouse import Warehouse


class _Row(BaseModel):
    model_config = ConfigDict(extra="ignore")


class StateUpdate(_Row):
    updated_at: datetime
    setpoint_celsius: int
    interior_temperature_celsius: int
    state_of_charge_percent: int
    battery_state: str
    external_input: bool
    input_watts: int
    charge_watts: int
    discharge_watts: int
    ambient_temperature_celsius: float | None = None
    ambient_humidity_percent: float | None = None


class HourlySnapshot(_Row):
    hour_started_at: datetime
    date_key: int
    hour_of_day: int
    covered_seconds: float
    state_of_charge_start_percent: int | None = None
    state_of_charge_end_percent: int | None = None
    state_of_charge_delta_percent: int | None = None
    discharged_watt_hours: float | None = None
    charged_watt_hours: float | None = None
    interior_temperature_celsius: float | None = None
    setpoint_celsius: float | None = None
    ambient_temperature_celsius: float | None = None
    external_input_ratio: float | None = None
    charging_ratio: float | None = None


class Pulldown(_Row):
    pulldown_key: str
    started_at: datetime
    trigger: str
    interior_temperature_start_celsius: int
    setpoint_celsius: int
    state_of_charge_start_percent: int
    reached_at: datetime | None = None
    ended_at: datetime | None = None
    outcome: str | None = None
    state_of_charge_delta_percent: int | None = None
    duration_seconds: float | None = None
    elapsed_seconds: float | None = None
    discharged_watt_hours: float | None = None
    charged_watt_hours: float | None = None
    ambient_temperature_celsius: float | None = None
    external_input_ratio: float | None = None


class Band(_Row):
    band_key: int
    label: str
    sort_order: int
    lower_celsius: float | None = None
    upper_celsius: float | None = None


class Energy(_Row):
    discharged_watt_hours: float
    charged_watt_hours: float


_STATE_UPDATE_COLUMNS = """
  updated_at, setpoint_celsius, interior_temperature_celsius, state_of_charge_percent,
  battery_state, external_input, input_watts, charge_watts, discharge_watts,
  ambient_temperature_celsius, ambient_humidity_percent
"""

_PULLDOWN_COLUMNS = """
  pulldown_key, started_at, reached_at, ended_at, trigger, outcome,
  interior_temperature_start_celsius, setpoint_celsius, state_of_charge_start_percent,
  state_of_charge_delta_percent, duration_seconds, elapsed_seconds,
  discharged_watt_hours, charged_watt_hours, ambient_temperature_celsius, external_input_ratio
"""


def latest_state_update(warehouse: Warehouse) -> StateUpdate | None:
    """The most recent state of the cooler; None while no data has arrived at all."""
    sql = f"""
      -- name: latest_state_update
      SELECT {_STATE_UPDATE_COLUMNS}
      FROM {warehouse.table("fact_cooler_state_update")}
      ORDER BY updated_at DESC
      LIMIT 1
    """
    rows = warehouse.rows(sql)
    return StateUpdate.model_validate(rows[0]) if rows else None


def upload_started_times(
    warehouse: Warehouse, raw_events_table: str, limit: int = 2
) -> list[datetime]:
    """Timestamps of the last upload runs the Pi started, newest first."""
    sql = f"""
      -- name: upload_started_times
      SELECT ts
      FROM {warehouse.table(raw_events_table)}
      WHERE kind = 'upload_started'
      ORDER BY ts DESC
      LIMIT @limit
    """
    return [row["ts"] for row in warehouse.rows(sql, {"limit": limit})]


def energy_between(warehouse: Warehouse, start: datetime, end: datetime) -> Energy:
    """Battery energy discharged and charged in (start, end], integrated over held time."""
    sql = f"""
      -- name: energy_between
      SELECT
        COALESCE(SUM(discharge_watts * held_seconds / 3600), 0.0) AS discharged_watt_hours,
        COALESCE(SUM(charge_watts * held_seconds / 3600), 0.0) AS charged_watt_hours
      FROM {warehouse.table("fact_cooler_state_update")}
      WHERE updated_at > @start AND updated_at <= @end
    """
    rows = warehouse.rows(sql, {"start": start, "end": end})
    if not rows:
        return Energy(discharged_watt_hours=0.0, charged_watt_hours=0.0)
    return Energy.model_validate(rows[0])


def state_updates_between(
    warehouse: Warehouse, start: datetime, end: datetime
) -> list[StateUpdate]:
    """Every state update in [start, end], oldest first."""
    sql = f"""
      -- name: state_updates_between
      SELECT {_STATE_UPDATE_COLUMNS}
      FROM {warehouse.table("fact_cooler_state_update")}
      WHERE updated_at BETWEEN @start AND @end
      ORDER BY updated_at
    """
    rows = warehouse.rows(sql, {"start": start, "end": end})
    return [StateUpdate.model_validate(row) for row in rows]


def hourly_snapshots(warehouse: Warehouse, start: datetime, end: datetime) -> list[HourlySnapshot]:
    """Hourly rows whose hour starts in [start, end), oldest first (including empty hours)."""
    sql = f"""
      -- name: hourly_snapshots
      SELECT
        hour_started_at, date_key, hour_of_day, covered_seconds,
        state_of_charge_start_percent, state_of_charge_end_percent,
        state_of_charge_delta_percent, discharged_watt_hours, charged_watt_hours,
        interior_temperature_celsius, setpoint_celsius, ambient_temperature_celsius,
        external_input_ratio, charging_ratio
      FROM {warehouse.table("fact_cooler_hourly_snapshot")}
      WHERE hour_started_at >= @start AND hour_started_at < @end
      ORDER BY hour_started_at
    """
    rows = warehouse.rows(sql, {"start": start, "end": end})
    return [HourlySnapshot.model_validate(row) for row in rows]


def finished_pulldowns_between(
    warehouse: Warehouse, start: datetime, end: datetime
) -> list[Pulldown]:
    """Pull-downs that finished (reached or interrupted) in (start, end], oldest first."""
    sql = f"""
      -- name: finished_pulldowns_between
      SELECT {_PULLDOWN_COLUMNS}
      FROM {warehouse.table("fact_cooler_pulldown")}
      WHERE outcome IS NOT NULL AND ended_at > @start AND ended_at <= @end
      ORDER BY ended_at
    """
    rows = warehouse.rows(sql, {"start": start, "end": end})
    return [Pulldown.model_validate(row) for row in rows]


def ambient_bands(warehouse: Warehouse) -> list[Band]:
    """The cabin temperature bands, in display order."""
    sql = f"""
      -- name: ambient_bands
      SELECT band_key, label, lower_celsius, upper_celsius, sort_order
      FROM {warehouse.table("band_ambient_temperature")}
      ORDER BY sort_order
    """
    return [Band.model_validate(row) for row in warehouse.rows(sql)]

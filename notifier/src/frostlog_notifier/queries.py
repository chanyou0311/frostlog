"""The queries the notifications are built from, and the rows they return.

Every statement opens with a ``-- name:`` comment; that name identifies the
query in logs and in the tests' fake warehouse. Columns and tables are only
those of contracts/semantics.odcs.yaml: the raw tables behind it are the semantic
data product's business, not this application's.
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


class HourlySnapshot(_Row):
    hour_started_at: datetime
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


class Snapshot(_Row):
    """One step of a chart: what was true over it, weighted by how long each state held."""

    started_at: datetime
    covered_seconds: float
    state_of_charge_end_percent: int | None = None
    interior_temperature_celsius: float | None = None
    ambient_temperature_celsius: float | None = None
    external_input_ratio: float | None = None


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
  ambient_temperature_celsius
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


def latest_state_update_at(warehouse: Warehouse, moment: datetime) -> StateUpdate | None:
    """The most recent state of the cooler at or before ``moment``."""
    sql = f"""
      -- name: latest_state_update_at
      SELECT {_STATE_UPDATE_COLUMNS}
      FROM {warehouse.table("fact_cooler_state_update")}
      WHERE updated_at <= @moment
      ORDER BY updated_at DESC
      LIMIT 1
    """
    rows = warehouse.rows(sql, {"moment": moment})
    return StateUpdate.model_validate(rows[0]) if rows else None


def state_update_count_between(warehouse: Warehouse, start: datetime, end: datetime) -> int:
    """How many state updates fall in [start, end); zero means the cooler said nothing."""
    sql = f"""
      -- name: state_update_count_between
      SELECT COUNT(*) AS update_count
      FROM {warehouse.table("fact_cooler_state_update")}
      WHERE updated_at >= @start AND updated_at < @end
    """
    rows = warehouse.rows(sql, {"start": start, "end": end})
    return int(rows[0]["update_count"]) if rows else 0


def energy_between(warehouse: Warehouse, start: datetime, end: datetime) -> Energy:
    """Battery energy discharged and charged in (start, end] (the fact's own Wh columns)."""
    sql = f"""
      -- name: energy_between
      SELECT
        COALESCE(SUM(discharged_watt_hours), 0.0) AS discharged_watt_hours,
        COALESCE(SUM(charged_watt_hours), 0.0) AS charged_watt_hours
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


def snapshots(
    warehouse: Warehouse, start: datetime, end: datetime, step_seconds: int
) -> list[Snapshot]:
    """The period in steps of ``step_seconds``, oldest first, aggregated by BigQuery.

    fact_cooler_hourly_snapshot is dense and cheap but an hour is too coarse for a
    chart of a two-hour trip. The atomic fact is as fine as the cooler talks —
    every few seconds — so the step is chosen by whoever is drawing, and the
    rolling up happens there rather than here: what comes back is one row per
    step, never the thousands behind them.

    A step nothing was recorded in has no row at all, which is how the caller
    tells a silence from a reading.
    """
    sql = f"""
      -- name: snapshots
      SELECT
        TIMESTAMP_SECONDS(DIV(UNIX_SECONDS(updated_at), @step) * @step) AS started_at,
        SUM(held_seconds) AS covered_seconds,
        ARRAY_AGG(state_of_charge_percent ORDER BY updated_at DESC LIMIT 1)[SAFE_OFFSET(0)]
          AS state_of_charge_end_percent,
        SAFE_DIVIDE(
          SUM(interior_temperature_celsius * held_seconds), SUM(held_seconds)
        ) AS interior_temperature_celsius,
        SAFE_DIVIDE(
          SUM(ambient_temperature_celsius * held_seconds),
          SUM(IF(ambient_temperature_celsius IS NULL, 0, held_seconds))
        ) AS ambient_temperature_celsius,
        SAFE_DIVIDE(
          SUM(IF(external_input, held_seconds, 0)), SUM(held_seconds)
        ) AS external_input_ratio
      FROM {warehouse.table("fact_cooler_state_update")}
      WHERE updated_at >= @start AND updated_at < @end
      GROUP BY started_at
      ORDER BY started_at
    """
    rows = warehouse.rows(sql, {"start": start, "end": end, "step": step_seconds})
    return [Snapshot.model_validate(row) for row in rows]


def hourly_snapshots(warehouse: Warehouse, start: datetime, end: datetime) -> list[HourlySnapshot]:
    """Hourly rows whose hour starts in [start, end), oldest first (including empty hours)."""
    sql = f"""
      -- name: hourly_snapshots
      SELECT
        hour_started_at, covered_seconds,
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
    """The ambient temperature bands, in display order."""
    sql = f"""
      -- name: ambient_bands
      SELECT band_key, label, lower_celsius, upper_celsius, sort_order
      FROM {warehouse.table("band_ambient_temperature")}
      ORDER BY sort_order
    """
    return [Band.model_validate(row) for row in warehouse.rows(sql)]

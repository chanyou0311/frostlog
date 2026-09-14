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


class Snapshot(_Row):
    """One quarter hour of fact_cooler_snapshot."""

    slot_started_at: datetime
    covered_seconds: float
    state_of_charge_start_percent: int | None = None
    state_of_charge_end_percent: int | None = None
    state_of_charge_delta_percent: int | None = None
    discharged_watt_hours: float | None = None
    charged_watt_hours: float | None = None
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


def weeks_with_state_updates(warehouse: Warehouse, start: datetime, end: datetime) -> set[str]:
    """Which JST ISO weeks in [start, end) the cooler said anything in, as `YYYY-Www`.

    One query for the whole backlog: asking week by week costs the ten-mebibyte
    minimum eight times over, and the answer is the same grouping BigQuery would
    do anyway. The week is taken in JST because that is the week the summary is
    about, and DATE_TRUNC on an ISOWEEK starts it on the Monday.
    """
    sql = f"""
      -- name: weeks_with_state_updates
      SELECT
        FORMAT(
          '%d-W%02d',
          EXTRACT(ISOYEAR FROM DATETIME(updated_at, 'Asia/Tokyo')),
          EXTRACT(ISOWEEK FROM DATETIME(updated_at, 'Asia/Tokyo'))
        ) AS week_key
      FROM {warehouse.table("fact_cooler_state_update")}
      WHERE updated_at >= @start AND updated_at < @end
      GROUP BY week_key
    """
    return {str(row["week_key"]) for row in warehouse.rows(sql, {"start": start, "end": end})}


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


def snapshots(warehouse: Warehouse, start: datetime, end: datetime) -> list[Snapshot]:
    """Quarter-hour rows whose slot starts in [start, end), oldest first, holes included.

    The grain is the semantic product's, not this one's: what a slot means — how its
    seconds were counted, how a report straddling its edge was split — is decided
    there and tested against the contract. All this does is read them. Folding four
    into an hour, or eight into two, is presentation and belongs where the drawing is;
    the contract says which columns add and which weight by covered_seconds.
    """
    sql = f"""
      -- name: snapshots
      SELECT
        slot_started_at, covered_seconds,
        state_of_charge_start_percent, state_of_charge_end_percent,
        state_of_charge_delta_percent, discharged_watt_hours, charged_watt_hours,
        interior_temperature_celsius, ambient_temperature_celsius, external_input_ratio
      FROM {warehouse.table("fact_cooler_snapshot")}
      WHERE slot_started_at >= @start AND slot_started_at < @end
      ORDER BY slot_started_at
    """
    rows = warehouse.rows(sql, {"start": start, "end": end})
    return [Snapshot.model_validate(row) for row in rows]


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

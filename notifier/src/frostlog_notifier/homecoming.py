"""帰宅の要約: what happened while the car was away, and how the night looks.

The car coming home is recognised in the data rather than in a clock: raw
chunks only reach the bucket on the home Wi-Fi, so an upload run that starts
two hours or more after the previous one is a return. The summary then covers
everything since the previous summary.
"""

import logging
from datetime import datetime, timedelta

from frostlog_notifier import charts, formatting, queries
from frostlog_notifier.clock import next_morning, to_jst
from frostlog_notifier.notification import HOMECOMING, Notification
from frostlog_notifier.queries import HourlySnapshot, StateUpdate
from frostlog_notifier.warehouse import Warehouse

log = logging.getLogger(__name__)

#: A pause in the arrivals at least this long means the car was away.
ARRIVAL_GAP = timedelta(hours=2)
#: How far back the summary reaches when nothing has been summarised yet.
DEFAULT_PERIOD = timedelta(hours=24)
#: Hours the outlook's slope is taken from.
SLOPE_HOURS = 3
CHART_HOURS = 24


def arrival_gap(warehouse: Warehouse, raw_events_table: str) -> timedelta | None:
    """The pause before the most recent upload run, or None with fewer than two runs."""
    times = queries.upload_started_times(warehouse, raw_events_table, limit=2)
    if len(times) < 2:
        return None
    return times[0] - times[1]


def slope_percent_per_hour(hours: list[HourlySnapshot]) -> float | None:
    """State-of-charge change per hour over the last unplugged hours, or None."""
    unplugged = [
        hour
        for hour in hours
        if hour.covered_seconds > 0
        and hour.state_of_charge_delta_percent is not None
        and (hour.external_input_ratio or 0.0) == 0.0
    ]
    recent = unplugged[-SLOPE_HOURS:]
    covered = sum(hour.covered_seconds for hour in recent) / 3600
    if not recent or covered <= 0:
        return None
    return sum(hour.state_of_charge_delta_percent or 0 for hour in recent) / covered


def projected_state_of_charge(
    latest: StateUpdate, hours: list[HourlySnapshot], target: datetime
) -> float | None:
    """State of charge expected at ``target`` if the recent slope holds.

    None while the battery is charging or plugged in, and when no unplugged hour
    is available to take a slope from: an outlook would be made up.
    """
    if latest.battery_state == "charging" or latest.external_input:
        return None
    slope = slope_percent_per_hour(hours)
    if slope is None:
        return None
    span = (target - latest.updated_at).total_seconds() / 3600
    return min(100.0, max(0.0, latest.state_of_charge_percent + slope * span))


def _period_start(previous_key: str | None, end: datetime) -> datetime:
    if previous_key:
        try:
            return datetime.fromisoformat(previous_key)
        except ValueError:
            log.warning("previous key %r is not a timestamp; using the last day", previous_key)
    return end - DEFAULT_PERIOD


def build(
    warehouse: Warehouse,
    raw_events_table: str,
    previous_key: str | None,
) -> Notification | None:
    """The summary to post now, or None when the car has not just come back."""
    gap = arrival_gap(warehouse, raw_events_table)
    if gap is None or gap < ARRIVAL_GAP:
        return None
    latest = queries.latest_state_update(warehouse)
    if latest is None:
        log.info("no state update to summarise")
        return None

    end = latest.updated_at
    start = _period_start(previous_key, end)
    energy = queries.energy_between(warehouse, start, end)
    pulldowns = queries.finished_pulldowns_between(warehouse, start, end)
    hour_start = end.replace(minute=0, second=0, microsecond=0) - timedelta(hours=CHART_HOURS - 1)
    hours = queries.hourly_snapshots(warehouse, hour_start, end + timedelta(hours=1))

    morning = next_morning(end)
    projected = projected_state_of_charge(latest, hours, morning)
    text = _text(latest, start, end, energy, len(pulldowns), hours, morning, projected, gap)
    title = f"Last {CHART_HOURS} hours to {formatting.full_stamp(end)}"
    image = charts.daily_overview(hours, title)
    return Notification(
        kind=HOMECOMING,
        key=end.isoformat(),
        text=text,
        image=image,
        filename=f"homecoming-{to_jst(end):%Y%m%d-%H%M}.png",
    )


def _text(
    latest: StateUpdate,
    start: datetime,
    end: datetime,
    energy: queries.Energy,
    pulldown_count: int,
    hours: list[HourlySnapshot],
    morning: datetime,
    projected: float | None,
    gap: timedelta,
) -> str:
    net = energy.charged_watt_hours - energy.discharged_watt_hours
    slope = slope_percent_per_hour(hours)
    outlook = (
        f"{formatting.percent(projected)} (直近 {SLOPE_HOURS} 時間 {formatting.number(slope)} %/h)"
        if projected is not None
        else "見込みなし (充電中またはデータなし)"
    )
    return "\n".join(
        [
            f":house: 帰宅の要約 {formatting.full_stamp(end)}"
            f" (前回の到着から {formatting.hours(gap.total_seconds() / 3600)})",
            f"現在: SoC {formatting.percent(latest.state_of_charge_percent)}"
            f" / 庫内 {formatting.celsius(latest.interior_temperature_celsius, 0)}"
            f" / 設定 {formatting.celsius(latest.setpoint_celsius, 0)}"
            f" / 車内 {formatting.celsius(latest.ambient_temperature_celsius)}"
            f" / {formatting.battery_state(latest.battery_state)}"
            f" / 外部入力 {f'{latest.input_watts} W' if latest.external_input else 'なし'}",
            f"期間 {formatting.stamp(start)} → {formatting.stamp(end)}"
            f" ({formatting.hours((end - start).total_seconds() / 3600)}):"
            f" 消費 {formatting.watt_hours(energy.discharged_watt_hours)}"
            f" / 充電 {formatting.watt_hours(energy.charged_watt_hours)}"
            f" / 差引 {formatting.watt_hours(net)}",
            f"翌朝 {to_jst(morning):%m-%d %H:%M} の見込み: {outlook}",
            f"プルダウン完了: {pulldown_count} 件",
        ]
    )

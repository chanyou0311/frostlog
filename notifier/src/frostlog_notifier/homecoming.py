"""帰宅の要約: what happened while the car was away, and how the night looks.

The car coming home is recognised in the data rather than in a clock: chunks
only reach the bucket from the home Wi-Fi, so an upload run that begins two
hours or more after the previous one finished is a return. Those runs are
carried by the ``semantic_updated`` event (``upload_runs``), which reports a run
only once every cooler chunk it announced is loaded — so one return produces one
summary, whatever the chunks were split into. The summary then covers everything
since the previous summary's coverage end.
"""

import logging
from datetime import datetime, timedelta

from frostlog_notifier import charts, formatting, queries
from frostlog_notifier.clock import next_morning, to_jst
from frostlog_notifier.events import UploadRun
from frostlog_notifier.notification import HOMECOMING, Notification
from frostlog_notifier.queries import HourlySnapshot, StateUpdate
from frostlog_notifier.warehouse import Warehouse

log = logging.getLogger(__name__)

#: A pause between upload runs at least this long means the car was away.
ARRIVAL_GAP = timedelta(hours=2)
#: How far back the summary reaches when nothing has been summarised yet.
DEFAULT_PERIOD = timedelta(hours=24)
#: Hours the outlook's slope is taken from.
SLOPE_HOURS = 3
CHART_HOURS = 24


def is_return(run: UploadRun) -> bool:
    """True when this upload run is the car coming back rather than another run at home."""
    if run.previous_finished_at is None:
        return True
    return run.began_at - run.previous_finished_at >= ARRIVAL_GAP


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


def build_all(
    warehouse: Warehouse,
    runs: list[UploadRun],
    previous_coverage_end: datetime | None,
) -> list[Notification]:
    """One summary for each return the event carries, oldest first.

    Several returns in one event are chained: each starts where the one before
    it ended, which is also what the state table remembers between events.
    """
    summaries: list[Notification] = []
    start = previous_coverage_end
    for run in sorted((run for run in runs if is_return(run)), key=lambda run: run.began_at):
        summary = _build(warehouse, run, start)
        if summary is None:
            continue
        summaries.append(summary)
        start = summary.coverage_end
    return summaries


def _build(
    warehouse: Warehouse, run: UploadRun, previous_coverage_end: datetime | None
) -> Notification | None:
    latest = queries.latest_state_update_at(warehouse, run.finished_at)
    if latest is None:
        log.info("no state update up to %s; nothing to summarise", run.finished_at)
        return None

    end = latest.updated_at
    start = previous_coverage_end if previous_coverage_end is not None else end - DEFAULT_PERIOD
    # The cooler may have said nothing since the previous summary; then the period is empty.
    start = min(start, end)
    energy = queries.energy_between(warehouse, start, end)
    pulldowns = queries.finished_pulldowns_between(warehouse, start, end)
    hour_start = end.replace(minute=0, second=0, microsecond=0) - timedelta(hours=CHART_HOURS - 1)
    hours = queries.hourly_snapshots(warehouse, hour_start, end + timedelta(hours=1))

    morning = next_morning(end)
    projected = projected_state_of_charge(latest, hours, morning)
    gap = run.began_at - run.previous_finished_at if run.previous_finished_at else None
    text = _text(latest, start, end, energy, len(pulldowns), hours, morning, projected, gap)
    title = f"{formatting.full_stamp(end)} までの {CHART_HOURS} 時間"
    image = charts.daily_overview(hours, title)
    return Notification(
        kind=HOMECOMING,
        # The run, not the data: the key must not move when a later chunk of the same return lands.
        key=run.began_at.isoformat(),
        text=text,
        image=image,
        filename=f"homecoming-{to_jst(run.began_at):%Y%m%d-%H%M}.png",
        coverage_end=end,
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
    gap: timedelta | None,
) -> str:
    net = energy.charged_watt_hours - energy.discharged_watt_hours
    slope = slope_percent_per_hour(hours)
    outlook = (
        f"{formatting.percent(projected)} (直近 {SLOPE_HOURS} 時間 {formatting.number(slope)} %/h)"
        if projected is not None
        else "見込みなし (充電中またはデータなし)"
    )
    since = f" (前回の到着から {formatting.hours(gap.total_seconds() / 3600)})" if gap else ""
    return "\n".join(
        [
            f":house: 帰宅の要約 {formatting.full_stamp(end)}{since}",
            f"現在: SoC {formatting.percent(latest.state_of_charge_percent)}"
            f" / 庫内 {formatting.celsius(latest.interior_temperature_celsius, 0)}"
            f" / 設定 {formatting.celsius(latest.setpoint_celsius, 0)}"
            f" / 周辺 {formatting.celsius(latest.ambient_temperature_celsius)}"
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

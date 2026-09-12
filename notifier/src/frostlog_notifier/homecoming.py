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
import math
from datetime import datetime, timedelta

from frostlog_notifier import charts, formatting, queries
from frostlog_notifier.clock import next_morning, to_jst
from frostlog_notifier.events import UploadRun
from frostlog_notifier.notification import HOMECOMING, Notification
from frostlog_notifier.queries import Snapshot, StateUpdate
from frostlog_notifier.warehouse import Warehouse

log = logging.getLogger(__name__)

#: A pause between upload runs at least this long means the car was away.
ARRIVAL_GAP = timedelta(hours=2)
#: How far back the summary reaches when nothing has been summarised yet.
DEFAULT_PERIOD = timedelta(hours=24)
#: Hours the outlook's slope is taken from.
SLOPE_HOURS = 3
SLOT_SECONDS = 900
#: Slots behind the slope the outlook is read from: the last three hours of them.
SLOPE_SLOTS = SLOPE_HOURS * 3600 // SLOT_SECONDS
#: How far back the chart may reach for a stretch that was recorded. A trip records the
#: whole time it is away and sends it all on the way home, so the stretch can be days;
#: past a week it is no longer this homecoming.
CHART_DAYS = 7
#: Slots to a point, coarsest last. Each leaves a label a reader can place — a quarter
#: hour, a half, an hour, two, three, four, six, twelve, a day.
FOLD_FACTORS = (1, 2, 4, 8, 12, 16, 24, 48, 96)


def is_return(run: UploadRun) -> bool:
    """True when this upload run is the car coming back rather than another run at home."""
    if run.previous_finished_at is None:
        return True
    return run.began_at - run.previous_finished_at >= ARRIVAL_GAP


def slope_percent_per_hour(hours: list[Snapshot]) -> float | None:
    """State-of-charge change per hour over the last unplugged hours, or None."""
    unplugged = [
        hour
        for hour in hours
        if hour.covered_seconds > 0
        and hour.state_of_charge_delta_percent is not None
        and (hour.external_input_ratio or 0.0) == 0.0
    ]
    recent = unplugged[-SLOPE_SLOTS:]
    covered = sum(hour.covered_seconds for hour in recent) / 3600
    if not recent or covered <= 0:
        return None
    return sum(hour.state_of_charge_delta_percent or 0 for hour in recent) / covered


def projected_state_of_charge(
    latest: StateUpdate, hours: list[Snapshot], target: datetime
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
    slots = queries.snapshots(
        warehouse, end - timedelta(days=CHART_DAYS), end + timedelta(seconds=SLOT_SECONDS)
    )
    recorded = _recorded_run(slots)

    morning = next_morning(end)
    projected = projected_state_of_charge(latest, recorded, morning)
    gap = run.began_at - run.previous_finished_at if run.previous_finished_at else None
    return Notification(
        kind=HOMECOMING,
        # The run, not the data: the key must not move when a later chunk of the same return lands.
        key=run.began_at.isoformat(),
        text=_text(latest, start, end, energy, pulldowns, morning, projected, gap),
        blocks=_blocks(latest, energy, pulldowns, morning, projected, gap, recorded),
        coverage_end=end,
    )


def _recorded_run(slots: list[Snapshot]) -> list[Snapshot]:
    """The slots up to now the cooler was heard from, back to the last silence.

    Slack draws a series without holes or not at all, and the cooler is off — and so is
    the Pi it powers — for hours at a time. Charting the stretch that was recorded
    avoids inventing the readings a hole would need. The message says which slots it
    covers, and how long the silence before them was.
    """
    recorded: list[Snapshot] = []
    for slot in reversed(slots):
        if slot.covered_seconds <= 0:
            break
        recorded.append(slot)
    return list(reversed(recorded))


def _fold_factor(slot_count: int) -> int:
    """Slots to a point: the fewest that still leave a chart Slack will draw."""
    for factor in FOLD_FACTORS:
        if math.ceil(slot_count / factor) <= charts.MAX_POINTS:
            return factor
    # Longer than a day to a point. Whole hours, as many as it takes.
    return math.ceil(slot_count / charts.MAX_POINTS / 4) * 4


def _folded(slots: list[Snapshot]) -> tuple[list[str], list[list[Snapshot]]]:
    """The chart's categories and the slots behind each.

    Folding is addition, on the terms the contract sets: the seconds add and an average
    weights by them. The hard part — cutting a report's held interval at the boundaries
    — happened once, in the warehouse, and grouping what it produced cannot undo it.
    """
    factor = _fold_factor(len(slots))
    groups = [slots[index : index + factor] for index in range(0, len(slots), factor)]
    dated = to_jst(slots[0].slot_started_at).date() != to_jst(slots[-1].slot_started_at).date()
    return [_label(group[0].slot_started_at, factor, dated) for group in groups], groups


def _label(moment: datetime, factor: int, dated: bool) -> str:
    """A category a reader can place: the minutes only while a point is under an hour."""
    local = to_jst(moment)
    clock = f"{local:%-H時}" if factor % 4 == 0 else f"{local:%-H:%M}"
    return f"{local:%-d日} {clock}" if dated else clock


def _measure(groups: list[list[Snapshot]], name: str) -> list[float | None]:
    """Each point's average, weighted by the seconds its slots were recorded for."""
    values: list[float | None] = []
    for group in groups:
        present = [slot for slot in group if getattr(slot, name) is not None]
        weight = sum(slot.covered_seconds for slot in present)
        if weight <= 0:
            values.append(None)
            continue
        values.append(
            round(sum(getattr(slot, name) * slot.covered_seconds for slot in present) / weight, 1)
        )
    return values


def _blocks(
    latest: StateUpdate,
    energy: queries.Energy,
    pulldowns: list[queries.Pulldown],
    morning: datetime,
    projected: float | None,
    gap: timedelta | None,
    recorded: list[Snapshot],
) -> list[dict]:
    """The message as Block Kit: what it means, then what it looked like."""
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "🧊 ポータブル冷蔵庫のバッテリー",
                "emoji": True,
            },
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": _lead(latest, morning, projected)}},
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*残量*\n{formatting.percent(latest.state_of_charge_percent)}",
                },
                {
                    "type": "mrkdwn",
                    "text": "*庫内温度*\n"
                    f"{formatting.celsius(latest.interior_temperature_celsius, 0)}"
                    f" (設定 {formatting.celsius(latest.setpoint_celsius, 0)})",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*周辺温度*\n{formatting.celsius(latest.ambient_temperature_celsius)}",
                },
                {"type": "mrkdwn", "text": f"*電源*\n{_power(latest)}"},
            ],
        },
    ]
    if recorded:
        labels, groups = _folded(recorded)
        charge = charts.line(
            "バッテリー残量 (%)",
            labels,
            [charts.Series("残量", [group[-1].state_of_charge_end_percent for group in groups])],
        )
        temperature = charts.line(
            "温度 (°C)",
            labels,
            [
                charts.Series("庫内", _measure(groups, "interior_temperature_celsius")),
                charts.Series("周辺", _measure(groups, "ambient_temperature_celsius")),
            ],
        )
        blocks += [block for block in (charge, temperature) if block is not None]
    blocks.append(
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": _period(energy, pulldowns, recorded)},
        }
    )
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": _footnote(gap)}]})
    return blocks


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def _power(latest: StateUpdate) -> str:
    if latest.external_input:
        return f"つないでいる ({latest.input_watts} W)"
    return "つないでいない"


def _lead(latest: StateUpdate, morning: datetime, projected: float | None) -> str:
    """What the reader came for, in the first two lines."""
    now = f"*戻ってきた時点の残量は {formatting.percent(latest.state_of_charge_percent)} です。*"
    if projected is None:
        return f"{now}\nいま充電できる状態なので、翌朝の見込みは出していません。"
    return (
        f"{now}\nこのまま充電しなければ、翌朝 {to_jst(morning):%-H:%M} には"
        f" {formatting.percent(projected)} の見込みです。"
    )


def _period(
    energy: queries.Energy,
    pulldowns: list[queries.Pulldown],
    recorded: list[Snapshot],
) -> str:
    """The stretch the chart covers, and the two things it cannot draw."""
    if not recorded:
        return "この期間に記録はありませんでした。"
    covered = sum(slot.covered_seconds for slot in recorded)
    plugged = sum(slot.covered_seconds * (slot.external_input_ratio or 0.0) for slot in recorded)
    ended = to_jst(recorded[-1].slot_started_at) + timedelta(seconds=SLOT_SECONDS)
    lines = [
        f"*記録があったのは {formatting.stamp(recorded[0].slot_started_at)}"
        f" から {ended:%H:%M} まで*",
        f"消費 {formatting.watt_hours(energy.discharged_watt_hours)}"
        f" ・ 充電 {formatting.watt_hours(energy.charged_watt_hours)}"
        f" ・ このうち {formatting.percent(100 * plugged / covered if covered else None)}"
        " の時間は外部電源につないでいました。",
    ]
    reached = [episode for episode in pulldowns if episode.duration_seconds is not None]
    if reached:
        episode = reached[-1]
        lines.append(
            f"庫内は {formatting.celsius(episode.interior_temperature_start_celsius, 0)} から"
            f" {formatting.duration(episode.duration_seconds)}で設定温度に届きました。"
        )
    return "\n".join(lines)


def _footnote(gap: timedelta | None) -> str:
    device = "Anker Solix EverFrost 2"
    if gap is None:
        return device
    return f"直前の記録中断 {formatting.hours(gap.total_seconds() / 3600)} ・ {device}"


def _text(
    latest: StateUpdate,
    start: datetime,
    end: datetime,
    energy: queries.Energy,
    pulldowns: list[queries.Pulldown],
    morning: datetime,
    projected: float | None,
    gap: timedelta | None,
) -> str:
    """The same thing in one paragraph, for whoever does not get the blocks.

    A notification on a locked phone, a screen reader, a search result: Slack
    shows this and nothing else, so it has to stand on its own.
    """
    outlook = (
        f"翌朝 {to_jst(morning):%-H:%M} には {formatting.percent(projected)} の見込み"
        if projected is not None
        else "いま充電できる状態なので翌朝の見込みはなし"
    )
    since = f" (直前の記録中断 {formatting.hours(gap.total_seconds() / 3600)})" if gap else ""
    return (
        f"ポータブル冷蔵庫のバッテリー {formatting.full_stamp(end)}{since}"
        f" — 戻ってきた時点の残量 {formatting.percent(latest.state_of_charge_percent)}、{outlook}。"
        f" 庫内 {formatting.celsius(latest.interior_temperature_celsius, 0)}"
        f" (設定 {formatting.celsius(latest.setpoint_celsius, 0)})、"
        f"周辺 {formatting.celsius(latest.ambient_temperature_celsius)}。"
        f" {formatting.stamp(start)} から"
        f" 消費 {formatting.watt_hours(energy.discharged_watt_hours)}"
        f" / 充電 {formatting.watt_hours(energy.charged_watt_hours)}、"
        f"設定温度まで冷却 {len(pulldowns)} 回。"
    )

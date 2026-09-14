"""毎晩の要約: 残量と、その先の見通し。

A picture of the last two days, not a diff against the last one. A diff has to
remember where it stopped; a picture of a window fixed to `now` does not, which is
why nothing here is written down anywhere. Data that reached the warehouse late --
a trip's backlog, uploaded when the car came home -- is simply in the window the
night it lands.

Two days rather than one because yesterday is what today is compared against.
"""

import logging
from datetime import datetime, timedelta
from itertools import groupby

from frostlog_notifier import charts, folding, formatting, queries
from frostlog_notifier.clock import next_morning, to_jst
from frostlog_notifier.folding import SLOT_SECONDS
from frostlog_notifier.notification import DAILY, Notification
from frostlog_notifier.queries import Snapshot, StateUpdate
from frostlog_notifier.warehouse import Warehouse

log = logging.getLogger(__name__)

#: The window: today, and yesterday to compare it with.
WINDOW = timedelta(days=2)
#: The day whose energy the summary reports, and whose pull-downs it lists.
TODAY = timedelta(days=1)
#: Hours the outlook's slope is taken from.
SLOPE_HOURS = 3
#: Slots behind the slope the outlook is read from: the last three hours of them.
SLOPE_SLOTS = SLOPE_HOURS * 3600 // SLOT_SECONDS
#: Slots to a point, coarsest last. Each leaves a label a reader can place — a quarter
#: hour, a half, an hour, two, three, four, six, twelve, a day. The last one is what
#: makes the choice total: a day to a point puts CHART_DAYS days inside MAX_POINTS
#: however the boundaries fall, so there is no length this table does not cover.
FOLD_FACTORS = (1, 2, 4, 8, 12, 16, 24, 48, 96)


def _on_battery(slot: Snapshot) -> bool:
    """The cooler was heard from through this slot and ran on its own battery."""
    return (
        slot.covered_seconds > 0
        and slot.state_of_charge_start_percent is not None
        and slot.state_of_charge_end_percent is not None
        and (slot.external_input_ratio or 0.0) == 0.0
    )


def slope_percent_per_hour(slots: list[Snapshot]) -> float | None:
    """State-of-charge change per hour over the last hours on the battery, or None.

    Read at the ends of each stretch, the way the contract says state of charge is
    read, and never by adding the slots' own deltas: a delta is the change inside a
    slot, so the change between two slots belongs to neither and adding them up
    quietly drops it. Stretches are separated by the cooler being plugged in, and
    what happened while it was plugged in is not this slope's business, so each
    stretch is measured on its own and only the drops are added.
    """
    stretches = [list(run) for battery, run in groupby(slots, key=_on_battery) if battery]
    recent: list[list[Snapshot]] = []
    budget = SLOPE_SLOTS
    for stretch in reversed(stretches):
        if budget <= 0:
            break
        tail = stretch[-budget:]
        recent.append(tail)
        budget -= len(tail)
    covered = sum(slot.covered_seconds for stretch in recent for slot in stretch) / 3600
    if not recent or covered <= 0:
        return None
    change = sum(
        (stretch[-1].state_of_charge_end_percent or 0)
        - (stretch[0].state_of_charge_start_percent or 0)
        for stretch in recent
    )
    return change / covered


#: Why there is no outlook. They read differently to someone deciding whether to
#: plug the cooler in, so the message says which it was rather than one phrase for all.
PLUGGED_IN = "plugged_in"
NO_SLOPE = "no_slope"


def projected_state_of_charge(
    latest: StateUpdate, slots: list[Snapshot], target: datetime
) -> tuple[float | None, str | None]:
    """State of charge expected at ``target`` if the recent slope holds, and why not.

    An outlook while the cooler is on external power would be a guess about when it
    comes off; one without a stretch of unplugged running behind it would be a guess
    about how fast it drains. Both are refused, and the caller is told which.
    """
    if latest.battery_state == "charging" or latest.external_input:
        return None, PLUGGED_IN
    slope = slope_percent_per_hour(slots)
    if slope is None:
        return None, NO_SLOPE
    span = (target - latest.updated_at).total_seconds() / 3600
    return min(100.0, max(0.0, latest.state_of_charge_percent + slope * span)), None


def hours_remaining(latest: StateUpdate, slots: list[Snapshot]) -> float | None:
    """Hours until the battery is empty at the recent slope, or None if it is not.

    The same slope the outlook uses, solved for zero instead of for a moment. It is
    refused for the same two reasons: on external power there is nothing to run down,
    and without a stretch of unplugged running there is no rate to run it down at. A
    slope that is flat or rising also answers None -- "forever" is not an hour count.
    """
    if latest.battery_state == "charging" or latest.external_input:
        return None
    slope = slope_percent_per_hour(slots)
    if slope is None or slope >= 0:
        return None
    return latest.state_of_charge_percent / -slope


def build(warehouse: Warehouse, now: datetime) -> Notification | None:
    """Tonight's summary, or nothing when the cooler has never been heard from.

    Everything is read from the window ending at ``now``; nothing is read from what
    was said before. Five queries, once a day.
    """
    latest = queries.latest_state_update(warehouse)
    if latest is None:
        log.info("no state update at all; nothing to summarise")
        return None

    start, midnight = now - WINDOW, now - TODAY
    slots = queries.snapshots(warehouse, start, now)
    recorded = _latest_stretch(slots)
    today = queries.energy_between(warehouse, midnight, now)
    yesterday = queries.energy_between(warehouse, start, midnight)
    pulldowns = queries.finished_pulldowns_between(warehouse, midnight, now)

    morning = next_morning(now)
    projected, refused = projected_state_of_charge(latest, slots, morning)
    remaining = hours_remaining(latest, slots)
    return Notification(
        kind=DAILY,
        text=_text(
            latest, now, today, yesterday, pulldowns, morning, projected, refused, remaining
        ),
        blocks=_blocks(
            latest,
            now,
            today,
            yesterday,
            pulldowns,
            morning,
            projected,
            refused,
            remaining,
            recorded,
        ),
    )


def _latest_stretch(slots: list[Snapshot]) -> list[Snapshot]:
    """The slots up to now the cooler was heard from, back to the last silence.

    Two things want it, for reasons of their own. A chart because Slack draws a
    series without holes or not at all, and the cooler — with the Pi it powers — is
    off for hours at a time, so a hole would have to be invented. The outlook because
    a drop measured across a silence is not a drop the battery made: what the cooler
    did while nothing was recorded is not in these numbers either way.

    Changing it for one of them changes it for the other, which is why it is named
    after what it is rather than after what either wanted it for.
    """
    recorded: list[Snapshot] = []
    for slot in reversed(slots):
        if slot.covered_seconds <= 0:
            break
        recorded.append(slot)
    return list(reversed(recorded))


def _fold_factor(slots: list[Snapshot]) -> int:
    """Slots to a point: the fewest that still leave a chart Slack will draw.

    Counted on the groups the boundaries actually make rather than on the slots
    divided by the factor. A stretch that begins at 13:45 puts those three quarters
    in an hour of their own, which is one point more than dividing would predict —
    and one point over the limit is a chart Slack declines to draw at all.
    """
    for factor in FOLD_FACTORS:
        starts = {folding.group_start(slot.slot_started_at, factor) for slot in slots}
        if len(starts) <= charts.MAX_POINTS:
            return factor
    return FOLD_FACTORS[-1]


def _folded(slots: list[Snapshot]) -> tuple[list[str], list[Snapshot]]:
    """The chart's categories and the snapshot behind each point.

    Coarsening is the contract's business, not this module's; :mod:`frostlog_notifier.folding`
    holds the four rules. All that is decided here is how coarse to go, which is the
    one thing the contract cannot know: it depends on what Slack will draw.
    """
    factor = _fold_factor(slots)
    points = folding.fold(slots, factor)
    dated = to_jst(slots[0].slot_started_at).date() != to_jst(slots[-1].slot_started_at).date()
    return [_label(point.slot_started_at, factor, dated) for point in points], points


def _label(moment: datetime, factor: int, dated: bool) -> str:
    """A category a reader can place: the minutes only while a point is under an hour."""
    local = to_jst(moment)
    clock = f"{local:%-H時}" if factor % 4 == 0 else f"{local:%-H:%M}"
    return f"{local:%-d日} {clock}" if dated else clock


def _blocks(
    latest: StateUpdate,
    now: datetime,
    today: queries.Energy,
    yesterday: queries.Energy,
    pulldowns: list[queries.Pulldown],
    morning: datetime,
    projected: float | None,
    refused: str | None,
    remaining: float | None,
    recorded: list[Snapshot],
) -> list[dict]:
    """The message as Block Kit: what it means, then what it looked like."""
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "🧊 ポータブル冷蔵庫の 24 時間",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _lead(latest, now, morning, projected, refused, remaining),
            },
        },
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
        labels, points = _folded(recorded)
        charge = charts.line(
            "バッテリー残量 (%)",
            labels,
            [charts.Series("残量", [point.state_of_charge_end_percent for point in points])],
        )
        temperature = charts.line(
            "温度 (°C)",
            labels,
            [
                charts.Series(
                    "庫内", [_rounded(point.interior_temperature_celsius) for point in points]
                ),
                charts.Series(
                    "周辺", [_rounded(point.ambient_temperature_celsius) for point in points]
                ),
            ],
        )
        blocks += charts.at_most(charge, temperature)
    blocks.append(
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": _period(today, yesterday, pulldowns, recorded)},
        }
    )
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": _footnote()}]})
    return blocks


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def _power(latest: StateUpdate) -> str:
    if latest.external_input:
        return f"つないでいる ({latest.input_watts} W)"
    return "つないでいない"


def _lead(
    latest: StateUpdate,
    now: datetime,
    morning: datetime,
    projected: float | None,
    refused: str | None,
    remaining: float | None,
) -> str:
    """What the reader came for, in the first lines.

    The outlook is stated as whichever comes first. A battery that runs out before
    morning has a time, and saying "翌朝 0 %" instead would be true and useless: the
    projection saturates at zero, so it reads as "it lasts the night and is then
    empty" when the cooler will in fact stop hours earlier.

    The reading is dated. It is the last one that arrived, which after a quiet day
    may be hours old, and a number stated without its time would be read as now.
    """
    lines = [
        f"*残量 {formatting.percent(latest.state_of_charge_percent)}"
        f" ({formatting.stamp(latest.updated_at)} 時点)*"
    ]
    empty = now + timedelta(hours=remaining) if remaining is not None else None
    if refused == PLUGGED_IN:
        lines.append("いま外部電源につながっているので、この先の見込みは出していません。")
    elif refused == NO_SLOPE:
        lines.append(
            "電源につないでいない時間の記録が足りないので、この先の見込みは出していません。"
        )
    elif empty is not None and empty < morning:
        lines.append(
            f"このまま充電しなければ、{to_jst(empty):%-H:%M} ごろに空になる見込みです"
            f" (あと約 {formatting.hours(remaining)})。"
        )
    else:
        lines.append(
            f"このまま充電しなければ、翌朝 {to_jst(morning):%-H:%M} には"
            f" {formatting.percent(projected)} の見込みです。"
        )
        if remaining is not None:
            lines.append(f"このペースなら、空になるまで約 {formatting.hours(remaining)}。")
    return "\n".join(lines)


def _period(
    today: queries.Energy,
    yesterday: queries.Energy,
    pulldowns: list[queries.Pulldown],
    recorded: list[Snapshot],
) -> str:
    """The day's energy against the one before it, and the two things the chart cannot draw."""
    lines = [
        f"*この 24 時間* 消費 {formatting.watt_hours(today.discharged_watt_hours)}"
        f" ・ 充電 {formatting.watt_hours(today.charged_watt_hours)}"
        f" ({_against_yesterday(today, yesterday)})"
    ]
    if recorded:
        covered = sum(slot.covered_seconds for slot in recorded)
        plugged = sum(
            slot.covered_seconds * (slot.external_input_ratio or 0.0) for slot in recorded
        )
        ended = to_jst(recorded[-1].slot_started_at) + timedelta(seconds=SLOT_SECONDS)
        lines.append(
            f"記録があったのは {formatting.stamp(recorded[0].slot_started_at)}"
            f" から {ended:%H:%M} まで ・ うち"
            f" {formatting.percent(100 * plugged / covered if covered else None)}"
            " の時間は外部電源につないでいました。"
        )
    else:
        lines.append("この 1 日の記録はありませんでした。")
    reached = [episode for episode in pulldowns if episode.duration_seconds is not None]
    if reached:
        episode = reached[-1]
        lines.append(
            f"庫内は {formatting.celsius(episode.interior_temperature_start_celsius, 0)} から"
            f" {formatting.duration(episode.duration_seconds)}で設定温度に届きました"
            f" (24 時間で {len(reached)} 回)。"
        )
    return "\n".join(lines)


def _against_yesterday(today: queries.Energy, yesterday: queries.Energy) -> str:
    """How the last day's draw compares with the day before it, or why it does not.

    The day before can be empty for two different reasons -- the cooler was off, or
    it ran on external power throughout -- and neither makes "0 Wh" a number worth
    comparing against. Both answer the same way: there is nothing to compare with.
    """
    before, after = yesterday.discharged_watt_hours, today.discharged_watt_hours
    if not before or after is None:
        return "その前の 24 時間と比べる記録なし"
    difference = after - before
    if abs(difference) < 1:
        return "その前の 24 時間とほぼ同じ"
    direction = "多い" if difference > 0 else "少ない"
    return f"その前の 24 時間より {formatting.watt_hours(abs(difference))} {direction}"


def _footnote() -> str:
    return "Anker Solix EverFrost 2"


def _text(
    latest: StateUpdate,
    now: datetime,
    today: queries.Energy,
    yesterday: queries.Energy,
    pulldowns: list[queries.Pulldown],
    morning: datetime,
    projected: float | None,
    refused: str | None,
    remaining: float | None,
) -> str:
    """The same thing in one paragraph, for whoever does not get the blocks.

    A notification on a locked phone, a screen reader, a search result: Slack
    shows this and nothing else, so it has to stand on its own.
    """
    empty = now + timedelta(hours=remaining) if remaining is not None else None
    left = ""
    if refused == PLUGGED_IN:
        outlook = "外部電源につながっているので先の見込みはなし"
    elif refused == NO_SLOPE:
        outlook = "記録が足りないので先の見込みはなし"
    elif empty is not None and empty < morning:
        outlook = (
            f"{to_jst(empty):%-H:%M} ごろに空になる見込み (あと約 {formatting.hours(remaining)})"
        )
    else:
        outlook = f"翌朝 {to_jst(morning):%-H:%M} には {formatting.percent(projected)} の見込み"
        left = f" 空になるまで約 {formatting.hours(remaining)}。" if remaining is not None else ""
    return (
        f"ポータブル冷蔵庫の 24 時間 {formatting.full_stamp(now)}"
        f" — 残量 {formatting.percent(latest.state_of_charge_percent)}"
        f" ({formatting.stamp(latest.updated_at)} 時点)、{outlook}。{left}"
        f" 庫内 {formatting.celsius(latest.interior_temperature_celsius, 0)}"
        f" (設定 {formatting.celsius(latest.setpoint_celsius, 0)})、"
        f"周辺 {formatting.celsius(latest.ambient_temperature_celsius)}。"
        f" この 24 時間の消費 {formatting.watt_hours(today.discharged_watt_hours)}"
        f" / 充電 {formatting.watt_hours(today.charged_watt_hours)}"
        f" ({_against_yesterday(today, yesterday)})、"
        f"設定温度まで冷却 {len(pulldowns)} 回。"
    )

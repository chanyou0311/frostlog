"""週の要約: what the week cost, and what it would take to break even.

Everything here is derived from the hourly snapshot rows of one ISO week, which
are dense by contract, so a missing hour is data (covered_seconds = 0) rather
than a gap in the arithmetic.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from frostlog_notifier import charts, folding, formatting, queries
from frostlog_notifier.clock import jst_dates, to_jst
from frostlog_notifier.notification import WEEKLY, Notification
from frostlog_notifier.queries import Pulldown, Snapshot
from frostlog_notifier.warehouse import Warehouse

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TriggerCount:
    trigger: str
    count: int
    mean_duration_seconds: float | None


@dataclass(frozen=True)
class DailyEnergy:
    day: date
    discharged_watt_hours: float
    charged_watt_hours: float
    #: The day's lowest and highest reading, or None on a day nothing was recorded.
    lowest_percent: int | None = None
    highest_percent: int | None = None


@dataclass(frozen=True)
class Summary:
    discharged_watt_hours: float
    charged_watt_hours: float
    external_input_hours: float
    average_charge_watts: float | None
    balancing_hours: float | None
    triggers: list[TriggerCount]
    gap_hours: int
    missing_days: list[date]
    daily: list[DailyEnergy]

    @property
    def net_watt_hours(self) -> float:
        return self.charged_watt_hours - self.discharged_watt_hours


def summarize(
    hours: list[Snapshot],
    pulldowns: list[Pulldown],
    days: list[date],
) -> Summary:
    discharged = sum(hour.discharged_watt_hours or 0.0 for hour in hours)
    charged = sum(hour.charged_watt_hours or 0.0 for hour in hours)
    external_seconds = sum(
        (hour.external_input_ratio or 0.0) * hour.covered_seconds for hour in hours
    )
    charged_while_plugged = sum(
        hour.charged_watt_hours or 0.0 for hour in hours if (hour.external_input_ratio or 0.0) > 0
    )
    external_hours = external_seconds / 3600
    average_charge_watts = charged_while_plugged / external_hours if external_hours > 0 else None
    deficit = discharged - charged
    balancing_hours = (
        deficit / average_charge_watts if average_charge_watts and deficit > 0 else None
    )

    covered_days = {
        to_jst(hour.slot_started_at).date() for hour in hours if hour.covered_seconds > 0
    }
    observed = [index for index, hour in enumerate(hours) if hour.covered_seconds > 0]
    gap_hours = (
        sum(1 for hour in hours[observed[0] : observed[-1]] if hour.covered_seconds == 0)
        if observed
        else 0
    )

    return Summary(
        discharged_watt_hours=discharged,
        charged_watt_hours=charged,
        external_input_hours=external_hours,
        average_charge_watts=average_charge_watts,
        balancing_hours=balancing_hours,
        triggers=_triggers(pulldowns),
        gap_hours=gap_hours,
        missing_days=[day for day in days if day not in covered_days],
        daily=_daily(hours, days),
    )


def _triggers(pulldowns: list[Pulldown]) -> list[TriggerCount]:
    grouped: dict[str, list[Pulldown]] = defaultdict(list)
    for episode in pulldowns:
        grouped[episode.trigger].append(episode)
    counts = []
    for trigger, episodes in sorted(grouped.items()):
        durations = [
            episode.duration_seconds for episode in episodes if episode.duration_seconds is not None
        ]
        counts.append(
            TriggerCount(
                trigger=trigger,
                count=len(episodes),
                mean_duration_seconds=sum(durations) / len(durations) if durations else None,
            )
        )
    return counts


def _daily(hours: list[Snapshot], days: list[date]) -> list[DailyEnergy]:
    discharged: dict[date, float] = defaultdict(float)
    charged: dict[date, float] = defaultdict(float)
    readings: dict[date, list[int]] = defaultdict(list)
    for hour in hours:
        day = to_jst(hour.slot_started_at).date()
        discharged[day] += hour.discharged_watt_hours or 0.0
        charged[day] += hour.charged_watt_hours or 0.0
        if hour.covered_seconds > 0:
            # Both ends: an hour that fell from 80 to 60 holds the day's high and its
            # low at once, and taking only one of them would lose half of every swing.
            readings[day] += [
                percent
                for percent in (
                    hour.state_of_charge_start_percent,
                    hour.state_of_charge_end_percent,
                )
                if percent is not None
            ]
    return [
        DailyEnergy(
            day,
            discharged[day],
            charged[day],
            lowest_percent=min(readings[day]) if readings[day] else None,
            highest_percent=max(readings[day]) if readings[day] else None,
        )
        for day in days
    ]


def by_hour(slots: list[Snapshot]) -> list[Snapshot]:
    """The quarter hours folded back into hours, which is the grain this asks at.

    State of charge is a whole percent, so over a quarter hour it is mostly 0 or 1 —
    read as a rate that is 0 or 4 %/h, and a week of that says more about the
    resolution than about the cooler. An hour is long enough for the number to mean
    something. Folding follows the contract: the seconds and the watt-hours add, the
    averages weight by the seconds, and the change is the last reading less the first.
    """
    return folding.fold(slots, 4)


#: The window: the seven days behind the moment the job runs.
WINDOW = timedelta(days=7)


def build(warehouse: Warehouse, now: datetime) -> Notification:
    """The last seven days, counted back from ``now``.

    A rolling window and not an ISO week. The job runs Saturday morning, and the
    most recent ISO week closed the Sunday before it -- a summary of that week would
    be six days stale on arrival. Counting back from the moment it runs also means
    nothing has to be remembered about which week was last reported.
    """
    start, end = now - WINDOW, now
    hours = by_hour(queries.snapshots(warehouse, start, end))
    pulldowns = queries.finished_pulldowns_between(warehouse, start, end)
    days = jst_dates(start, end)
    summary = summarize(hours, pulldowns, days)
    title = f"{days[0]:%m-%d} 〜 {days[-1]:%m-%d}"
    return Notification(
        kind=WEEKLY,
        text=_text(summary, title, days),
        blocks=_blocks(summary, title, days),
    )


def _blocks(summary: Summary, title: str, days: list[date]) -> list[dict]:
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"📅 直近 7 日 ({title})", "emoji": True},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{days[0]:%m-%d} から {days[-1]:%m-%d} の 7 日間で"
                f" {formatting.watt_hours(summary.discharged_watt_hours)} 使いました。*\n"
                f"充電は {formatting.watt_hours(summary.charged_watt_hours)}、"
                f"差引 {formatting.watt_hours(summary.net_watt_hours)}。",
            },
        },
    ]
    energy = charts.bar(
        "日ごとの電力量 (Wh) ・ 1 点 1 日",
        [day.day.strftime("%m-%d") for day in summary.daily],
        [
            charts.Series("消費", [day.discharged_watt_hours for day in summary.daily]),
            charts.Series("充電", [day.charged_watt_hours for day in summary.daily]),
        ],
    )
    blocks += charts.at_most(energy, _charge_chart(summary))
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "外部電源につないでいた時間 "
                    f"{formatting.hours(summary.external_input_hours)}"
                    f" ・ 記録のなかった時間 {summary.gap_hours} 時間"
                    " ・ Anker Solix EverFrost 2",
                }
            ],
        }
    )
    return blocks


def _charge_chart(summary: Summary) -> dict | None:
    """How far the battery swung each day of the week.

    A day nothing was recorded in is left out rather than drawn flat or carried over
    from the day before: Slack cannot draw a hole, and a hole invented here would
    claim a reading nobody took. The labels are dates, so a day that is missing shows
    as a gap in them.
    """
    recorded = [
        day
        for day in summary.daily
        if day.lowest_percent is not None and day.highest_percent is not None
    ]
    if not recorded:
        return None
    return charts.line(
        "日ごとのバッテリー残量 (%) ・ 1 点 1 日",
        [day.day.strftime("%m-%d") for day in recorded],
        [
            charts.Series("最高", [day.highest_percent for day in recorded]),
            charts.Series("最低", [day.lowest_percent for day in recorded]),
        ],
    )


def _text(summary: Summary, title: str, days: list[date]) -> str:
    lines = [
        f":calendar: 直近 7 日の要約 ({days[0]:%m-%d} - {days[-1]:%m-%d})",
        f"消費 {formatting.watt_hours(summary.discharged_watt_hours)}"
        f" / 充電 {formatting.watt_hours(summary.charged_watt_hours)}"
        f" / 差引 {formatting.watt_hours(summary.net_watt_hours)}",
        f"外部入力 {formatting.hours(summary.external_input_hours)}"
        f" / 均衡に必要な走行 {formatting.hours(summary.balancing_hours)}"
        f" (外部入力中の平均充電 {formatting.number(summary.average_charge_watts, 0)} W)",
    ]
    if summary.triggers:
        episodes = " / ".join(
            f"{trigger.trigger} {trigger.count} 件"
            f" 平均 {formatting.duration(trigger.mean_duration_seconds)}"
            for trigger in summary.triggers
        )
        total = sum(trigger.count for trigger in summary.triggers)
        lines.append(f"プルダウン {total} 件: {episodes}")
    else:
        lines.append("プルダウン 0 件")
    lines.append(f"データ欠損: {summary.gap_hours} 時間")
    if summary.missing_days:
        missing = ", ".join(f"{day:%m-%d}" for day in summary.missing_days)
        lines.append(f"データのない日: {missing}")
    return "\n".join(lines)

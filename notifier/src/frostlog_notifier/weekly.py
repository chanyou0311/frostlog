"""週の要約: what the week cost, and what it would take to break even.

Everything here is derived from the hourly snapshot rows of one ISO week, which
are dense by contract, so a missing hour is data (covered_seconds = 0) rather
than a gap in the arithmetic.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

from frostlog_notifier import charts, formatting, queries
from frostlog_notifier.clock import (
    iso_week_bounds,
    iso_week_key,
    jst_dates,
    previous_iso_week,
    to_jst,
)
from frostlog_notifier.notification import WEEKLY, Notification
from frostlog_notifier.queries import Band, HourlySnapshot, Pulldown
from frostlog_notifier.warehouse import Warehouse

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BandDrop:
    label: str
    #: State of charge change per hour, negative while the battery drains.
    percent_per_hour: float
    hours: float
    samples: list[float]


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


@dataclass(frozen=True)
class Summary:
    discharged_watt_hours: float
    charged_watt_hours: float
    external_input_hours: float
    average_charge_watts: float | None
    balancing_hours: float | None
    bands: list[BandDrop]
    most_common_band: BandDrop | None
    hours_from_full: float | None
    triggers: list[TriggerCount]
    gap_hours: int
    missing_days: list[date]
    daily: list[DailyEnergy]

    @property
    def net_watt_hours(self) -> float:
        return self.charged_watt_hours - self.discharged_watt_hours


def _band_of(bands: list[Band], value: float) -> Band | None:
    """The band a value falls in; bounds are half-open, [lower, upper)."""
    for band in bands:
        low = band.lower_celsius
        high = band.upper_celsius
        if (low is None or value >= low) and (high is None or value < high):
            return band
    return None


def summarize(
    hours: list[HourlySnapshot],
    bands: list[Band],
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

    band_drops = _band_drops(hours, bands)
    most_common = max(band_drops, key=lambda drop: drop.hours, default=None)
    hours_from_full = (
        100 / -most_common.percent_per_hour
        if most_common and most_common.percent_per_hour < 0
        else None
    )

    covered_days = {
        to_jst(hour.hour_started_at).date() for hour in hours if hour.covered_seconds > 0
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
        bands=band_drops,
        most_common_band=most_common,
        hours_from_full=hours_from_full,
        triggers=_triggers(pulldowns),
        gap_hours=gap_hours,
        missing_days=[day for day in days if day not in covered_days],
        daily=_daily(hours, days),
    )


def _band_drops(hours: list[HourlySnapshot], bands: list[Band]) -> list[BandDrop]:
    """State-of-charge change per hour per ambient temperature band, unplugged hours only."""
    deltas: dict[int, list[float]] = defaultdict(list)
    covered: dict[int, float] = defaultdict(float)
    summed: dict[int, float] = defaultdict(float)
    known: dict[int, Band] = {}
    for hour in hours:
        if (
            hour.covered_seconds <= 0
            or (hour.external_input_ratio or 0.0) != 0.0
            or hour.state_of_charge_delta_percent is None
            or hour.ambient_temperature_celsius is None
        ):
            continue
        band = _band_of(bands, hour.ambient_temperature_celsius)
        if band is None:
            continue
        known[band.band_key] = band
        span = hour.covered_seconds / 3600
        covered[band.band_key] += span
        summed[band.band_key] += hour.state_of_charge_delta_percent
        deltas[band.band_key].append(hour.state_of_charge_delta_percent / span)
    return [
        BandDrop(
            label=band.label,
            percent_per_hour=summed[key] / covered[key],
            hours=covered[key],
            samples=deltas[key],
        )
        for key, band in sorted(known.items(), key=lambda item: item[1].sort_order)
        if covered[key] > 0
    ]


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


def _daily(hours: list[HourlySnapshot], days: list[date]) -> list[DailyEnergy]:
    discharged: dict[date, float] = defaultdict(float)
    charged: dict[date, float] = defaultdict(float)
    for hour in hours:
        day = to_jst(hour.hour_started_at).date()
        discharged[day] += hour.discharged_watt_hours or 0.0
        charged[day] += hour.charged_watt_hours or 0.0
    return [DailyEnergy(day, discharged[day], charged[day]) for day in days]


def due_week(moment: datetime) -> tuple[int, int]:
    """The ISO week to summarise now: the one before the week ``moment`` is in."""
    return previous_iso_week(moment)


def build(warehouse: Warehouse, iso_year: int, iso_week: int) -> Notification:
    start, end = iso_week_bounds(iso_year, iso_week)
    hours = queries.hourly_snapshots(warehouse, start, end)
    bands = queries.ambient_bands(warehouse)
    pulldowns = queries.finished_pulldowns_between(warehouse, start, end)
    days = jst_dates(start, end)
    summary = summarize(hours, bands, pulldowns, days)
    key = iso_week_key(iso_year, iso_week)
    image = charts.weekly(
        [
            (day.day.strftime("%m-%d"), day.discharged_watt_hours, day.charged_watt_hours)
            for day in summary.daily
        ],
        [(band.label, band.samples) for band in summary.bands if band.samples],
        f"Week {key} ({days[0]:%m-%d} to {days[-1]:%m-%d} JST)",
    )
    return Notification(
        kind=WEEKLY,
        key=key,
        text=_text(summary, key, days),
        image=image,
        filename=f"weekly-{key}.png",
    )


def _text(summary: Summary, key: str, days: list[date]) -> str:
    lines = [
        f":calendar: 週の要約 {key} ({days[0]:%m-%d} - {days[-1]:%m-%d})",
        f"消費 {formatting.watt_hours(summary.discharged_watt_hours)}"
        f" / 充電 {formatting.watt_hours(summary.charged_watt_hours)}"
        f" / 差引 {formatting.watt_hours(summary.net_watt_hours)}",
        f"外部入力 {formatting.hours(summary.external_input_hours)}"
        f" / 均衡に必要な走行 {formatting.hours(summary.balancing_hours)}"
        f" (外部入力中の平均充電 {formatting.number(summary.average_charge_watts, 0)} W)",
    ]
    if summary.bands:
        drops = " / ".join(
            f"{band.label} {formatting.number(band.percent_per_hour)} %/h"
            f" ({formatting.hours(band.hours)})"
            for band in summary.bands
        )
        lines.append(f"外気温帯別 SoC 変化 (外部入力なし): {drops}")
    if summary.most_common_band:
        lines.append(
            f"100 % からの持ち時間 (最頻帯 {summary.most_common_band.label}):"
            f" {formatting.hours(summary.hours_from_full)}"
        )
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

"""The PNG that goes with each notification.

Labels are ASCII on purpose: the container carries no Japanese font, and a
missing glyph would silently become a box. Dates in titles are JST.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from frostlog_notifier.clock import to_jst
from frostlog_notifier.queries import HourlySnapshot, StateUpdate

if TYPE_CHECKING:
    from matplotlib.axes import Axes
    from matplotlib.figure import Figure


def _pyplot() -> Any:
    """matplotlib, loaded on first use.

    Importing it costs a good part of a second, and most requests (health checks,
    events that post nothing) never draw; a cold start should not pay for them.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def _dates() -> Any:
    import matplotlib.dates as mdates

    return mdates


CHARGING = "#4c9be8"
SOC = "#2b7a3d"
INTERIOR = "#1f4e79"
SETPOINT = "#8a8a8a"
AMBIENT = "#d1701a"
DISCHARGED = "#c0504d"
NET = "#333333"


def _png(figure: Figure) -> bytes:
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=110, bbox_inches="tight")
    _pyplot().close(figure)
    return buffer.getvalue()


def _hour_axis(axes: Axes) -> None:
    axes.xaxis.set_major_formatter(_dates().DateFormatter("%m-%d %H"))
    for label in axes.get_xticklabels():
        label.set_rotation(0)
        label.set_fontsize(8)


def _local(moment: datetime) -> datetime:
    """Naive JST, so that matplotlib labels the axis in the reader's time."""
    return to_jst(moment).replace(tzinfo=None)


def daily_overview(hours: list[HourlySnapshot], title: str) -> bytes:
    """State of charge over the period, with the hours that had external input shaded,
    and the temperatures below it."""
    figure, (top, bottom) = _pyplot().subplots(2, 1, figsize=(9, 6), sharex=True)
    figure.suptitle(title)
    times = [_local(hour.hour_started_at) for hour in hours]

    charge = [hour.state_of_charge_end_percent for hour in hours]
    top.plot(times, charge, color=SOC, marker="o", markersize=2.5, label="state of charge (%)")
    top.set_ylabel("state of charge (%)")
    top.set_ylim(0, 105)
    for hour, start in zip(hours, times, strict=True):
        if (hour.external_input_ratio or 0.0) > 0:
            top.axvspan(
                start,
                start + (times[1] - times[0] if len(times) > 1 else timedelta(hours=1)),
                color=CHARGING,
                alpha=0.18,
                linewidth=0,
            )
    top.grid(alpha=0.3)
    top.legend(loc="lower left", fontsize=8)

    bottom.plot(
        times,
        [hour.interior_temperature_celsius for hour in hours],
        color=INTERIOR,
        label="interior (C)",
    )
    bottom.plot(
        times,
        [hour.setpoint_celsius for hour in hours],
        color=SETPOINT,
        linestyle="--",
        label="setpoint (C)",
    )
    bottom.plot(
        times,
        [hour.ambient_temperature_celsius for hour in hours],
        color=AMBIENT,
        label="cabin (C)",
    )
    bottom.set_ylabel("temperature (C)")
    bottom.grid(alpha=0.3)
    bottom.legend(loc="upper left", fontsize=8)
    _hour_axis(bottom)
    return _png(figure)


def pulldown(updates: list[StateUpdate], setpoint_celsius: int, title: str) -> bytes:
    """Interior temperature over one pull-down episode, with the cabin temperature."""
    figure, axes = _pyplot().subplots(figsize=(9, 4))
    figure.suptitle(title)
    minutes = _minutes_from_start(updates)
    axes.plot(
        minutes,
        [update.interior_temperature_celsius for update in updates],
        color=INTERIOR,
        marker="o",
        markersize=2.5,
        label="interior (C)",
    )
    axes.plot(
        minutes,
        [update.ambient_temperature_celsius for update in updates],
        color=AMBIENT,
        label="cabin (C)",
    )
    axes.axhline(setpoint_celsius, color=SETPOINT, linestyle="--", label="setpoint (C)")
    axes.set_xlabel("minutes from start")
    axes.set_ylabel("temperature (C)")
    axes.grid(alpha=0.3)
    axes.legend(loc="upper right", fontsize=8)
    return _png(figure)


def _minutes_from_start(updates: list[StateUpdate]) -> list[float]:
    if not updates:
        return []
    start = updates[0].updated_at
    return [(update.updated_at - start).total_seconds() / 60 for update in updates]


def weekly(
    days: list[tuple[str, float, float]],
    band_samples: list[tuple[str, list[float]]],
    title: str,
) -> bytes:
    """Energy per day (discharged against charged, with the net) and the spread of the
    hourly state-of-charge change per cabin temperature band."""
    figure, (left, right) = _pyplot().subplots(1, 2, figsize=(11, 4.5))
    figure.suptitle(title)

    labels = [label for label, _, _ in days]
    positions = range(len(days))
    width = 0.4
    left.bar(
        [position - width / 2 for position in positions],
        [discharged for _, discharged, _ in days],
        width=width,
        color=DISCHARGED,
        label="discharged (Wh)",
    )
    left.bar(
        [position + width / 2 for position in positions],
        [charged for _, _, charged in days],
        width=width,
        color=CHARGING,
        label="charged (Wh)",
    )
    left.plot(
        list(positions),
        [charged - discharged for _, discharged, charged in days],
        color=NET,
        marker="o",
        markersize=3,
        label="net (Wh)",
    )
    left.axhline(0, color=NET, linewidth=0.8)
    left.set_xticks(list(positions))
    left.set_xticklabels(labels, fontsize=8)
    left.set_ylabel("watt hours")
    left.grid(alpha=0.3, axis="y")
    left.legend(fontsize=8)

    if band_samples:
        right.boxplot(
            [samples for _, samples in band_samples],
            tick_labels=[label for label, _ in band_samples],
        )
        right.axhline(0, color=NET, linewidth=0.8)
    else:
        right.text(0.5, 0.5, "no unplugged hours", ha="center", va="center", fontsize=9)
        right.set_xticks([])
    right.set_ylabel("state of charge change (%/h)")
    right.set_xlabel("cabin temperature band (C)")
    right.tick_params(axis="x", labelsize=8)
    right.grid(alpha=0.3, axis="y")
    return _png(figure)

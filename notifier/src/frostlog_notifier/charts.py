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
#: An hour nothing was recorded in.
SILENT = "#9aa0a6"


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
    """State of charge over the period, with external input and silence shaded.

    Every hour of the period is on the axis whether it holds a reading or not.
    The cooler is often unplugged and out of range for hours at a time, and a
    chart that quietly narrowed to the hours it heard from would show a calm
    line over a night nothing was recorded in.
    """
    figure, (top, bottom) = _pyplot().subplots(2, 1, figsize=(9, 6), sharex=True)
    figure.suptitle(title)
    times = [_local(hour.hour_started_at) for hour in hours]
    step = times[1] - times[0] if len(times) > 1 else timedelta(hours=1)

    charge = [hour.state_of_charge_end_percent for hour in hours]
    top.plot(times, charge, color=SOC, marker="o", markersize=2.5, label="state of charge (%)")
    top.set_ylabel("state of charge (%)")
    top.set_ylim(0, 105)
    for axes in (top, bottom):
        _shade_hours(axes, hours, times, step)
        axes.set_xlim(times[0], times[-1] + step)
        axes.grid(alpha=0.3)
    _legend(top, extra=_shading_key())

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
        label="ambient (C)",
    )
    bottom.set_ylabel("temperature (C)")
    _legend(bottom)
    _hour_axis(bottom)
    return _png(figure)


def _shade_hours(
    axes: Axes, hours: list[HourlySnapshot], times: list[datetime], step: timedelta
) -> None:
    """Grey an hour nothing was recorded in; tint one by how much of it was plugged in.

    An hour is rarely all or nothing — the cooler goes on the car socket for the
    drive and comes off at the door — so the tint follows the share of the hour
    rather than marking the whole of it.
    """
    # axvspan wants numbers, and the axis is in matplotlib's own date units.
    to_number = _dates().date2num
    for hour, start in zip(hours, times, strict=True):
        left, right = to_number(start), to_number(start + step)
        if hour.covered_seconds <= 0:
            axes.axvspan(left, right, color=SILENT, alpha=0.35, linewidth=0)
            continue
        plugged = hour.external_input_ratio or 0.0
        if plugged > 0:
            axes.axvspan(left, right, color=CHARGING, alpha=0.30 * plugged, linewidth=0)


def _shading_key() -> list[Any]:
    """Legend entries for the two shadings, which no plotted line would explain."""
    from matplotlib.patches import Patch

    return [
        Patch(facecolor=CHARGING, alpha=0.30, label="plugged in (deeper = more of the hour)"),
        Patch(facecolor=SILENT, alpha=0.35, label="nothing recorded"),
    ]


def _legend(axes: Axes, extra: list[Any] | None = None) -> None:
    """Above the axes: inside it, the box lands on the data often enough to matter."""
    handles, labels = axes.get_legend_handles_labels()
    for patch in extra or []:
        handles.append(patch)
        labels.append(patch.get_label())
    axes.legend(
        handles,
        labels,
        loc="lower left",
        bbox_to_anchor=(0, 1.01),
        ncols=3,
        frameon=False,
        fontsize=8,
    )


def pulldown(updates: list[StateUpdate], setpoint_celsius: int, title: str) -> bytes:
    """Interior temperature over one pull-down episode, with the air around the cooler."""
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
        label="ambient (C)",
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
    hourly state-of-charge change per ambient temperature band."""
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
    right.set_xlabel("ambient temperature band (C)")
    right.tick_params(axis="x", labelsize=8)
    right.grid(alpha=0.3, axis="y")
    return _png(figure)

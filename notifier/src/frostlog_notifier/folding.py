"""Coarsening the snapshot's quarter hours, on the terms its contract sets.

``fact_cooler_snapshot`` is dense at fifteen minutes because that is the finest grain
worth keeping, not because it is the grain anything reads at. Its contract says how a
consumer makes a coarser one, and the rules are not interchangeable with the obvious
guesses:

* sums add — the seconds covered, the watt-hours in and out;
* averages weight by ``covered_seconds``, not by slot, because a slot the cooler was
  heard from for a minute must not count as much as one it was heard from throughout;
* state of charge is read at the ends, never added up, because a slot's delta is the
  change *within* it and the change between two slots belongs to neither;
* a group starts on a boundary of the grain it is making, so that an hour is an hour
  on the clock and can be labelled as one.

The last rule is the one a list-slicing fold quietly breaks: a recording that resumes
at 13:45 would put 13:45-14:45 in the first group and call it 13時. So the group a
slot belongs to is decided by when the slot started, not by where it sits in a list.

Every factor this is asked for divides a day, so the boundaries are the same instants
in JST regardless of how coarse the grain is. They are computed in JST because that is
where the reader's day starts.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from frostlog_notifier.clock import to_jst
from frostlog_notifier.queries import Snapshot

#: The grain fact_cooler_snapshot is dense at, as the contract fixes it.
SLOT_SECONDS = 900


def fold(slots: list[Snapshot], factor: int) -> list[Snapshot]:
    """``slots`` gathered into groups of ``factor``, each group one snapshot again.

    The result is ordered and, like its input, carries a row for every group that had
    slots — a group nothing was recorded in keeps its zero seconds and its nulls rather
    than vanishing, so a gap stays visible to whoever asked for the coarser grain.
    """
    grouped: dict[datetime, list[Snapshot]] = defaultdict(list)
    for slot in slots:
        grouped[group_start(slot.slot_started_at, factor)].append(slot)
    return [_folded(started, members) for started, members in sorted(grouped.items())]


def group_start(moment: datetime, factor: int) -> datetime:
    """The start of the group ``moment`` falls in, on a JST boundary of that grain."""
    local = to_jst(moment)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    span = factor * SLOT_SECONDS
    elapsed = int((local - midnight).total_seconds())
    return (midnight + timedelta(seconds=elapsed // span * span)).astimezone(UTC)


def weighted(slots: list[Snapshot], name: str) -> float | None:
    """One attribute's average over ``slots``, weighted by the seconds each covers."""
    present = [slot for slot in slots if getattr(slot, name) is not None]
    weight = sum(slot.covered_seconds for slot in present)
    if weight <= 0:
        return None
    return sum(getattr(slot, name) * slot.covered_seconds for slot in present) / weight


def _folded(started: datetime, slots: list[Snapshot]) -> Snapshot:
    covered = sum(slot.covered_seconds for slot in slots)
    # Only the slots something was recorded in can say where the charge stood; a slot
    # with no seconds has no reading to read, at either end.
    observed = [slot for slot in slots if slot.covered_seconds > 0]
    start = next((slot.state_of_charge_start_percent for slot in observed), None)
    end = next((slot.state_of_charge_end_percent for slot in reversed(observed)), None)
    return Snapshot(
        slot_started_at=started,
        covered_seconds=covered,
        state_of_charge_start_percent=start,
        state_of_charge_end_percent=end,
        state_of_charge_delta_percent=None if start is None or end is None else end - start,
        discharged_watt_hours=sum(slot.discharged_watt_hours or 0.0 for slot in slots),
        charged_watt_hours=sum(slot.charged_watt_hours or 0.0 for slot in slots),
        interior_temperature_celsius=weighted(slots, "interior_temperature_celsius"),
        setpoint_celsius=weighted(slots, "setpoint_celsius"),
        ambient_temperature_celsius=weighted(slots, "ambient_temperature_celsius"),
        external_input_ratio=weighted(slots, "external_input_ratio"),
        charging_ratio=weighted(slots, "charging_ratio"),
    )

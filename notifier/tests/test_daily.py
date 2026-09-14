from datetime import UTC, datetime, timedelta

import pytest
from conftest import FakeWarehouse, at, pulldown_row, quarters, slot, state_update

from frostlog_notifier import charts, daily
from frostlog_notifier.notification import DAILY
from frostlog_notifier.queries import Snapshot, StateUpdate

#: Where the recorded stretch ends in most of these.
RETURN = at("2026-09-11", 12, 3)  # 21:03 JST
#: When the evening job fires.
NOW = at("2026-09-11", 11, 0)  # 20:00 JST


def day_of_slots(end: datetime, first_charge: int = 86, plugged: range = range(0)) -> list[dict]:
    """24 dense hours ending at ``end``, as the 96 slots they are made of."""
    rows: list[dict] = []
    charge = first_charge
    for index in range(24):
        started = end.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23 - index)
        if index in plugged:
            charge = min(100, charge + 6)
            rows += quarters(
                started,
                charge,
                delta=6,
                external_input_ratio=1.0,
                charged_watt_hours=54.0,
                discharged_watt_hours=0.0,
            )
        else:
            charge -= 1
            rows += quarters(started, charge)
    return rows


def warehouse_with(**overrides) -> FakeWarehouse:
    answers = {
        "latest_state_update": [state_update(RETURN, state_of_charge_percent=62)],
        "energy_between": [
            {"discharged_watt_hours": 128.4, "charged_watt_hours": 40.2},
        ],
        "finished_pulldowns_between": [pulldown_row(at("2026-09-11", 0, 30))],
        "snapshots": day_of_slots(RETURN),
    }
    return FakeWarehouse(answers | overrides)


def test_the_outlook_follows_the_slope_of_the_last_unplugged_hours() -> None:
    hours = [Snapshot.model_validate(row) for row in day_of_slots(RETURN)]
    latest = StateUpdate.model_validate(state_update(RETURN, state_of_charge_percent=62))
    # One percent an hour down, and 9.95 hours from 21:03 JST to 07:00 the next morning.
    morning = datetime(2026, 9, 11, 22, 0, tzinfo=UTC)
    assert daily.slope_percent_per_hour(hours) == -1.0
    assert daily.projected_state_of_charge(latest, hours, morning) == (
        pytest.approx(52.05),
        None,
    )


def test_a_partly_covered_slot_weighs_less_in_the_slope() -> None:
    end = RETURN.replace(minute=0)
    slots = [
        Snapshot.model_validate(slot(end - timedelta(minutes=30), 64, delta=-1)),
        Snapshot.model_validate(
            slot(end - timedelta(minutes=15), 63, delta=-1, covered_seconds=450.0)
        ),
    ]
    # Two percent over 900 + 450 seconds, which is three eighths of an hour.
    assert daily.slope_percent_per_hour(slots) == -2 / 0.375


def test_the_slope_is_read_at_the_ends_not_added_up_from_the_slots() -> None:
    """Four quarter hours the cooler was heard from for half of each.

    Inside each of them the charge fell by one, and between them — while nothing was
    being recorded — it fell by one more. Adding the slots' own deltas sees four of
    those falls and misses three, which makes the battery look like it lasts twice as
    long as it does. The contract says the charge is read at the ends, so it is.
    """
    end = RETURN.replace(minute=0)
    slots = [
        Snapshot.model_validate(
            slot(end - timedelta(minutes=15 * (4 - index)), charge, delta=-1, covered_seconds=450.0)
        )
        for index, charge in enumerate([99, 97, 95, 93])
    ]
    # 1800 seconds recorded in all, so half an hour; 100 % at the start, 93 % at the end.
    assert daily.slope_percent_per_hour(slots) == pytest.approx(-14.0)


def test_charging_in_the_middle_does_not_count_towards_the_slope() -> None:
    """The stretches on battery are measured apart, so a charge between them is not a rise."""
    end = RETURN.replace(minute=0)
    before = [Snapshot.model_validate(slot(end - timedelta(minutes=60), 80, delta=-2))]
    charging = [
        Snapshot.model_validate(
            slot(end - timedelta(minutes=45), 90, delta=10, external_input_ratio=1.0)
        )
    ]
    after = [Snapshot.model_validate(slot(end - timedelta(minutes=30), 88, delta=-2))]
    # Four percent lost over the two quarter hours on battery, and the charge ignored.
    assert daily.slope_percent_per_hour(before + charging + after) == pytest.approx(-8.0)


def test_there_is_no_outlook_while_charging() -> None:
    hours = [Snapshot.model_validate(row) for row in day_of_slots(RETURN)]
    charging = StateUpdate.model_validate(
        state_update(RETURN, battery_state="charging", external_input=True, input_watts=58)
    )
    morning = datetime(2026, 9, 11, 22, 0, tzinfo=UTC)
    assert daily.projected_state_of_charge(charging, hours, morning) == (
        None,
        daily.PLUGGED_IN,
    )

    notification = daily.build(
        warehouse_with(latest_state_update=[dict(charging.model_dump())]), NOW
    )
    assert notification is not None
    assert "外部電源につながっているので先の見込みはなし" in notification.text


def test_there_is_no_outlook_without_an_unplugged_hour() -> None:
    hours = [Snapshot.model_validate(row) for row in day_of_slots(RETURN, plugged=range(24))]
    latest = StateUpdate.model_validate(state_update(RETURN))
    assert daily.slope_percent_per_hour(hours) is None
    assert daily.projected_state_of_charge(latest, hours, RETURN + timedelta(hours=10)) == (
        None,
        daily.NO_SLOPE,
    )


def test_the_outlook_never_leaves_the_scale() -> None:
    hours = [
        Snapshot.model_validate(slot(RETURN - timedelta(hours=1), 2, delta=-30)),
    ]
    latest = StateUpdate.model_validate(state_update(RETURN, state_of_charge_percent=2))
    assert daily.projected_state_of_charge(latest, hours, RETURN + timedelta(hours=10)) == (
        0.0,
        None,
    )


def _stretch(hours_recorded: float, starting_at: datetime = RETURN) -> list[Snapshot]:
    """A dense run of quarter hours, the last of them ending at ``starting_at``."""
    count = round(hours_recorded * 3600 / daily.SLOT_SECONDS)
    return [
        Snapshot.model_validate(slot(starting_at + timedelta(minutes=15 * index), 80 - index))
        for index in range(count)
    ]


@pytest.mark.parametrize("hours_recorded", [0.25, 0.5, 1, 2, 3, 5, 9, 12, 20, 24, 36, 48, 72, 168])
@pytest.mark.parametrize("offset_minutes", [0, 15, 45])
def test_the_fold_always_leaves_a_chart_slack_will_draw(
    hours_recorded: float, offset_minutes: int
) -> None:
    """However long the stretch and wherever it starts, the points fit.

    The offsets matter because the groups sit on clock boundaries: a stretch that
    begins at 13:45 opens an hour with three quarters in it, which is a point the
    slots divided by the factor does not predict.
    """
    slots = _stretch(hours_recorded, RETURN + timedelta(minutes=offset_minutes))
    labels, points = daily._folded(slots)
    assert len(points) == len(labels)
    assert len(points) <= charts.MAX_POINTS, f"{hours_recorded} h is {len(points)} points"


def test_a_shorter_stretch_is_charted_more_finely() -> None:
    """Two hours out used to be two points; the fold follows the length now."""
    assert daily._fold_factor(_stretch(2)) == 1, "a quarter hour to a point while it fits"
    assert daily._fold_factor(_stretch(2)) < daily._fold_factor(_stretch(9))


def test_a_group_starts_on_the_clock_not_where_the_recording_did() -> None:
    """A stretch from 13:45 puts that quarter in the 13時 point, not at the head of 14時.

    Folding by position would have labelled 13:45-14:45 as 13時, which is a claim about
    an hour the reader can check against their own clock.
    """
    # Ten hours is long enough that a quarter and a half hour to a point both overflow,
    # so the fold settles on whole hours and the boundaries become visible.
    started = datetime(2026, 9, 11, 4, 45, tzinfo=UTC)  # 13:45 JST
    labels, points = daily._folded(_stretch(10, started))
    assert labels[:3] == ["13時", "14時", "15時"]
    # The first point is that single quarter hour; the ones after it are whole hours.
    assert [point.covered_seconds for point in points[:3]] == [900.0, 3600.0, 3600.0]


def test_the_hours_left_are_the_same_slope_solved_for_empty() -> None:
    hours = [Snapshot.model_validate(row) for row in day_of_slots(RETURN)]
    latest = StateUpdate.model_validate(state_update(RETURN, state_of_charge_percent=62))

    assert daily.slope_percent_per_hour(hours) == -1.0
    assert daily.hours_remaining(latest, hours) == 62.0


def test_there_are_no_hours_left_to_count_while_charging() -> None:
    hours = [Snapshot.model_validate(row) for row in day_of_slots(RETURN)]
    charging = StateUpdate.model_validate(
        state_update(RETURN, battery_state="charging", external_input=True, input_watts=58)
    )

    assert daily.hours_remaining(charging, hours) is None


def test_a_battery_that_is_not_falling_has_no_hour_count() -> None:
    """ "Forever" is not an answer, and neither is a negative number of hours."""
    flat = [Snapshot.model_validate(row) for row in day_of_slots(RETURN, first_charge=62)]
    for row in flat:
        object.__setattr__(row, "state_of_charge_start_percent", 62)
        object.__setattr__(row, "state_of_charge_end_percent", 62)
    latest = StateUpdate.model_validate(state_update(RETURN, state_of_charge_percent=62))

    assert daily.hours_remaining(latest, flat) is None


def test_the_summary_asks_the_warehouse_these_questions() -> None:
    warehouse = warehouse_with()

    notification = daily.build(warehouse, NOW)

    assert notification is not None
    assert notification.kind == DAILY
    # Every statement the summary runs, by name, and how often.
    assert sorted(name for name, _ in warehouse.queried) == [
        "energy_between",
        "energy_between",
        "finished_pulldowns_between",
        "latest_state_update",
        "snapshots",
    ]


def test_the_day_is_reported_against_the_day_before_it() -> None:
    def energy(given: dict) -> list[dict]:
        today = given["start"] >= NOW - daily.TODAY
        return [{"discharged_watt_hours": 300.0 if today else 200.0, "charged_watt_hours": 0.0}]

    notification = daily.build(warehouse_with(energy_between=energy), NOW)

    assert notification is not None
    assert "前日より 100.0 Wh 多い" in notification.text


def test_a_day_with_nothing_to_compare_against_says_so() -> None:
    def energy(given: dict) -> list[dict]:
        today = given["start"] >= NOW - daily.TODAY
        return [{"discharged_watt_hours": 300.0 if today else 0.0, "charged_watt_hours": 0.0}]

    notification = daily.build(warehouse_with(energy_between=energy), NOW)

    assert notification is not None
    assert "前日と比べる記録なし" in notification.text


def test_nothing_is_summarised_before_any_state_update() -> None:
    assert daily.build(warehouse_with(latest_state_update=[]), NOW) is None


def test_a_battery_that_empties_before_morning_is_given_the_hour_not_a_zero() -> None:
    """The projection saturates at zero, so "翌朝 0 %" reads as "it lasts the night".

    It does not: at this slope the cooler stops hours before morning, and when that
    happens is the thing worth knowing tonight.
    """
    # The slots fall 1 %/h, so 5 % runs out five hours from the reading -- the job
    # fires at 20:00 JST and the outlook's morning is 07:00, eleven hours away.
    nearly_empty = state_update(RETURN, state_of_charge_percent=5)

    notification = daily.build(warehouse_with(latest_state_update=[nearly_empty]), NOW)

    assert notification is not None
    assert "ごろに空になる見込み" in notification.text
    assert "翌朝" not in notification.text


def test_everything_the_message_says_comes_before_the_charts() -> None:
    """A reader who stops at the first picture has already read all of it."""
    notification = daily.build(warehouse_with(), NOW)

    assert notification is not None
    kinds = [block["type"] for block in notification.blocks]
    first_chart = kinds.index("data_visualization")
    assert "section" not in kinds[first_chart:], kinds

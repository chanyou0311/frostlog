from datetime import UTC, datetime, timedelta

import pytest
from conftest import FakeWarehouse, at, pulldown_row, quarters, slot, state_update, upload_run

from frostlog_notifier import charts, formatting, homecoming
from frostlog_notifier.events import UploadRun
from frostlog_notifier.notification import HOMECOMING
from frostlog_notifier.queries import Snapshot, StateUpdate

RETURN = at("2026-09-11", 12, 3)  # 21:03 JST
#: The run that shipped the trip: started on arrival, finished two minutes later.
ARRIVAL = UploadRun.model_validate(
    upload_run(
        finished_at=RETURN + timedelta(minutes=2),
        started_at=RETURN,
        previous_finished_at=RETURN - timedelta(hours=9),
    )
)


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
        "latest_state_update_at": [state_update(RETURN, state_of_charge_percent=62)],
        "energy_between": [
            {"discharged_watt_hours": 128.4, "charged_watt_hours": 40.2},
        ],
        "finished_pulldowns_between": [pulldown_row(at("2026-09-11", 0, 30))],
        "snapshots": day_of_slots(RETURN),
    }
    return FakeWarehouse(answers | overrides)


def _nothing_posted(kind: str, keys: list[str]) -> set[str]:
    """The state table with no row for any of these returns."""
    return set()


def test_an_event_without_upload_runs_summarises_nothing() -> None:
    assert homecoming.build_all(warehouse_with(), [], None, _nothing_posted) == []


def test_a_run_that_follows_the_previous_one_closely_is_not_a_return() -> None:
    run = UploadRun.model_validate(
        upload_run(
            finished_at=RETURN + timedelta(minutes=7),
            started_at=RETURN + timedelta(minutes=5),
            previous_finished_at=RETURN + timedelta(minutes=2),
        )
    )
    assert homecoming.is_return(run) is False
    assert homecoming.build_all(warehouse_with(), [run], None, _nothing_posted) == []


def test_the_first_run_of_all_is_a_return() -> None:
    run = UploadRun.model_validate(upload_run(finished_at=RETURN, previous_finished_at=None))
    assert homecoming.is_return(run) is True


def test_a_run_without_a_start_falls_back_to_when_it_finished() -> None:
    run = UploadRun.model_validate(
        upload_run(finished_at=RETURN, previous_finished_at=RETURN - timedelta(hours=3))
    )
    assert homecoming.is_return(run) is True
    [notification] = homecoming.build_all(warehouse_with(), [run], None, _nothing_posted)
    assert notification.key == RETURN.isoformat()


def test_a_gap_of_two_hours_makes_one_summary() -> None:
    [notification] = homecoming.build_all(warehouse_with(), [ARRIVAL], None, _nothing_posted)
    assert notification.kind == HOMECOMING
    assert notification.key == ARRIVAL.finished_at.isoformat()
    assert notification.coverage_end == RETURN
    assert "ポータブル冷蔵庫のバッテリー" in notification.text
    assert "前回のアップロードから 9.0 h" in notification.text
    # The reading is dated, so nobody reads an hours-old number as now.
    assert "残量 62 % (09-11 21:03 時点)" in notification.text
    assert "残量 62 %" in notification.text
    assert "消費 128.4 Wh" in notification.text
    assert "充電 40.2 Wh" in notification.text
    assert "消費 128.4 Wh / 充電 40.2 Wh" in notification.text
    assert "設定温度まで冷却 1 回" in notification.text
    kinds = [block["type"] for block in notification.blocks]
    assert kinds.count("data_visualization") == 2


def test_the_period_ends_at_the_last_update_the_run_carried() -> None:
    warehouse = warehouse_with()
    homecoming.build_all(warehouse, [ARRIVAL], None, _nothing_posted)
    assert dict(warehouse.queried)["latest_state_update_at"] == {"moment": ARRIVAL.finished_at}


def test_the_period_starts_where_the_previous_summary_ended() -> None:
    previous = at("2026-09-11", 0, 0)
    warehouse = warehouse_with()
    homecoming.build_all(warehouse, [ARRIVAL], previous, _nothing_posted)
    energy = dict(warehouse.queried)["energy_between"]
    assert energy["start"] == previous
    assert energy["end"] == RETURN


def test_the_first_summary_of_all_reaches_back_a_day() -> None:
    warehouse = warehouse_with()
    homecoming.build_all(warehouse, [ARRIVAL], None, _nothing_posted)
    energy = dict(warehouse.queried)["energy_between"]
    assert energy["end"] - energy["start"] == timedelta(hours=24)


def test_two_returns_in_one_event_are_chained() -> None:
    later = at("2026-09-12", 12, 0)
    warehouse = FakeWarehouse(
        {
            "latest_state_update_at": lambda given: [state_update(given["moment"])],
            "energy_between": [{"discharged_watt_hours": 10.0, "charged_watt_hours": 0.0}],
            "finished_pulldowns_between": [],
            "snapshots": day_of_slots(RETURN),
        }
    )
    second = UploadRun.model_validate(
        upload_run(finished_at=later, started_at=later, previous_finished_at=ARRIVAL.finished_at)
    )
    first, latest = homecoming.build_all(warehouse, [second, ARRIVAL], None, _nothing_posted)
    assert [first.key, latest.key] == [ARRIVAL.finished_at.isoformat(), later.isoformat()]
    # The second summary begins where the first one stopped, not a day back.
    starts = [given["start"] for name, given in warehouse.queried if name == "energy_between"]
    assert starts[1] == first.coverage_end


def test_nothing_is_summarised_before_any_state_update() -> None:
    assert (
        homecoming.build_all(
            warehouse_with(latest_state_update_at=[]), [ARRIVAL], None, _nothing_posted
        )
        == []
    )


def test_the_outlook_follows_the_slope_of_the_last_unplugged_hours() -> None:
    hours = [Snapshot.model_validate(row) for row in day_of_slots(RETURN)]
    latest = StateUpdate.model_validate(state_update(RETURN, state_of_charge_percent=62))
    # One percent an hour down, and 9.95 hours from 21:03 JST to 07:00 the next morning.
    morning = datetime(2026, 9, 11, 22, 0, tzinfo=UTC)
    assert homecoming.slope_percent_per_hour(hours) == -1.0
    assert homecoming.projected_state_of_charge(latest, hours, morning) == (
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
    assert homecoming.slope_percent_per_hour(slots) == -2 / 0.375


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
    assert homecoming.slope_percent_per_hour(slots) == pytest.approx(-14.0)


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
    assert homecoming.slope_percent_per_hour(before + charging + after) == pytest.approx(-8.0)


def test_there_is_no_outlook_while_charging() -> None:
    hours = [Snapshot.model_validate(row) for row in day_of_slots(RETURN)]
    charging = StateUpdate.model_validate(
        state_update(RETURN, battery_state="charging", external_input=True, input_watts=58)
    )
    morning = datetime(2026, 9, 11, 22, 0, tzinfo=UTC)
    assert homecoming.projected_state_of_charge(charging, hours, morning) == (
        None,
        homecoming.PLUGGED_IN,
    )

    [notification] = homecoming.build_all(
        warehouse_with(latest_state_update_at=[dict(charging.model_dump())]),
        [ARRIVAL],
        None,
        _nothing_posted,
    )
    assert "外部電源につながっているので翌朝の見込みはなし" in notification.text


def test_there_is_no_outlook_without_an_unplugged_hour() -> None:
    hours = [Snapshot.model_validate(row) for row in day_of_slots(RETURN, plugged=range(24))]
    latest = StateUpdate.model_validate(state_update(RETURN))
    assert homecoming.slope_percent_per_hour(hours) is None
    assert homecoming.projected_state_of_charge(latest, hours, RETURN + timedelta(hours=10)) == (
        None,
        homecoming.NO_SLOPE,
    )


def test_the_outlook_never_leaves_the_scale() -> None:
    hours = [
        Snapshot.model_validate(slot(RETURN - timedelta(hours=1), 2, delta=-30)),
    ]
    latest = StateUpdate.model_validate(state_update(RETURN, state_of_charge_percent=2))
    assert homecoming.projected_state_of_charge(latest, hours, RETURN + timedelta(hours=10)) == (
        0.0,
        None,
    )


def _stretch(hours_recorded: float, starting_at: datetime = RETURN) -> list[Snapshot]:
    """A dense run of quarter hours, the last of them ending at ``starting_at``."""
    count = round(hours_recorded * 3600 / homecoming.SLOT_SECONDS)
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
    labels, points = homecoming._folded(slots)
    assert len(points) == len(labels)
    assert len(points) <= charts.MAX_POINTS, f"{hours_recorded} h is {len(points)} points"


def test_a_shorter_stretch_is_charted_more_finely() -> None:
    """Two hours out used to be two points; the fold follows the length now."""
    assert homecoming._fold_factor(_stretch(2)) == 1, "a quarter hour to a point while it fits"
    assert homecoming._fold_factor(_stretch(2)) < homecoming._fold_factor(_stretch(9))


def test_a_group_starts_on_the_clock_not_where_the_recording_did() -> None:
    """A stretch from 13:45 puts that quarter in the 13時 point, not at the head of 14時.

    Folding by position would have labelled 13:45-14:45 as 13時, which is a claim about
    an hour the reader can check against their own clock.
    """
    # Ten hours is long enough that a quarter and a half hour to a point both overflow,
    # so the fold settles on whole hours and the boundaries become visible.
    started = datetime(2026, 9, 11, 4, 45, tzinfo=UTC)  # 13:45 JST
    labels, points = homecoming._folded(_stretch(10, started))
    assert labels[:3] == ["13時", "14時", "15時"]
    # The first point is that single quarter hour; the ones after it are whole hours.
    assert [point.covered_seconds for point in points[:3]] == [900.0, 3600.0, 3600.0]


def test_the_key_does_not_move_when_a_late_chunk_changes_the_start() -> None:
    """The producer recomputes started_at as a MAX over the whole events table.

    An upload_started that arrives in a later chunk moves that MAX, so a key built
    on it would name the same return twice. finished_at is the upload_done row's own
    ts and cannot move.
    """
    warehouse = FakeWarehouse(
        {
            "latest_state_update_at": lambda given: [state_update(given["moment"])],
            "energy_between": [{"discharged_watt_hours": 10.0, "charged_watt_hours": 0.0}],
            "finished_pulldowns_between": [],
            "snapshots": day_of_slots(RETURN),
        }
    )
    late_start = UploadRun.model_validate(
        upload_run(
            finished_at=ARRIVAL.finished_at,
            started_at=RETURN + timedelta(minutes=1),  # a nearer upload_started turned up
            previous_finished_at=ARRIVAL.previous_finished_at,
        )
    )

    [before] = homecoming.build_all(warehouse, [ARRIVAL], None, _nothing_posted)
    [after] = homecoming.build_all(warehouse, [late_start], None, _nothing_posted)

    assert before.key == after.key


def test_a_return_already_posted_is_not_built_again() -> None:
    """Four queries per summary, and a return rides two days of events."""
    warehouse = FakeWarehouse(
        {
            "latest_state_update_at": lambda given: [state_update(given["moment"])],
            "energy_between": [{"discharged_watt_hours": 10.0, "charged_watt_hours": 0.0}],
            "finished_pulldowns_between": [],
            "snapshots": day_of_slots(RETURN),
        }
    )

    assert homecoming.build_all(warehouse, [ARRIVAL], None, lambda kind, keys: set(keys)) == []
    assert warehouse.queried == []


def test_the_block_says_which_period_each_number_is_for() -> None:
    """The recorded stretch and the energy period are different spans.

    After a silence the chart covers a few hours while the energy is counted from
    where the last summary stopped, which can be days. A heading naming only the
    first would make the second read as three hours of consumption.
    """
    warehouse = FakeWarehouse(
        {
            "latest_state_update_at": lambda given: [state_update(given["moment"])],
            "energy_between": [{"discharged_watt_hours": 1200.0, "charged_watt_hours": 0.0}],
            "finished_pulldowns_between": [],
            "snapshots": day_of_slots(RETURN),
        }
    )
    previous = RETURN - timedelta(days=3)

    [notification] = homecoming.build_all(warehouse, [ARRIVAL], previous, _nothing_posted)

    period = next(
        block["text"]["text"]
        for block in notification.blocks
        if block.get("text", {}).get("text", "").startswith("*記録があったのは")
    )
    recorded_line, energy_line = period.splitlines()[:2]
    assert "外部電源" in recorded_line  # the ratio belongs to the stretch it is measured over
    assert "消費" in energy_line and "充電" in energy_line
    # The energy line carries its own start, which is the previous summary's end --
    # three days before the stretch the line above it names.
    assert formatting.stamp(previous) in energy_line
    assert formatting.stamp(previous) not in recorded_line

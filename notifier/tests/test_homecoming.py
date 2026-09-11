from datetime import UTC, datetime, timedelta

import pytest
from conftest import FakeWarehouse, at, hourly, pulldown_row, state_update, upload_run

from frostlog_notifier import homecoming
from frostlog_notifier.events import UploadRun
from frostlog_notifier.notification import HOMECOMING
from frostlog_notifier.queries import HourlySnapshot, StateUpdate

RETURN = at("2026-09-11", 12, 3)  # 21:03 JST
#: The run that shipped the trip: started on arrival, finished two minutes later.
ARRIVAL = UploadRun.model_validate(
    upload_run(
        finished_at=RETURN + timedelta(minutes=2),
        started_at=RETURN,
        previous_finished_at=RETURN - timedelta(hours=9),
    )
)


def day_of_hours(end: datetime, first_charge: int = 86, plugged: range = range(0)) -> list[dict]:
    """24 dense hours ending at ``end``: one percent an hour, some of them plugged in."""
    rows = []
    charge = first_charge
    for index in range(24):
        started = end.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23 - index)
        if index in plugged:
            charge = min(100, charge + 6)
            rows.append(
                hourly(
                    started,
                    charge,
                    delta=6,
                    external_input_ratio=1.0,
                    charging_ratio=1.0,
                    charged_watt_hours=54.0,
                    discharged_watt_hours=0.0,
                )
            )
        else:
            charge -= 1
            rows.append(hourly(started, charge))
    return rows


def warehouse_with(**overrides) -> FakeWarehouse:
    answers = {
        "latest_state_update_at": [state_update(RETURN, state_of_charge_percent=62)],
        "energy_between": [
            {"discharged_watt_hours": 128.4, "charged_watt_hours": 40.2},
        ],
        "finished_pulldowns_between": [pulldown_row(at("2026-09-11", 0, 30))],
        "hourly_snapshots": day_of_hours(RETURN),
    }
    return FakeWarehouse(answers | overrides)


def test_an_event_without_upload_runs_summarises_nothing() -> None:
    assert homecoming.build_all(warehouse_with(), [], None) == []


def test_a_run_that_follows_the_previous_one_closely_is_not_a_return() -> None:
    run = UploadRun.model_validate(
        upload_run(
            finished_at=RETURN + timedelta(minutes=7),
            started_at=RETURN + timedelta(minutes=5),
            previous_finished_at=RETURN + timedelta(minutes=2),
        )
    )
    assert homecoming.is_return(run) is False
    assert homecoming.build_all(warehouse_with(), [run], None) == []


def test_the_first_run_of_all_is_a_return() -> None:
    run = UploadRun.model_validate(upload_run(finished_at=RETURN, previous_finished_at=None))
    assert homecoming.is_return(run) is True


def test_a_run_without_a_start_falls_back_to_when_it_finished() -> None:
    run = UploadRun.model_validate(
        upload_run(finished_at=RETURN, previous_finished_at=RETURN - timedelta(hours=3))
    )
    assert homecoming.is_return(run) is True
    [notification] = homecoming.build_all(warehouse_with(), [run], None)
    assert notification.key == RETURN.isoformat()


def test_a_gap_of_two_hours_makes_one_summary() -> None:
    [notification] = homecoming.build_all(warehouse_with(), [ARRIVAL], None)
    assert notification.kind == HOMECOMING
    assert notification.key == RETURN.isoformat()
    assert notification.coverage_end == RETURN
    assert "帰宅の要約" in notification.text
    assert "前回の到着から 9.0 h" in notification.text
    assert "SoC 62 %" in notification.text
    assert "消費 128.4 Wh" in notification.text
    assert "充電 40.2 Wh" in notification.text
    assert "差引 -88.2 Wh" in notification.text
    assert "プルダウン完了: 1 件" in notification.text
    assert notification.image is not None
    assert notification.image.startswith(b"\x89PNG")


def test_the_period_ends_at_the_last_update_the_run_carried() -> None:
    warehouse = warehouse_with()
    homecoming.build_all(warehouse, [ARRIVAL], None)
    assert dict(warehouse.queried)["latest_state_update_at"] == {"moment": ARRIVAL.finished_at}


def test_the_period_starts_where_the_previous_summary_ended() -> None:
    previous = at("2026-09-11", 0, 0)
    warehouse = warehouse_with()
    homecoming.build_all(warehouse, [ARRIVAL], previous)
    energy = dict(warehouse.queried)["energy_between"]
    assert energy["start"] == previous
    assert energy["end"] == RETURN


def test_the_first_summary_of_all_reaches_back_a_day() -> None:
    warehouse = warehouse_with()
    homecoming.build_all(warehouse, [ARRIVAL], None)
    energy = dict(warehouse.queried)["energy_between"]
    assert energy["end"] - energy["start"] == timedelta(hours=24)


def test_two_returns_in_one_event_are_chained() -> None:
    later = at("2026-09-12", 12, 0)
    warehouse = FakeWarehouse(
        {
            "latest_state_update_at": lambda given: [state_update(given["moment"])],
            "energy_between": [{"discharged_watt_hours": 10.0, "charged_watt_hours": 0.0}],
            "finished_pulldowns_between": [],
            "hourly_snapshots": day_of_hours(RETURN),
        }
    )
    second = UploadRun.model_validate(
        upload_run(finished_at=later, started_at=later, previous_finished_at=ARRIVAL.finished_at)
    )
    first, latest = homecoming.build_all(warehouse, [second, ARRIVAL], None)
    assert [first.key, latest.key] == [RETURN.isoformat(), later.isoformat()]
    # The second summary begins where the first one stopped, not a day back.
    starts = [given["start"] for name, given in warehouse.queried if name == "energy_between"]
    assert starts[1] == first.coverage_end


def test_nothing_is_summarised_before_any_state_update() -> None:
    assert homecoming.build_all(warehouse_with(latest_state_update_at=[]), [ARRIVAL], None) == []


def test_the_outlook_follows_the_slope_of_the_last_unplugged_hours() -> None:
    hours = [HourlySnapshot.model_validate(row) for row in day_of_hours(RETURN)]
    latest = StateUpdate.model_validate(state_update(RETURN, state_of_charge_percent=62))
    # One percent an hour down, and 9.95 hours from 21:03 JST to 07:00 the next morning.
    morning = datetime(2026, 9, 11, 22, 0, tzinfo=UTC)
    assert homecoming.slope_percent_per_hour(hours) == -1.0
    assert homecoming.projected_state_of_charge(latest, hours, morning) == pytest.approx(52.05)


def test_a_partly_covered_hour_weighs_less_in_the_slope() -> None:
    end = RETURN.replace(minute=0)
    hours = [
        HourlySnapshot.model_validate(hourly(end - timedelta(hours=2), 64, delta=-1)),
        HourlySnapshot.model_validate(
            hourly(end - timedelta(hours=1), 63, delta=-1, covered_seconds=1800.0)
        ),
    ]
    # Two percent over one and a half covered hours.
    assert homecoming.slope_percent_per_hour(hours) == -2 / 1.5


def test_there_is_no_outlook_while_charging() -> None:
    hours = [HourlySnapshot.model_validate(row) for row in day_of_hours(RETURN)]
    charging = StateUpdate.model_validate(
        state_update(RETURN, battery_state="charging", external_input=True, input_watts=58)
    )
    morning = datetime(2026, 9, 11, 22, 0, tzinfo=UTC)
    assert homecoming.projected_state_of_charge(charging, hours, morning) is None

    [notification] = homecoming.build_all(
        warehouse_with(latest_state_update_at=[dict(charging.model_dump())]), [ARRIVAL], None
    )
    assert "見込みなし" in notification.text


def test_there_is_no_outlook_without_an_unplugged_hour() -> None:
    hours = [HourlySnapshot.model_validate(row) for row in day_of_hours(RETURN, plugged=range(24))]
    latest = StateUpdate.model_validate(state_update(RETURN))
    assert homecoming.slope_percent_per_hour(hours) is None
    assert homecoming.projected_state_of_charge(latest, hours, RETURN + timedelta(hours=10)) is None


def test_the_outlook_never_leaves_the_scale() -> None:
    hours = [
        HourlySnapshot.model_validate(hourly(RETURN - timedelta(hours=1), 2, delta=-30)),
    ]
    latest = StateUpdate.model_validate(state_update(RETURN, state_of_charge_percent=2))
    assert homecoming.projected_state_of_charge(latest, hours, RETURN + timedelta(hours=10)) == 0.0

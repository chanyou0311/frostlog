from datetime import UTC, datetime, timedelta

import pytest
from conftest import FakeWarehouse, at, hourly, pulldown_row, state_update

from frostlog_notifier import homecoming
from frostlog_notifier.notification import HOMECOMING
from frostlog_notifier.queries import HourlySnapshot, StateUpdate

RETURN = at("2026-09-11", 12, 3)  # 21:03 JST


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
        "upload_started_times": [
            {"ts": RETURN - timedelta(minutes=2)},
            {"ts": RETURN - timedelta(hours=9)},
        ],
        "latest_state_update": [state_update(RETURN, state_of_charge_percent=62)],
        "energy_between": [
            {"discharged_watt_hours": 128.4, "charged_watt_hours": 40.2},
        ],
        "finished_pulldowns_between": [pulldown_row(at("2026-09-11", 0, 30))],
        "hourly_snapshots": day_of_hours(RETURN),
    }
    return FakeWarehouse(answers | overrides)


def test_no_summary_while_the_uploads_keep_coming() -> None:
    warehouse = warehouse_with(
        upload_started_times=[
            {"ts": RETURN},
            {"ts": RETURN - timedelta(minutes=5)},
        ]
    )
    assert homecoming.build(warehouse, "raw_events", None) is None


def test_no_summary_before_the_second_upload_run_ever() -> None:
    warehouse = warehouse_with(upload_started_times=[{"ts": RETURN}])
    assert homecoming.build(warehouse, "raw_events", None) is None


def test_a_gap_of_two_hours_makes_a_summary() -> None:
    notification = homecoming.build(warehouse_with(), "raw_events", None)
    assert notification is not None
    assert notification.kind == HOMECOMING
    assert notification.key == RETURN.isoformat()
    assert "帰宅の要約" in notification.text
    assert "SoC 62 %" in notification.text
    assert "消費 128.4 Wh" in notification.text
    assert "充電 40.2 Wh" in notification.text
    assert "差引 -88.2 Wh" in notification.text
    assert "プルダウン完了: 1 件" in notification.text
    assert notification.image is not None
    assert notification.image.startswith(b"\x89PNG")


def test_the_period_starts_where_the_previous_summary_ended() -> None:
    previous = at("2026-09-11", 0, 0)
    warehouse = warehouse_with()
    homecoming.build(warehouse, "raw_events", previous.isoformat())
    energy = dict(warehouse.queried)["energy_between"]
    assert energy["start"] == previous
    assert energy["end"] == RETURN


def test_an_unreadable_previous_key_falls_back_to_a_day() -> None:
    warehouse = warehouse_with()
    homecoming.build(warehouse, "raw_events", "not a timestamp")
    energy = dict(warehouse.queried)["energy_between"]
    assert energy["end"] - energy["start"] == timedelta(hours=24)


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

    notification = homecoming.build(
        warehouse_with(latest_state_update=[dict(charging.model_dump())]), "raw_events", None
    )
    assert notification is not None
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


def test_nothing_is_summarised_before_any_state_update() -> None:
    assert homecoming.build(warehouse_with(latest_state_update=[]), "raw_events", None) is None

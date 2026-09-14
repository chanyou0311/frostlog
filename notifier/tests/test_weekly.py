from datetime import UTC, datetime, timedelta

from conftest import FakeWarehouse, empty_slot, pulldown_row, quarters

from frostlog_notifier import weekly
from frostlog_notifier.clock import jst_dates, to_jst
from frostlog_notifier.notification import WEEKLY
from frostlog_notifier.queries import Pulldown, Snapshot

#: The job fires Saturday 09:00 JST; the window is the seven days behind it.
NOW = datetime(2026, 9, 19, 0, 0, tzinfo=UTC)  # 09:00 JST, Saturday
START, END = NOW - weekly.WINDOW, NOW


def week_of_slots() -> list[dict]:
    """Seven days: unplugged all day, plugged in for an hour each evening, one dead night.

    The window starts when the job runs (Saturday morning), not at midnight, so
    ``hour_of_day`` below counts from the window's start rather than from a JST day.
    """
    rows: list[dict] = []
    charge = 100
    for index in range(24 * 7):
        started = START + timedelta(hours=index)
        hour_of_day = index % 24  # hours since the window opened, not JST hours
        if 3 <= index < 8:  # the first night has no data at all
            rows.extend(empty_slot(started + timedelta(minutes=15 * q)) for q in range(4))
            continue
        if hour_of_day == 20:
            charge = min(100, charge + 8)
            rows.extend(
                quarters(
                    started,
                    charge,
                    delta=8,
                    external_input_ratio=1.0,
                    charged_watt_hours=60.0,
                    discharged_watt_hours=0.0,
                    ambient_temperature_celsius=22.0,
                )
            )
            continue
        drop = 2 if hour_of_day in range(12, 18) else 1
        charge = max(0, charge - drop)
        rows.extend(
            quarters(
                started,
                charge,
                delta=-drop,
                ambient_temperature_celsius=27.0 if drop == 2 else 22.0,
                discharged_watt_hours=30.0 if drop == 2 else 18.0,
            )
        )
    return rows


def summarize(rows: list[dict] | None = None, pulldowns: list[dict] | None = None):
    # The week is asked by the hour, so the slots are folded first, as build() does.
    hours = weekly.by_hour([Snapshot.model_validate(row) for row in (rows or week_of_slots())])
    episodes = [Pulldown.model_validate(row) for row in (pulldowns or [])]
    return weekly.summarize(hours, episodes, jst_dates(START, END))


def test_the_totals_are_the_sums_of_the_hours() -> None:
    rows = week_of_slots()
    summary = summarize(rows)
    assert summary.discharged_watt_hours == sum(row["discharged_watt_hours"] or 0 for row in rows)
    assert summary.charged_watt_hours == sum(row["charged_watt_hours"] or 0 for row in rows)
    assert summary.net_watt_hours == summary.charged_watt_hours - summary.discharged_watt_hours


def test_external_input_hours_count_the_covered_share() -> None:
    summary = summarize()
    assert summary.external_input_hours == 7.0  # one hour a day
    assert summary.average_charge_watts == 60.0  # 60 Wh in each of those hours


def test_the_driving_hours_needed_are_the_deficit_over_the_charge_rate() -> None:
    summary = summarize()
    deficit = summary.discharged_watt_hours - summary.charged_watt_hours
    assert summary.balancing_hours is not None
    assert summary.balancing_hours == deficit / 60.0


def test_a_week_that_pays_for_itself_needs_no_driving() -> None:
    rows = [
        row
        for index in range(24)
        for row in quarters(
            START + timedelta(hours=index),
            100,
            delta=0,
            discharged_watt_hours=0.0,
            charged_watt_hours=10.0,
            external_input_ratio=1.0,
        )
        for index in range(24)
    ]
    assert summarize(rows).balancing_hours is None


def test_gaps_between_the_first_and_the_last_hour_are_counted() -> None:
    summary = summarize()
    assert summary.gap_hours == 5
    assert summary.missing_days == []


def test_a_day_without_any_data_is_named() -> None:
    # Blanked by JST date rather than by slot index: the window starts when the job
    # runs, not at midnight, so a day of it is not a round number of slots from the
    # start. A missing day is a JST day, which is what the summary names.
    target = jst_dates(START, END)[3]
    rows = [
        empty_slot(row["slot_started_at"])
        if to_jst(row["slot_started_at"]).date() == target
        else row
        for row in week_of_slots()
    ]
    summary = summarize(rows)
    assert target in summary.missing_days


def test_pulldowns_are_counted_and_averaged_by_trigger() -> None:
    episodes = [
        pulldown_row(START + timedelta(hours=1)),
        pulldown_row(START + timedelta(hours=30), pulldown_key="pd-b", duration_seconds=1800.0),
        pulldown_row(
            START + timedelta(hours=50), pulldown_key="pd-c", trigger="rise", duration_seconds=600.0
        ),
    ]
    triggers = {stat.trigger: stat for stat in summarize(pulldowns=episodes).triggers}
    assert triggers["start_up"].count == 2
    assert triggers["start_up"].mean_duration_seconds == (46 * 60 + 1800) / 2
    assert triggers["rise"].count == 1
    assert triggers["rise"].mean_duration_seconds == 600.0


def test_the_summary_is_built_with_its_text_and_chart() -> None:
    warehouse = FakeWarehouse(
        {
            "snapshots": week_of_slots(),
            "finished_pulldowns_between": [pulldown_row(START + timedelta(hours=1))],
        }
    )
    notification = weekly.build(warehouse, NOW)
    assert notification.kind == WEEKLY
    assert "直近 7 日の要約" in notification.text
    assert (
        f"{jst_dates(START, END)[0]:%m-%d} - {jst_dates(START, END)[-1]:%m-%d}" in notification.text
    )
    assert "均衡に必要な走行" in notification.text
    assert "プルダウン 1 件" in notification.text
    assert "データ欠損: 5 時間" in notification.text
    kinds = [block["type"] for block in notification.blocks]
    assert kinds.count("data_visualization") == 2
    assert dict(warehouse.queried)["snapshots"] == {"start": START, "end": END}


def test_the_second_chart_is_the_battery_across_the_days() -> None:
    warehouse = FakeWarehouse(
        {
            "snapshots": week_of_slots(),
            "finished_pulldowns_between": [],
        }
    )

    notification = weekly.build(warehouse, NOW)

    charted = [b for b in notification.blocks if b["type"] == "data_visualization"]
    assert [b["title"] for b in charted] == [
        "日ごとの電力量 (Wh) ・ 1 点 1 日",
        "日ごとのバッテリー残量 (%) ・ 1 点 1 日",
    ]
    assert [s["name"] for s in charted[1]["chart"]["series"]] == ["最高", "最低"]


def test_a_day_with_no_reading_is_left_out_of_the_battery_chart() -> None:
    """Slack cannot draw a hole, and a hole invented here would be a reading nobody took."""
    target = jst_dates(START, END)[3]
    rows = [
        empty_slot(row["slot_started_at"])
        if to_jst(row["slot_started_at"]).date() == target
        else row
        for row in week_of_slots()
    ]
    warehouse = FakeWarehouse({"snapshots": rows, "finished_pulldowns_between": []})

    notification = weekly.build(warehouse, NOW)

    charted = [b for b in notification.blocks if b["type"] == "data_visualization"]
    labels = [point["label"] for point in charted[1]["chart"]["series"][0]["data"]]
    assert f"{target:%m-%d}" not in labels
    assert labels, "the other days are still drawn"

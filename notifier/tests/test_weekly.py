from datetime import UTC, date, datetime, timedelta

from conftest import FakeWarehouse, ambient_band_rows, empty_hour, hourly, pulldown_row

from frostlog_notifier import weekly
from frostlog_notifier.clock import iso_week_bounds, jst_dates
from frostlog_notifier.notification import WEEKLY
from frostlog_notifier.queries import Band, HourlySnapshot, Pulldown

START, END = iso_week_bounds(2026, 37)
BANDS = [Band.model_validate(row) for row in ambient_band_rows()]


def week_of_hours() -> list[dict]:
    """Seven days: unplugged all day, plugged in for an hour each evening, one dead night."""
    rows: list[dict] = []
    charge = 100
    for index in range(24 * 7):
        started = START + timedelta(hours=index)
        hour_of_day = index % 24  # the week starts at midnight JST
        if 3 <= index < 8:  # the first night has no data at all
            rows.append(empty_hour(started))
            continue
        if hour_of_day == 20:
            charge = min(100, charge + 8)
            rows.append(
                hourly(
                    started,
                    charge,
                    delta=8,
                    external_input_ratio=1.0,
                    charging_ratio=1.0,
                    charged_watt_hours=60.0,
                    discharged_watt_hours=0.0,
                    ambient_temperature_celsius=22.0,
                )
            )
            continue
        drop = 2 if hour_of_day in range(12, 18) else 1
        charge = max(0, charge - drop)
        rows.append(
            hourly(
                started,
                charge,
                delta=-drop,
                ambient_temperature_celsius=27.0 if drop == 2 else 22.0,
                discharged_watt_hours=30.0 if drop == 2 else 18.0,
            )
        )
    return rows


def summarize(rows: list[dict] | None = None, pulldowns: list[dict] | None = None):
    hours = [HourlySnapshot.model_validate(row) for row in (rows or week_of_hours())]
    episodes = [Pulldown.model_validate(row) for row in (pulldowns or [])]
    return weekly.summarize(hours, BANDS, episodes, jst_dates(START, END))


def test_the_totals_are_the_sums_of_the_hours() -> None:
    rows = week_of_hours()
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
        hourly(
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


def test_the_drop_per_hour_is_reported_per_ambient_temperature_band() -> None:
    summary = summarize()
    bands = {band.label: band for band in summary.bands}
    assert set(bands) == {"20..25 °C", "25..30 °C"}
    assert bands["20..25 °C"].percent_per_hour == -1.0
    assert bands["25..30 °C"].percent_per_hour == -2.0
    # The plugged-in hours are left out of the bands entirely.
    assert bands["20..25 °C"].hours == 24 * 7 - 5 - 7 - 6 * 7


def test_the_hours_from_full_use_the_band_with_the_most_hours() -> None:
    summary = summarize()
    assert summary.most_common_band is not None
    assert summary.most_common_band.label == "20..25 °C"
    assert summary.hours_from_full == 100.0


def test_gaps_between_the_first_and_the_last_hour_are_counted() -> None:
    summary = summarize()
    assert summary.gap_hours == 5
    assert summary.missing_days == []


def test_a_day_without_any_data_is_named() -> None:
    rows = week_of_hours()
    for index in range(48, 72):  # the third UTC day of the week
        rows[index] = empty_hour(START + timedelta(hours=index))
    summary = summarize(rows)
    assert date(2026, 9, 9) in summary.missing_days


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


def test_the_week_to_summarise_is_the_one_that_just_ended() -> None:
    assert weekly.due_week(datetime(2026, 9, 14, 12, tzinfo=UTC)) == (2026, 37)


def test_the_summary_is_built_with_its_text_and_chart() -> None:
    warehouse = FakeWarehouse(
        {
            "hourly_snapshots": week_of_hours(),
            "ambient_bands": ambient_band_rows(),
            "finished_pulldowns_between": [pulldown_row(START + timedelta(hours=1))],
        }
    )
    notification = weekly.build(warehouse, 2026, 37)
    assert notification.kind == WEEKLY
    assert notification.key == "2026-W37"
    assert "週の要約 2026-W37" in notification.text
    assert "09-07 - 09-13" in notification.text
    assert "均衡に必要な走行" in notification.text
    assert "100 % からの持ち時間" in notification.text
    assert "プルダウン 1 件" in notification.text
    assert "データ欠損: 5 時間" in notification.text
    assert notification.image is not None
    assert notification.image.startswith(b"\x89PNG")
    assert dict(warehouse.queried)["hourly_snapshots"] == {"start": START, "end": END}

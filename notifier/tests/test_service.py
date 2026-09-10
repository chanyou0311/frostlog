from datetime import timedelta

import pytest
from conftest import (
    FakeSlack,
    FakeWarehouse,
    ambient_band_rows,
    at,
    hourly,
    pulldown_row,
    state_update,
)
from test_homecoming import day_of_hours
from test_weekly import week_of_hours

from frostlog_notifier.clock import iso_week_bounds
from frostlog_notifier.errors import Transient
from frostlog_notifier.events import QualityReport, SemanticUpdated
from frostlog_notifier.service import Notifier
from frostlog_notifier.settings import Settings
from frostlog_notifier.state import PostedNotifications

RETURN = at("2026-09-11", 12, 3)
UPDATED = SemanticUpdated(
    run_id="run-17",
    published_at=RETURN,
    date_keys=[20260911],
    raw_uploaded_at_max=RETURN,
    build_passed=True,
)


def arrivals(gap: timedelta = timedelta(hours=9)) -> list[dict]:
    return [{"ts": RETURN}, {"ts": RETURN - gap}]


def build_notifier(
    warehouse: FakeWarehouse, slack: FakeSlack, settings: Settings, now=lambda: RETURN
) -> Notifier:
    return Notifier(
        warehouse=warehouse,
        posted=PostedNotifications(warehouse, settings.posted_table),
        slack=slack,
        settings=settings,
        now=now,
    )


@pytest.fixture
def arrived() -> FakeWarehouse:
    """A warehouse that has just received the data of a trip that ended now."""
    return FakeWarehouse(
        {
            "upload_started_times": arrivals(),
            "latest_state_update": [state_update(RETURN)],
            "energy_between": [{"discharged_watt_hours": 128.4, "charged_watt_hours": 40.2}],
            "finished_pulldowns_between": [pulldown_row(at("2026-09-11", 8, 12))],
            "state_updates_between": [state_update(at("2026-09-11", 8, 12))],
            "hourly_snapshots": day_of_hours(RETURN),
            "ambient_bands": ambient_band_rows(),
        }
    )


def test_an_arrival_posts_the_summary_the_pulldown_and_the_closed_week(
    arrived: FakeWarehouse, slack: FakeSlack, settings: Settings
) -> None:
    notifier = build_notifier(arrived, slack, settings)
    posted = notifier.handle(UPDATED)
    # The data is from Friday of week 37, so week 36 is over and gets its summary too.
    assert [notification.kind for notification in posted] == ["homecoming", "pulldown", "weekly"]
    assert len(slack.messages) == 3


def test_the_same_event_delivered_again_posts_nothing(
    arrived: FakeWarehouse, slack: FakeSlack, settings: Settings
) -> None:
    notifier = build_notifier(arrived, slack, settings)
    notifier.handle(UPDATED)
    assert notifier.handle(UPDATED) == []
    assert len(slack.messages) == 3


def test_a_run_that_did_not_build_changes_nothing(
    arrived: FakeWarehouse, slack: FakeSlack, settings: Settings
) -> None:
    notifier = build_notifier(arrived, slack, settings)
    assert notifier.handle(UPDATED.model_copy(update={"build_passed": False})) == []
    assert slack.messages == []


def test_a_failed_contract_test_is_posted_with_its_checks(
    warehouse: FakeWarehouse, slack: FakeSlack, settings: Settings
) -> None:
    report = QualityReport(
        run_id="daily-1",
        published_at=RETURN,
        contract_id="frostlog-semantic",
        passed=False,
        failed_checks=["hours_are_dense"],
    )
    notifier = build_notifier(warehouse, slack, settings)
    [notification] = notifier.handle(report)
    assert notification.key == "frostlog-semantic/daily-1"
    assert "hours_are_dense" in slack.messages[0][0]
    assert slack.messages[0][1] is None  # no chart
    assert notifier.handle(report) == []


def test_a_passing_contract_test_says_nothing(
    warehouse: FakeWarehouse, slack: FakeSlack, settings: Settings
) -> None:
    report = QualityReport(
        run_id="daily-2",
        published_at=RETURN,
        contract_id="frostlog-raw",
        passed=True,
        failed_checks=[],
    )
    assert build_notifier(warehouse, slack, settings).handle(report) == []
    assert slack.messages == []


def test_the_weekly_summary_follows_the_first_data_of_the_new_week(
    slack: FakeSlack, settings: Settings
) -> None:
    _, end = iso_week_bounds(2026, 37)
    monday = end + timedelta(hours=6)
    warehouse = FakeWarehouse(
        {
            "upload_started_times": [{"ts": monday}],  # no arrival gap, so no summary
            "latest_state_update": [state_update(monday)],
            "finished_pulldowns_between": [],
            "hourly_snapshots": week_of_hours(),
            "ambient_bands": ambient_band_rows(),
        }
    )
    notifier = build_notifier(warehouse, slack, settings, now=lambda: monday)
    [notification] = notifier.handle(UPDATED)
    assert notification.key == "2026-W37"
    assert notifier.handle(UPDATED) == []


def test_the_weekly_summary_covers_the_week_that_ended_not_the_running_one(
    slack: FakeSlack, settings: Settings
) -> None:
    start, _ = iso_week_bounds(2026, 37)
    midweek = start + timedelta(days=3)
    warehouse = FakeWarehouse(
        {
            "upload_started_times": [{"ts": midweek}],
            "latest_state_update": [state_update(midweek)],
            "finished_pulldowns_between": [],
            "hourly_snapshots": [hourly(midweek, 80)],
            "ambient_bands": ambient_band_rows(),
        }
    )
    # The week before is 2026-W36, whose end is behind us: it is the one that gets posted.
    notifier = build_notifier(warehouse, slack, settings, now=lambda: midweek)
    [notification] = notifier.handle(UPDATED)
    assert notification.key == "2026-W36"


def test_the_monday_job_posts_last_week(slack: FakeSlack, settings: Settings) -> None:
    _, end = iso_week_bounds(2026, 37)
    monday_evening = end + timedelta(hours=12, minutes=3)
    warehouse = FakeWarehouse(
        {
            "hourly_snapshots": week_of_hours(),
            "ambient_bands": ambient_band_rows(),
            "finished_pulldowns_between": [],
        }
    )
    notifier = build_notifier(warehouse, slack, settings, now=lambda: monday_evening)
    [notification] = notifier.run_weekly_deadline()
    assert notification.key == "2026-W37"
    assert notifier.run_weekly_deadline() == []


def test_a_transient_failure_is_not_swallowed(arrived: FakeWarehouse, settings: Settings) -> None:
    slack = FakeSlack(fail=Transient("Slack is unavailable"))
    with pytest.raises(Transient):
        build_notifier(arrived, slack, settings).handle(UPDATED)


def test_a_failure_reports_itself_to_the_channel(
    arrived: FakeWarehouse, slack: FakeSlack, settings: Settings
) -> None:
    notifier = build_notifier(arrived, slack, settings)
    notifier.report_failure("events/semantic", ValueError("boom"))
    assert "notifier の失敗" in slack.messages[0][0]
    assert "ValueError: boom" in slack.messages[0][0]


def test_a_failure_without_a_token_is_only_logged(
    arrived: FakeWarehouse, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    slack = FakeSlack(enabled=False)
    build_notifier(arrived, slack, settings).report_failure("jobs/weekly-deadline", RuntimeError())
    assert slack.messages == []
    assert "notifier failed" in caplog.text

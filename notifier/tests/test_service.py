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
    upload_run,
)
from test_homecoming import day_of_hours, steps_of
from test_weekly import week_of_hours

from frostlog_notifier.clock import iso_week_bounds
from frostlog_notifier.errors import Transient
from frostlog_notifier.events import QualityReport, SemanticUpdated
from frostlog_notifier.notification import HOMECOMING
from frostlog_notifier.service import Notifier
from frostlog_notifier.settings import Settings
from frostlog_notifier.slack import Slack
from frostlog_notifier.state import PostedNotifications


def updates_in(*weeks: tuple[int, int], count: int = 604):
    """Answers state_update_count_between: ``count`` inside the given ISO weeks, 0 elsewhere."""
    bounds = [iso_week_bounds(*week) for week in weeks]

    def answer(given: dict) -> list[dict]:
        inside = any(given["start"] >= start and given["end"] <= end for start, end in bounds)
        return [{"update_count": count if inside else 0}]

    return answer


RETURN = at("2026-09-11", 12, 3)
#: The upload run that shipped the trip, nine hours after the previous one finished.
ARRIVAL = upload_run(
    finished_at=RETURN + timedelta(minutes=2),
    started_at=RETURN,
    previous_finished_at=RETURN - timedelta(hours=9),
)


def updated(**overrides) -> SemanticUpdated:
    """A semantic_updated event; by default one that carries no upload run."""
    event = {
        "run_id": "run-17",
        "published_at": RETURN,
        "date_keys": [20260911],
        "raw_uploaded_at_max": RETURN,
        "build_passed": True,
        "upload_runs": [],
    }
    return SemanticUpdated.model_validate(event | overrides)


ARRIVED = updated(upload_runs=[ARRIVAL])


def build_notifier(
    warehouse: FakeWarehouse, slack: FakeSlack | Slack, settings: Settings, now=lambda: RETURN
) -> Notifier:
    return Notifier(
        warehouse=warehouse,
        posted=PostedNotifications(warehouse),
        slack=slack,
        now=now,
    )


@pytest.fixture
def arrived() -> FakeWarehouse:
    """A warehouse that has just received the data of a trip that ended now."""
    return FakeWarehouse(
        {
            "latest_state_update": [state_update(RETURN)],
            "latest_state_update_at": [state_update(RETURN)],
            "state_update_count_between": updates_in((2026, 36), (2026, 37)),
            "energy_between": [{"discharged_watt_hours": 128.4, "charged_watt_hours": 40.2}],
            "finished_pulldowns_between": [pulldown_row(at("2026-09-11", 8, 12))],
            "state_updates_between": [state_update(at("2026-09-11", 8, 12))],
            "hourly_snapshots": day_of_hours(RETURN),
            "snapshots": steps_of(RETURN),
            "ambient_bands": ambient_band_rows(),
        }
    )


def test_an_arrival_posts_the_summary_the_pulldown_and_the_closed_week(
    arrived: FakeWarehouse, slack: FakeSlack, settings: Settings
) -> None:
    notifier = build_notifier(arrived, slack, settings)
    posted = notifier.handle(ARRIVED)
    # The data is from Friday of week 37, so week 36 is over and gets its summary too.
    assert [notification.kind for notification in posted] == ["homecoming", "pulldown", "weekly"]
    assert len(slack.messages) == 3


def test_the_same_event_delivered_again_posts_nothing(
    arrived: FakeWarehouse, slack: FakeSlack, settings: Settings
) -> None:
    notifier = build_notifier(arrived, slack, settings)
    notifier.handle(ARRIVED)
    assert notifier.handle(ARRIVED) == []
    assert len(slack.messages) == 3


def test_one_return_is_summarised_once_however_many_chunks_it_took(
    arrived: FakeWarehouse, slack: FakeSlack, settings: Settings
) -> None:
    notifier = build_notifier(arrived, slack, settings)
    # The cooler chunks of the trip arrive first; they carry no upload run.
    for _ in range(3):
        assert [n.kind for n in notifier.handle(updated())] != [HOMECOMING]
    # Then the events chunk, which reports the run that shipped them all.
    [summary] = [n for n in notifier.handle(ARRIVED) if n.kind == HOMECOMING]
    assert summary.key == RETURN.isoformat()

    # A run five minutes later is the Pi still uploading at home, not another return.
    quiet = updated(
        upload_runs=[
            upload_run(
                finished_at=RETURN + timedelta(minutes=7),
                started_at=RETURN + timedelta(minutes=5),
                previous_finished_at=RETURN + timedelta(minutes=2),
            )
        ]
    )
    assert [n for n in notifier.handle(quiet) if n.kind == HOMECOMING] == []
    assert len([text for text, _ in slack.messages if "戻ってきた時点の残量" in text]) == 1


def test_the_next_summary_starts_where_the_last_one_stopped(
    arrived: FakeWarehouse, slack: FakeSlack, settings: Settings
) -> None:
    notifier = build_notifier(arrived, slack, settings)
    notifier.handle(ARRIVED)
    assert PostedNotifications(arrived, "notifier_posted").latest_coverage_end(HOMECOMING) == RETURN


def test_a_run_that_did_not_build_changes_nothing(
    arrived: FakeWarehouse, slack: FakeSlack, settings: Settings
) -> None:
    notifier = build_notifier(arrived, slack, settings)
    assert notifier.handle(ARRIVED.model_copy(update={"build_passed": False})) == []
    assert slack.messages == []
    assert arrived.executed == []  # not even the state table is touched


def test_a_failed_contract_test_is_posted_with_its_checks(
    warehouse: FakeWarehouse, slack: FakeSlack, settings: Settings
) -> None:
    report = QualityReport(
        run_id="daily-1",
        published_at=RETURN,
        contract_id="frostlog-semantics",
        passed=False,
        failed_checks=["hours_are_dense"],
    )
    notifier = build_notifier(warehouse, slack, settings)
    [notification] = notifier.handle(report)
    assert notification.key == "frostlog-semantics/daily-1"
    assert "hours_are_dense" in slack.messages[0][0]
    assert slack.messages[0][1] == []  # words only, no blocks
    assert notifier.handle(report) == []


def test_a_passing_contract_test_says_nothing(
    warehouse: FakeWarehouse, slack: FakeSlack, settings: Settings
) -> None:
    report = QualityReport(
        run_id="daily-2",
        published_at=RETURN,
        contract_id="frostlog-collection",
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
            "latest_state_update": [state_update(monday)],
            "state_update_count_between": updates_in((2026, 37)),
            "finished_pulldowns_between": [],
            "hourly_snapshots": week_of_hours(),
            "ambient_bands": ambient_band_rows(),
        }
    )
    notifier = build_notifier(warehouse, slack, settings, now=lambda: monday)
    [notification] = notifier.handle(updated())
    assert notification.key == "2026-W37"
    assert notifier.handle(updated()) == []


def test_the_weekly_summary_covers_the_week_that_ended_not_the_running_one(
    slack: FakeSlack, settings: Settings
) -> None:
    start, _ = iso_week_bounds(2026, 37)
    midweek = start + timedelta(days=3)
    warehouse = FakeWarehouse(
        {
            "latest_state_update": [state_update(midweek)],
            "state_update_count_between": updates_in((2026, 36), (2026, 37), count=100),
            "finished_pulldowns_between": [],
            "hourly_snapshots": [hourly(midweek, 80)],
            "ambient_bands": ambient_band_rows(),
        }
    )
    # The week before is 2026-W36, whose end is behind us: it is the one that gets posted.
    notifier = build_notifier(warehouse, slack, settings, now=lambda: midweek)
    [notification] = notifier.handle(updated())
    assert notification.key == "2026-W36"


def test_weeks_that_come_home_together_are_each_summarised_oldest_first(
    slack: FakeSlack, settings: Settings
) -> None:
    start, _ = iso_week_bounds(2026, 37)
    midweek = start + timedelta(days=3)
    warehouse = FakeWarehouse(
        {
            "latest_state_update": [state_update(midweek)],
            # Three weeks away: 35 and 36 are closed and unposted, 34 has nothing.
            "state_update_count_between": updates_in((2026, 35), (2026, 36), (2026, 37)),
            "finished_pulldowns_between": [],
            "hourly_snapshots": [hourly(midweek, 80)],
            "ambient_bands": ambient_band_rows(),
        }
    )
    notifier = build_notifier(warehouse, slack, settings, now=lambda: midweek)
    posted = notifier.handle(updated())
    assert [notification.key for notification in posted] == ["2026-W35", "2026-W36"]
    assert notifier.handle(updated()) == []


def test_a_week_without_any_data_is_passed_over_in_silence(
    slack: FakeSlack, settings: Settings
) -> None:
    _, end = iso_week_bounds(2026, 37)
    monday = end + timedelta(hours=6)
    warehouse = FakeWarehouse(
        {
            "latest_state_update": [state_update(monday)],
            "state_update_count_between": [{"update_count": 0}],
            "finished_pulldowns_between": [],
            "hourly_snapshots": [],
            "ambient_bands": ambient_band_rows(),
        }
    )
    notifier = build_notifier(warehouse, slack, settings, now=lambda: monday)
    assert notifier.handle(updated()) == []
    assert slack.messages == []


def test_the_monday_job_posts_last_week(slack: FakeSlack, settings: Settings) -> None:
    _, end = iso_week_bounds(2026, 37)
    monday_evening = end + timedelta(hours=12, minutes=3)
    warehouse = FakeWarehouse(
        {
            "hourly_snapshots": week_of_hours(),
            "state_update_count_between": updates_in((2026, 37)),
            "ambient_bands": ambient_band_rows(),
            "finished_pulldowns_between": [],
        }
    )
    notifier = build_notifier(warehouse, slack, settings, now=lambda: monday_evening)
    [notification] = notifier.run_weekly_deadline()
    assert notification.key == "2026-W37"
    assert notifier.run_weekly_deadline() == []


def test_the_monday_job_says_nothing_about_a_week_without_data(
    slack: FakeSlack, settings: Settings
) -> None:
    _, end = iso_week_bounds(2026, 37)
    monday_evening = end + timedelta(hours=12, minutes=3)
    warehouse = FakeWarehouse(
        {
            "hourly_snapshots": [],
            "state_update_count_between": [{"update_count": 0}],
            "ambient_bands": ambient_band_rows(),
            "finished_pulldowns_between": [],
        }
    )
    notifier = build_notifier(warehouse, slack, settings, now=lambda: monday_evening)
    assert notifier.run_weekly_deadline() == []
    assert slack.messages == []


def test_a_transient_failure_is_not_swallowed(arrived: FakeWarehouse, settings: Settings) -> None:
    slack = FakeSlack(fail=Transient("Slack is unavailable"))
    with pytest.raises(Transient):
        build_notifier(arrived, slack, settings).handle(ARRIVED)


def test_a_failure_reports_itself_to_the_channel(
    arrived: FakeWarehouse, slack: FakeSlack, settings: Settings
) -> None:
    notifier = build_notifier(arrived, slack, settings)
    notifier.report_failure("events/pubsub", ValueError("boom"))
    assert "notifier の失敗" in slack.messages[0][0]
    assert "ValueError: boom" in slack.messages[0][0]


def test_a_defect_that_keeps_coming_back_is_reported_once_a_day(
    arrived: FakeWarehouse, slack: FakeSlack, settings: Settings
) -> None:
    notifier = build_notifier(arrived, slack, settings)
    for _ in range(4):  # Pub/Sub redelivers the same message all day
        notifier.report_failure("events/pubsub", ValueError("boom"))
    assert len(slack.messages) == 1

    # Another defect, and the same one tomorrow, are worth saying.
    notifier.report_failure("events/pubsub", KeyError("other"))
    notifier.report_failure("jobs/weekly-deadline", ValueError("boom"))
    tomorrow = build_notifier(arrived, slack, settings, now=lambda: RETURN + timedelta(days=1))
    tomorrow.report_failure("events/pubsub", ValueError("boom"))
    assert len(slack.messages) == 4


def test_a_failure_without_a_token_is_only_logged(
    arrived: FakeWarehouse, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    slack = FakeSlack(enabled=False)
    build_notifier(arrived, slack, settings).report_failure("jobs/weekly-deadline", RuntimeError())
    assert slack.messages == []
    assert len(slack.dry_runs) == 1
    assert "notifier failed" in caplog.text


def test_without_a_token_everything_is_recorded_as_a_dry_run(
    arrived: FakeWarehouse, settings: Settings
) -> None:
    slack = Slack(token=None, channel="#fumo")
    posted = build_notifier(arrived, slack, settings).handle(ARRIVED)
    rows = {row["kind"]: row for name, row in arrived.executed if name == "record_posted"}
    assert set(rows) == {"homecoming", "pulldown", "weekly"}
    assert all(row["dry_run"] is True for row in rows.values())
    assert all("slack_ts" not in row for row in rows.values())
    # Each of them carries its charts even when there is nowhere to send them.
    for notification in posted:
        assert any(block["type"] == "data_visualization" for block in notification.blocks)

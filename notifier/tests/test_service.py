"""The two scheduled jobs and the one event, against the fakes.

What is not here any more is a test that the same thing is not said twice. There
is nothing to suppress: each job reads a window ending when it fires and says what
it finds, so saying it twice would take running the job twice.
"""

import pytest
from conftest import (
    FakeSlack,
    FakeWarehouse,
    ambient_band_rows,
    at,
    pulldown_row,
    state_update,
)
from test_charts import check as check_chart
from test_daily import day_of_slots
from test_weekly import week_of_slots

from frostlog_notifier import charts
from frostlog_notifier.events import QualityReport, SemanticUpdated
from frostlog_notifier.notification import DAILY, WEEKLY
from frostlog_notifier.service import Notifier
from frostlog_notifier.settings import Settings

#: When the evening job fires: 20:00 JST.
NOW = at("2026-09-11", 11, 0)


def build_notifier(warehouse: FakeWarehouse, slack: FakeSlack, settings: Settings) -> Notifier:
    return Notifier(warehouse=warehouse, slack=slack, now=lambda: NOW)


@pytest.fixture
def recorded() -> FakeWarehouse:
    """A warehouse holding a day of dense slots and a week behind it."""
    return FakeWarehouse(
        {
            "latest_state_update": [state_update(NOW)],
            "energy_between": [{"discharged_watt_hours": 128.4, "charged_watt_hours": 40.2}],
            "finished_pulldowns_between": [pulldown_row(at("2026-09-11", 8, 12))],
            "snapshots": day_of_slots(NOW),
            "ambient_bands": ambient_band_rows(),
        }
    )


@pytest.fixture
def weekly_recorded(recorded: FakeWarehouse) -> FakeWarehouse:
    recorded.answers["snapshots"] = week_of_slots()
    return recorded


def test_the_evening_job_posts_one_summary(
    recorded: FakeWarehouse, slack: FakeSlack, settings: Settings
) -> None:
    posted = build_notifier(recorded, slack, settings).run_daily()

    assert [notification.kind for notification in posted] == [DAILY]
    assert len(slack.messages) == 1


def test_the_evening_job_says_nothing_before_any_state_update(
    recorded: FakeWarehouse, slack: FakeSlack, settings: Settings
) -> None:
    recorded.answers["latest_state_update"] = []

    assert build_notifier(recorded, slack, settings).run_daily() == []
    assert slack.messages == []


def test_the_saturday_job_posts_one_summary(
    weekly_recorded: FakeWarehouse, slack: FakeSlack, settings: Settings
) -> None:
    posted = build_notifier(weekly_recorded, slack, settings).run_weekly()

    assert [notification.kind for notification in posted] == [WEEKLY]
    assert len(slack.messages) == 1


def test_every_chart_a_notification_carries_is_one_slack_would_accept(
    weekly_recorded: FakeWarehouse, slack: FakeSlack, settings: Settings
) -> None:
    """Both summaries, drawn from the fakes, held against the reference.

    A block Slack refuses takes its whole message with it, and nothing else here
    talks to Slack, so this is where that is caught.
    """
    notifier = build_notifier(weekly_recorded, slack, settings)
    for notification in notifier.run_daily() + notifier.run_weekly():
        assert notification.text, "a message must stand on its own without its blocks"
        charted = [b for b in notification.blocks if b["type"] == "data_visualization"]
        assert charted, notification.kind
        assert len(charted) <= charts.MAX_CHARTS, notification.kind
        for block in charted:
            check_chart(block)


def test_a_failed_contract_test_is_posted_with_its_checks(
    recorded: FakeWarehouse, slack: FakeSlack, settings: Settings
) -> None:
    report = QualityReport.model_validate(
        {
            "event": "quality_report",
            "run_id": "run-9",
            "published_at": NOW,
            "contract_id": "frostlog-semantics",
            "passed": False,
            "failed_checks": ["hours_are_dense", "every_report_is_reflected"],
        }
    )

    (notification,) = build_notifier(recorded, slack, settings).handle(report)

    assert "hours_are_dense" in notification.text
    assert "every_report_is_reflected" in notification.text


def test_a_passing_contract_test_says_nothing(
    recorded: FakeWarehouse, slack: FakeSlack, settings: Settings
) -> None:
    report = QualityReport.model_validate(
        {
            "event": "quality_report",
            "run_id": "run-9",
            "published_at": NOW,
            "contract_id": "frostlog-semantics",
            "passed": True,
            "failed_checks": [],
        }
    )

    assert build_notifier(recorded, slack, settings).handle(report) == []
    assert slack.messages == []


def test_the_warehouse_being_rebuilt_is_not_news(
    recorded: FakeWarehouse, slack: FakeSlack, settings: Settings
) -> None:
    """The summaries go to the warehouse on a schedule; they do not wait to be told."""
    event = SemanticUpdated.model_validate(
        {
            "run_id": "run-17",
            "published_at": NOW,
            "rows_arrived": 412,
            "build_passed": True,
            "upload_runs": [],
        }
    )

    assert build_notifier(recorded, slack, settings).handle(event) == []
    assert slack.messages == []
    assert recorded.queried == [], "an event must not cost a query"


def test_without_a_token_the_summary_is_built_but_not_sent(
    recorded: FakeWarehouse, settings: Settings
) -> None:
    slack = FakeSlack(enabled=False)

    posted = build_notifier(recorded, slack, settings).run_daily()

    assert [notification.kind for notification in posted] == [DAILY]
    assert slack.messages == []
    assert len(slack.dry_runs) == 1

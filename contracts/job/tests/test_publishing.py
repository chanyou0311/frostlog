"""What happens to an event when there is nowhere to publish it."""

import logging
from datetime import UTC, datetime

import pytest

from frostlog_contracts.events import QualityReport
from frostlog_contracts.publishing import NoPublisher, publisher_for


def test_without_a_topic_the_event_is_logged_rather_than_dropped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Local runs and tests have no topic. Raising there would make the job
    # untestable, and going silently nowhere would hide a missing topic in
    # production, so the event is said out loud instead.
    publisher = publisher_for("chanyou-frostlog", None)
    assert isinstance(publisher, NoPublisher)
    report = QualityReport(
        run_id="r1",
        published_at=datetime.now(UTC),
        contract_id="frostlog-collection",
        passed=True,
        failed_checks=[],
    )
    with caplog.at_level(logging.INFO):
        publisher.publish(report)
    assert "quality_report" in caplog.text

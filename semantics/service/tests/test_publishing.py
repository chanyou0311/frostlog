"""What happens to an event when there is nowhere to publish it."""

import logging
from datetime import UTC, datetime

import pytest

from frostlog_semantics.events import SemanticUpdated
from frostlog_semantics.publishing import NoPublisher, publisher_for


def test_without_a_topic_the_event_is_logged_rather_than_dropped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Local runs and tests have no topic. Raising there would make the service
    # untestable, and going silently nowhere would hide a missing topic in
    # production, so the event is said out loud instead. The contract-test job has
    # its own copy of this and has to decide it the same way.
    publisher = publisher_for("chanyou-frostlog", None)
    assert isinstance(publisher, NoPublisher)
    updated = SemanticUpdated(
        run_id="r1", published_at=datetime.now(UTC), rows_arrived=3, build_passed=True
    )
    with caplog.at_level(logging.INFO):
        publisher.publish(updated)
    assert "semantic_updated" in caplog.text

from conftest import FakeWarehouse, at

from frostlog_notifier.state import PostedNotifications


def test_the_table_is_created_if_it_is_missing(warehouse: FakeWarehouse) -> None:
    PostedNotifications(warehouse, "notifier_posted").ensure_table()
    assert warehouse.executed[0][0] == "create_posted_table"


def test_a_notification_is_posted_once(
    warehouse: FakeWarehouse, posted: PostedNotifications
) -> None:
    assert posted.is_posted("weekly", "2026-W37") is False
    posted.record("weekly", "2026-W37", "1770000000.000100", dry_run=False)
    assert posted.is_posted("weekly", "2026-W37") is True
    assert posted.is_posted("weekly", "2026-W38") is False
    assert posted.is_posted("homecoming", "2026-W37") is False


def test_a_dry_run_is_recorded_without_a_slack_timestamp(
    warehouse: FakeWarehouse, posted: PostedNotifications
) -> None:
    posted.record("homecoming", "2026-09-11T12:03:00+00:00", None, dry_run=True)
    name, parameters = warehouse.executed[-1]
    assert name == "record_posted"
    assert "slack_ts" not in parameters
    assert "coverage_end" not in parameters
    assert parameters["dry_run"] is True


def test_the_coverage_end_of_a_kind_is_the_furthest_one_written(
    posted: PostedNotifications,
) -> None:
    first, second = at("2026-09-10", 11), at("2026-09-11", 12)
    assert posted.latest_coverage_end("homecoming") is None
    posted.record("homecoming", "first", None, dry_run=True, coverage_end=first)
    posted.record("pulldown", "other", None, dry_run=True)
    posted.record("homecoming", "second", None, dry_run=True, coverage_end=second)
    assert posted.latest_coverage_end("homecoming") == second
    assert posted.latest_coverage_end("pulldown") is None

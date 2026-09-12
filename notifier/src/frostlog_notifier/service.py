"""What the endpoints do: decide, post once, remember.

Every notification passes through :meth:`Notifier.post`, which is the single
place the state table is consulted and written, so redelivery of a Pub/Sub
message can never produce a second message.
"""

import logging
from collections.abc import Callable
from datetime import datetime

from frostlog_notifier import clock, homecoming, pulldowns, quality, queries, weekly
from frostlog_notifier.events import Event, QualityReport, SemanticUpdated
from frostlog_notifier.notification import FAILURE, HOMECOMING, WEEKLY, Notification
from frostlog_notifier.settings import Settings
from frostlog_notifier.slack import Posted, Poster, Slack
from frostlog_notifier.state import PostedNotifications
from frostlog_notifier.warehouse import Warehouse

log = logging.getLogger(__name__)

#: How many closed weeks back a run looks for summaries it still owes. Data reaches
#: the warehouse only when the car comes home, which can be weeks after the fact.
WEEKLY_BACKLOG_WEEKS = 8


class Notifier:
    def __init__(
        self,
        warehouse: Warehouse,
        posted: PostedNotifications,
        slack: Poster,
        now: Callable[[], datetime] = clock.now,
    ) -> None:
        self._warehouse = warehouse
        self._posted = posted
        self._slack = slack
        self._now = now

    def handle(self, event: Event) -> list[Notification]:
        """React to one event of the semantic data contract."""
        if isinstance(event, QualityReport):
            return self._handle_quality_report(event)
        return self._handle_semantic_updated(event)

    def _handle_quality_report(self, report: QualityReport) -> list[Notification]:
        notification = quality.build(report)
        if notification is None:
            log.info("contract test passed for %s; nothing to say", report.contract_id)
            return []
        self._posted.ensure_table()
        return self._post_all([notification])

    def _handle_semantic_updated(self, event: SemanticUpdated) -> list[Notification]:
        if not event.build_passed:
            log.warning("run %s did not build; no data changed", event.run_id)
            return []
        self._posted.ensure_table()
        candidates = homecoming.build_all(
            self._warehouse,
            event.upload_runs,
            self._posted.latest_coverage_end(HOMECOMING),
        )
        candidates.extend(
            pulldowns.build_all(self._warehouse, self._posted.posted_keys, self._now())
        )
        candidates.extend(self._weekly_if_closed())
        return self._post_all(candidates)

    def _weekly_if_closed(self) -> list[Notification]:
        """The weekly summaries whose weeks are over and whose data has arrived."""
        latest = queries.latest_state_update(self._warehouse)
        if latest is None:
            return []
        return self._weekly_backlog(*weekly.due_week(latest.updated_at))

    def run_weekly_deadline(self) -> list[Notification]:
        """The Monday job: post the summaries the arrivals did not already."""
        self._posted.ensure_table()
        return self._post_all(self._weekly_backlog(*weekly.due_week(self._now())))

    def _weekly_backlog(self, iso_year: int, iso_week: int) -> list[Notification]:
        """The due week and the unposted weeks before it, oldest first.

        A trip of several weeks brings several closed weeks home at once, and a
        week's data can arrive after a later week was already posted; so every one
        of the last WEEKLY_BACKLOG_WEEKS closed weeks that has data and no summary
        yet gets its own message.
        """
        pending: list[Notification] = []
        year, week = iso_year, iso_week
        for _ in range(WEEKLY_BACKLOG_WEEKS):
            pending.extend(self._weekly(year, week))
            year, week = clock.previous_iso_week(clock.iso_week_bounds(year, week)[0])
        pending.reverse()
        return pending

    def _weekly(self, iso_year: int, iso_week: int) -> list[Notification]:
        key = clock.iso_week_key(iso_year, iso_week)
        if self._posted.is_posted(WEEKLY, key):
            return []
        start, end = clock.iso_week_bounds(iso_year, iso_week)
        if queries.state_update_count_between(self._warehouse, start, end) == 0:
            # A week the cooler recorded nothing in has nothing to say, not zeros to report.
            log.info("no state update in %s; no weekly summary", key)
            return []
        return [weekly.build(self._warehouse, iso_year, iso_week)]

    def _post_all(self, notifications: list[Notification]) -> list[Notification]:
        return [
            notification for notification in notifications if self.post(notification) is not None
        ]

    def post(self, notification: Notification) -> Posted | None:
        """Post a notification unless it has been posted before.

        The check and the insert are not one statement; they do not need to be,
        because Cloud Run runs this service as a single instance handling one
        request at a time (max_instance_count = 1, request concurrency = 1), so
        no other handler can slip a row in between them.
        """
        if self._posted.is_posted(notification.kind, notification.key):
            log.info("%s/%s already posted", notification.kind, notification.key)
            return None
        result = self._slack.post(notification.text, notification.blocks)
        self._posted.record(
            notification.kind,
            notification.key,
            result.slack_timestamp,
            result.dry_run,
            notification.coverage_end,
        )
        return result

    def report_failure(self, context: str, error: BaseException) -> None:
        """Say in the channel that the notifier itself failed; never raise while doing so."""
        log.exception("notifier failed while handling %s", context, exc_info=error)
        # A defect repeats with every redelivery: one message a day for the same defect.
        key = f"{context}/{type(error).__name__}/{clock.to_jst(self._now()):%Y-%m-%d}"
        text = f":warning: notifier の失敗 ({context}): {type(error).__name__}: {error}"
        try:
            self._posted.ensure_table()
            self.post(Notification(kind=FAILURE, key=key, text=text))
        except Exception:  # a failed self-report must never mask the failure it reports
            log.exception("could not report the failure to Slack")


def build(settings: Settings | None = None) -> Notifier:
    """A notifier wired to the real BigQuery dataset and Slack workspace."""
    from frostlog_notifier import secret_manager
    from frostlog_notifier.settings import resolve_project
    from frostlog_notifier.warehouse import BigQueryWarehouse

    configuration = settings or Settings()
    project = resolve_project(configuration)
    secret = configuration.slack_bot_token_secret
    warehouse = BigQueryWarehouse(project, configuration.bigquery_dataset)
    # The token is looked up on first use: the secret may still be empty at deployment.
    token = configuration.slack_bot_token or (
        lambda: secret_manager.slack_bot_token(project, secret)
    )
    return Notifier(
        warehouse=warehouse,
        posted=PostedNotifications(warehouse),
        slack=Slack(token, configuration.slack_channel),
    )

"""What the endpoints do: read the warehouse, say one thing.

Nothing is remembered between runs, and nothing is written anywhere. Each summary
is a picture of a window ending at the moment its job fires, so two runs of the
same job never produce the same message twice -- not because a second one was
suppressed, but because there is no second one to suppress.

That is what the schedules buy. An event saying "the warehouse changed" arrives
every hour and carries the same facts over and over, so acting on it means
remembering what was already said; a job that fires once says its piece once.
"""

import logging
from collections.abc import Callable
from datetime import datetime

from frostlog_notifier import clock, daily, quality, weekly
from frostlog_notifier.events import Event, QualityReport
from frostlog_notifier.notification import FAILURE, Notification
from frostlog_notifier.settings import Settings
from frostlog_notifier.slack import Posted, Poster, Slack
from frostlog_notifier.warehouse import Warehouse

log = logging.getLogger(__name__)


class Notifier:
    def __init__(
        self,
        warehouse: Warehouse,
        slack: Poster,
        now: Callable[[], datetime] = clock.now,
    ) -> None:
        self._warehouse = warehouse
        self._slack = slack
        self._now = now

    def handle(self, event: Event) -> list[Notification]:
        """React to one event of the signals contract.

        Only a failed contract test is worth interrupting someone for, and that test
        runs once a day, so the event arrives once. `semantic_updated` says the
        warehouse was rebuilt, which the schedules will see for themselves.
        """
        if not isinstance(event, QualityReport):
            log.info("nothing to say about this event; the summaries read the warehouse")
            return []
        notification = quality.build(event)
        if notification is None:
            log.info("contract test passed for %s; nothing to say", event.contract_id)
            return []
        return self._post_all([notification])

    def run_daily(self) -> list[Notification]:
        """The evening job: where the battery stands, and where it is going."""
        notification = daily.build(self._warehouse, self._now())
        return self._post_all([notification] if notification else [])

    def run_weekly(self) -> list[Notification]:
        """The Saturday job: the seven days behind it."""
        return self._post_all([weekly.build(self._warehouse, self._now())])

    def _post_all(self, notifications: list[Notification]) -> list[Notification]:
        return [
            notification for notification in notifications if self.post(notification) is not None
        ]

    def post(self, notification: Notification) -> Posted | None:
        result = self._slack.post(notification.text, notification.blocks)
        if result.dry_run:
            log.info("%s was a dry run; nothing reached the channel", notification.kind)
        return result

    def report_failure(self, context: str, error: BaseException) -> None:
        """Say in the channel that the notifier itself failed; never raise while doing so."""
        log.exception("notifier failed while handling %s", context, exc_info=error)
        text = f":warning: notifier の失敗 ({context}): {type(error).__name__}: {error}"
        try:
            self.post(Notification(kind=FAILURE, text=text))
        except Exception:  # a failed self-report must never mask the failure it reports
            log.exception("could not report the failure to Slack")


def build(settings: Settings | None = None) -> Notifier:
    """The notifier this deployment runs, from the environment."""
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
    return Notifier(warehouse=warehouse, slack=Slack(token, configuration.slack_channel))

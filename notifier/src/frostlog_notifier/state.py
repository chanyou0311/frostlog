"""What has already been posted.

One row per notification, keyed by its kind and its idempotency key (the upload
run for a homecoming summary, the pull-down key for a pull-down, the ISO week
for a weekly summary, the run id for a quality report, the defect and the day
for a failure report). Every notification is looked up here before it is posted
and recorded here after, so a redelivered Pub/Sub message produces nothing.

A summary that continues where the last one stopped also writes its coverage
end here, so the next one starts there rather than at its own idempotency key.
"""

import logging
from datetime import datetime

from frostlog_notifier.clock import now
from frostlog_notifier.warehouse import Warehouse

log = logging.getLogger(__name__)


class PostedNotifications:
    def __init__(self, warehouse: Warehouse, table: str = "notifier_posted") -> None:
        self._warehouse = warehouse
        self._table = warehouse.table(table)

    def ensure_table(self) -> None:
        self._warehouse.execute(f"""
          -- name: create_posted_table
          CREATE TABLE IF NOT EXISTS {self._table} (
            kind STRING NOT NULL,
            key STRING NOT NULL,
            posted_at TIMESTAMP NOT NULL,
            slack_ts STRING,
            dry_run BOOL NOT NULL,
            coverage_end TIMESTAMP
          )
        """)

    def is_posted(self, kind: str, key: str) -> bool:
        rows = self._warehouse.rows(
            f"""
              -- name: is_posted
              SELECT COUNT(*) AS posted_count FROM {self._table}
              WHERE kind = @kind AND key = @key
            """,
            {"kind": kind, "key": key},
        )
        return bool(rows) and int(rows[0]["posted_count"]) > 0

    def latest_coverage_end(self, kind: str) -> datetime | None:
        """How far the notifications of ``kind`` have covered so far."""
        rows = self._warehouse.rows(
            f"""
              -- name: latest_coverage_end
              SELECT coverage_end FROM {self._table}
              WHERE kind = @kind AND coverage_end IS NOT NULL
              ORDER BY coverage_end DESC
              LIMIT 1
            """,
            {"kind": kind},
        )
        return rows[0]["coverage_end"] if rows else None

    def record(
        self,
        kind: str,
        key: str,
        slack_timestamp: str | None,
        dry_run: bool,
        coverage_end: datetime | None = None,
    ) -> None:
        parameters: dict[str, object] = {
            "kind": kind,
            "key": key,
            "posted_at": now(),
            "dry_run": dry_run,
        }
        # A dry run has no Slack timestamp, and most notifications cover no period;
        # write the column's NULL rather than a placeholder value.
        if slack_timestamp is not None:
            parameters["slack_ts"] = slack_timestamp
        if coverage_end is not None:
            parameters["coverage_end"] = coverage_end
        timestamp = "@slack_ts" if slack_timestamp is not None else "NULL"
        coverage = "@coverage_end" if coverage_end is not None else "NULL"
        self._warehouse.execute(
            f"""
              -- name: record_posted
              INSERT INTO {self._table} (kind, key, posted_at, slack_ts, dry_run, coverage_end)
              VALUES (@kind, @key, @posted_at, {timestamp}, @dry_run, {coverage})
            """,
            parameters,
        )
        log.info("recorded %s/%s as posted (dry_run=%s)", kind, key, dry_run)

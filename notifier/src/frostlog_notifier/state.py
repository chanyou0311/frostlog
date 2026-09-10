"""What has already been posted.

One row per notification, keyed by its kind and its idempotency key (the period
end for a summary, the pull-down key for a pull-down, the ISO week for a weekly
summary, the run id for a quality report). Every notification is looked up here
before it is posted and recorded here after, so a redelivered Pub/Sub message
produces nothing.
"""

import logging

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
            dry_run BOOL NOT NULL
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

    def latest_key(self, kind: str) -> str | None:
        """The key of the most recently posted notification of ``kind``."""
        rows = self._warehouse.rows(
            f"""
              -- name: latest_key
              SELECT key FROM {self._table}
              WHERE kind = @kind
              ORDER BY posted_at DESC
              LIMIT 1
            """,
            {"kind": kind},
        )
        return str(rows[0]["key"]) if rows else None

    def record(self, kind: str, key: str, slack_timestamp: str | None, dry_run: bool) -> None:
        parameters: dict[str, object] = {
            "kind": kind,
            "key": key,
            "posted_at": now(),
            "dry_run": dry_run,
        }
        # A dry run has no Slack timestamp; write the column's NULL rather than an empty string.
        if slack_timestamp is not None:
            parameters["slack_ts"] = slack_timestamp
        timestamp = "@slack_ts" if slack_timestamp else "NULL"
        self._warehouse.execute(
            f"""
              -- name: record_posted
              INSERT INTO {self._table} (kind, key, posted_at, slack_ts, dry_run)
              VALUES (@kind, @key, @posted_at, {timestamp}, @dry_run)
            """,
            parameters,
        )
        log.info("recorded %s/%s as posted (dry_run=%s)", kind, key, dry_run)

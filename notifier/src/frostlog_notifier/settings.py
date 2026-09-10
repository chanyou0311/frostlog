"""Machine-specific configuration read from FROSTLOG_* environment variables.

The Slack token is optional on purpose: without it the service runs in dry run,
which is how it is deployed before the token exists.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FROSTLOG_", env_ignore_empty=True)

    bigquery_project: str = "frostlog-chanyou"
    #: Dataset holding the tables of the semantic data contract.
    bigquery_dataset: str = "frostlog"
    #: Table of the collector's own events (raw stream `events`), loaded by the transform.
    raw_events_table: str = "raw_events"
    #: Table this service keeps its posted-notification state in.
    posted_table: str = "notifier_posted"

    slack_bot_token: str | None = None
    slack_channel: str = "#fumo"
    #: Where charts are written when there is no Slack token.
    dry_run_directory: Path = Path("/tmp")

"""Machine-specific configuration read from FROSTLOG_* environment variables.

The Slack token is optional on purpose: without it the service runs in dry run,
which is how it is deployed before the token exists.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FROSTLOG_",
        env_ignore_empty=True,
        validate_by_name=True,
        validate_by_alias=True,
    )

    #: Project to read and write in; unset in Cloud Run, where the credentials name it.
    gcp_project: str | None = None
    #: Dataset holding the tables of the semantic data contract.
    bigquery_dataset: str = Field(default="frostlog", validation_alias="FROSTLOG_BQ_DATASET")

    #: The token itself; for local runs, where there is no Secret Manager.
    slack_bot_token: str | None = None
    #: Name of the Secret Manager secret holding the token (the name, never the token).
    slack_bot_token_secret: str | None = None
    slack_channel: str = "#fumo"


def resolve_project(settings: Settings) -> str:
    """The project to work in: the override if given, else the credentials' own."""
    if settings.gcp_project:
        return settings.gcp_project
    import google.auth

    _, project = google.auth.default()
    if not project:
        raise RuntimeError(
            "the Application Default Credentials name no project; set FROSTLOG_GCP_PROJECT"
        )
    return str(project)

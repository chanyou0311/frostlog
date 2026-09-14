import pytest

from frostlog_notifier.settings import Settings, resolve_project


def test_the_environment_names_the_dataset_and_the_token_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("FROSTLOG_GCP_PROJECT", "FROSTLOG_SLACK_BOT_TOKEN", "FROSTLOG_SLACK_CHANNEL"):
        monkeypatch.delenv(name, raising=False)
    # The names Terraform sets on the Cloud Run service.
    monkeypatch.setenv("FROSTLOG_BQ_DATASET", "frostlog")
    monkeypatch.setenv("FROSTLOG_SLACK_BOT_TOKEN_SECRET", "frostlog-slack-bot-token")
    settings = Settings()
    assert settings.bigquery_dataset == "frostlog"
    assert settings.slack_bot_token_secret == "frostlog-slack-bot-token"
    assert settings.slack_bot_token is None
    assert settings.slack_channel == "#talk"
    assert settings.gcp_project is None


def test_the_project_comes_from_the_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FROSTLOG_GCP_PROJECT", raising=False)
    monkeypatch.setattr("google.auth.default", lambda: (None, "frostlog-from-credentials"))
    assert resolve_project(Settings()) == "frostlog-from-credentials"


def test_the_project_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FROSTLOG_GCP_PROJECT", "frostlog-elsewhere")
    assert resolve_project(Settings()) == "frostlog-elsewhere"


def test_credentials_without_a_project_are_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FROSTLOG_GCP_PROJECT", raising=False)
    monkeypatch.setattr("google.auth.default", lambda: (None, None))
    with pytest.raises(RuntimeError, match="FROSTLOG_GCP_PROJECT"):
        resolve_project(Settings())

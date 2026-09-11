from typing import Any

import pytest
from google.api_core import exceptions

from frostlog_notifier.errors import Transient
from frostlog_notifier.secret_manager import slack_bot_token

TOKEN = "xoxb-0000000000-secret"


class FakeSecretClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.names: list[str] = []

    def access_secret_version(self, name: str) -> Any:
        self.names.append(name)
        if self.error is not None:
            raise self.error
        payload = type("Payload", (), {"data": TOKEN.encode()})()
        return type("Version", (), {"payload": payload})()


def test_the_token_is_read_from_the_latest_version() -> None:
    client = FakeSecretClient()
    assert slack_bot_token("frostlog-test", "frostlog-slack-bot-token", client) == TOKEN
    assert client.names == [
        "projects/frostlog-test/secrets/frostlog-slack-bot-token/versions/latest"
    ]


def test_no_secret_name_means_a_dry_run() -> None:
    assert slack_bot_token("frostlog-test", None) is None


@pytest.mark.parametrize(
    "error",
    [
        exceptions.NotFound("no such secret"),
        exceptions.FailedPrecondition("the secret has no version yet"),
        exceptions.PermissionDenied("not readable"),
    ],
)
def test_a_secret_that_cannot_be_read_means_a_dry_run(error: Exception) -> None:
    client = FakeSecretClient(error=error)
    assert slack_bot_token("frostlog-test", "frostlog-slack-bot-token", client) is None


def test_secret_manager_being_unavailable_is_transient() -> None:
    client = FakeSecretClient(error=exceptions.ServiceUnavailable("try again"))
    with pytest.raises(Transient):
        slack_bot_token("frostlog-test", "frostlog-slack-bot-token", client)


def test_the_token_is_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    slack_bot_token("frostlog-test", "frostlog-slack-bot-token", FakeSecretClient())
    slack_bot_token("frostlog-test", None)
    slack_bot_token(
        "frostlog-test",
        "frostlog-slack-bot-token",
        FakeSecretClient(error=exceptions.NotFound("gone")),
    )
    assert TOKEN not in caplog.text

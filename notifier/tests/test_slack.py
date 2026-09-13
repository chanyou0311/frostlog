from typing import Any

import pytest

from frostlog_notifier.errors import Transient
from frostlog_notifier.slack import Posted, Slack

CHART = {
    "type": "data_visualization",
    "title": "バッテリー残量 (%)",
    "chart": {
        "type": "line",
        "series": [{"name": "残量", "data": [{"label": "8時", "value": 62}]}],
        "axis_config": {"categories": ["8時"]},
    },
}
BLOCKS = [{"type": "section", "text": {"type": "mrkdwn", "text": "帰宅の要約"}}, CHART]


class SlackError(Exception):
    """A slack_sdk error: an exception carrying the answered response."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.response = type("Response", (), {"status_code": status_code})()


class FakeWebClient:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response or {"ok": True, "ts": "1770000000.000100"}
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def chat_postMessage(self, **kwargs: Any) -> Any:
        return self._answer("chat_postMessage", kwargs)

    def files_upload_v2(self, **kwargs: Any) -> Any:
        return self._answer("files_upload_v2", kwargs)

    def _answer(self, method: str, kwargs: dict[str, Any]) -> Any:
        self.calls.append((method, kwargs))
        if self.error is not None:
            raise self.error
        return self.response


def test_without_a_token_the_message_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    result = Slack(token=None, channel="#fumo").post("帰宅の要約", BLOCKS)
    assert result.dry_run is True
    assert result.slack_timestamp is None
    assert "帰宅の要約" in caplog.text
    # The blocks go to the log too: without a token that is the only way to see them.
    assert "data_visualization" in caplog.text


def test_the_blocks_go_with_the_text_that_stands_in_for_them() -> None:
    client = FakeWebClient()
    slack = Slack(token="xoxb-test", channel="#fumo", client=client)
    result = slack.post("到達", BLOCKS)
    method, kwargs = client.calls[0]
    assert method == "chat_postMessage"
    assert kwargs == {"channel": "#fumo", "text": "到達", "blocks": BLOCKS}
    assert result == Posted(slack_timestamp="1770000000.000100", dry_run=False)


def test_a_message_without_blocks_is_plain_text() -> None:
    client = FakeWebClient()
    Slack(token="xoxb-test", channel="#fumo", client=client).post("品質チェック失敗")
    method, kwargs = client.calls[0]
    assert method == "chat_postMessage"
    assert kwargs == {"channel": "#fumo", "text": "品質チェック失敗", "blocks": None}


def test_rate_limiting_is_transient() -> None:
    error = SlackError("ratelimited", 429)
    slack = Slack(token="xoxb-test", channel="#fumo", client=FakeWebClient(error=error))
    with pytest.raises(Transient):
        slack.post("x")


def test_a_rejected_message_is_not_transient() -> None:
    error = SlackError("channel_not_found", 404)
    slack = Slack(token="xoxb-test", channel="#fumo", client=FakeWebClient(error=error))
    with pytest.raises(Exception, match="channel_not_found") as raised:
        slack.post("x")
    assert not isinstance(raised.value, Transient)


def test_the_token_is_looked_up_once_and_only_when_it_is_needed() -> None:
    lookups: list[str] = []

    def source() -> str | None:
        lookups.append("looked up")
        return None

    slack = Slack(token=source, channel="#fumo")
    assert lookups == []  # building the poster reaches nothing
    assert slack.post("one").dry_run is True
    assert slack.post("two").dry_run is True
    assert len(lookups) == 1


def test_a_token_that_arrives_later_is_used() -> None:
    client = FakeWebClient()
    slack = Slack(token=lambda: "xoxb-from-the-secret", channel="#fumo", client=client)
    assert slack.token == "xoxb-from-the-secret"
    assert slack.enabled is True

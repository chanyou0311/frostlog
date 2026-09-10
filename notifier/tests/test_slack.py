from pathlib import Path
from typing import Any

import pytest

from frostlog_notifier.errors import Transient
from frostlog_notifier.slack import Posted, Slack

PNG = b"\x89PNG\r\n\x1a\nfake"


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


def test_without_a_token_the_text_is_logged_and_the_chart_written(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    slack = Slack(token=None, channel="#fumo", dry_run_directory=tmp_path)
    result = slack.post("帰宅の要約", PNG, "homecoming.png")
    assert result.dry_run is True
    assert result.slack_timestamp is None
    assert (tmp_path / "homecoming.png").read_bytes() == PNG
    assert "帰宅の要約" in caplog.text


def test_a_chart_is_uploaded_with_the_text_as_its_comment() -> None:
    client = FakeWebClient()
    slack = Slack(token="xoxb-test", channel="#fumo", client=client)
    result = slack.post("到達", PNG, "pulldown.png")
    method, kwargs = client.calls[0]
    assert method == "files_upload_v2"
    assert kwargs == {
        "channel": "#fumo",
        "file": PNG,
        "filename": "pulldown.png",
        "initial_comment": "到達",
    }
    assert result == Posted(slack_timestamp="1770000000.000100", dry_run=False)


def test_a_message_without_a_chart_is_plain_text() -> None:
    client = FakeWebClient()
    Slack(token="xoxb-test", channel="#fumo", client=client).post("品質チェック失敗")
    method, kwargs = client.calls[0]
    assert method == "chat_postMessage"
    assert kwargs == {"channel": "#fumo", "text": "品質チェック失敗"}


def test_the_timestamp_of_an_upload_is_found_in_its_shares() -> None:
    response = {
        "ok": True,
        "files": [
            {"id": "F1", "shares": {"public": {"C1": [{"ts": "1770000123.000200"}]}}},
        ],
    }
    client = FakeWebClient(response=response)
    result = Slack(token="xoxb-test", channel="#fumo", client=client).post("x", PNG)
    assert result.slack_timestamp == "1770000123.000200"


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


def test_the_token_is_looked_up_once_and_only_when_it_is_needed(tmp_path: Path) -> None:
    lookups: list[str] = []

    def source() -> str | None:
        lookups.append("looked up")
        return None

    slack = Slack(token=source, channel="#fumo", dry_run_directory=tmp_path)
    assert lookups == []  # building the poster reaches nothing
    assert slack.post("one").dry_run is True
    assert slack.post("two").dry_run is True
    assert len(lookups) == 1


def test_a_token_that_arrives_later_is_used() -> None:
    client = FakeWebClient()
    slack = Slack(token=lambda: "xoxb-from-the-secret", channel="#fumo", client=client)
    assert slack.token == "xoxb-from-the-secret"
    assert slack.enabled is True

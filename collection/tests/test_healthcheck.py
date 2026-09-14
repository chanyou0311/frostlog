import contextlib
import urllib.error
import urllib.request

import pytest

from frostlog.upload import healthcheck


def test_ping_gets_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda url, timeout: calls.append(url) or contextlib.nullcontext(),
    )
    healthcheck.ping("https://hc-ping.com/x")
    assert calls == ["https://hc-ping.com/x"]


def test_ping_failure_is_only_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def urlopen(url: str, timeout: float) -> None:
        raise urllib.error.URLError("down")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    healthcheck.ping("https://hc-ping.com/x")
    assert "ping failed" in caplog.text


def test_ping_with_a_bad_url_is_only_logged(caplog: pytest.LogCaptureFixture) -> None:
    healthcheck.ping("hc-ping.com/x")  # no scheme: urlopen raises before any request
    assert "ping failed" in caplog.text

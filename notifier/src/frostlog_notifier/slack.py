"""Posting to Slack, or to the log when there is no token.

The bot token arrives after the first deployment, so the service must be useful
without it: a dry run logs the message it would have sent and is not recorded, so
the same notification is posted for real once a token is there.
"""

import functools
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from frostlog_notifier.errors import Transient

log = logging.getLogger(__name__)

#: Slack answers these with a retry: rate limit and server errors.
_TRANSIENT_STATUS = frozenset({408, 429, 500, 502, 503, 504})

#: A token, looked up on first use because the lookup may reach Secret Manager.
TokenSource = Callable[[], str | None]


@dataclass(frozen=True)
class Posted:
    slack_timestamp: str | None
    dry_run: bool


class Poster(Protocol):
    """What the notifier needs of Slack; the tests substitute their own."""

    def post(self, text: str, blocks: list[dict[str, Any]] | None = None) -> Posted: ...


class Slack:
    def __init__(
        self,
        token: str | TokenSource | None,
        channel: str,
        client: Any = None,
    ) -> None:
        self._token_source: TokenSource
        if token is None or isinstance(token, str):
            self._token_source = lambda: token
        else:
            self._token_source = token
        self._channel = channel
        self._client = client

    @functools.cached_property
    def token(self) -> str | None:
        """The bot token, looked up once and kept for the life of the instance.

        Cached even when it is None: a secret that is not there is an answer, and
        asking Secret Manager again on every notification would not change it.
        """
        return self._token_source()

    @property
    def enabled(self) -> bool:
        return self._client is not None or bool(self.token)

    @property
    def client(self) -> Any:
        if self._client is None:
            from slack_sdk import WebClient
            from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler

            self._client = WebClient(token=self.token)
            # The SDK ships only a connection-error handler by default. Slack allows
            # about one message a second per channel, so a run that posts more than
            # one can get a 429 on the second. Without this the whole request fails
            # and the caller retries it, which repeats every query behind it.
            self._client.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=2))
        return self._client

    def post(self, text: str, blocks: list[dict[str, Any]] | None = None) -> Posted:
        """Post the message. ``text`` travels even with blocks: it is what a
        notification, a screen reader and a search result show."""
        if not self.enabled:
            return self._dry_run(text, blocks)
        try:
            response = self.client.chat_postMessage(
                channel=self._channel, text=text, blocks=blocks or None
            )
        except Exception as exc:  # classified, then re-raised
            raise self._classify(exc) from exc
        return Posted(slack_timestamp=_timestamp_of(response), dry_run=False)

    def _dry_run(self, text: str, blocks: list[dict[str, Any]] | None) -> Posted:
        log.info("slack dry run for %s:\n%s", self._channel, text)
        if blocks:
            log.info("slack dry run blocks:\n%s", json.dumps(blocks, ensure_ascii=False, indent=2))
        return Posted(slack_timestamp=None, dry_run=True)

    @staticmethod
    def _classify(exc: Exception) -> Exception:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in _TRANSIENT_STATUS or isinstance(exc, OSError):
            return Transient(f"Slack is unavailable: {exc}")
        return exc


def _timestamp_of(response: Any) -> str | None:
    """The message timestamp Slack answered with."""
    if response is None:
        return None
    data = response.data if hasattr(response, "data") else response
    if not isinstance(data, dict):
        return None
    timestamp = data.get("ts")
    return str(timestamp) if timestamp else None

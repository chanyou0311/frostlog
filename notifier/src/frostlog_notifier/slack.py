"""Posting to Slack, or to the log and /tmp when there is no token.

The bot token arrives after the first deployment, so the service must be useful
without it: a dry run writes the chart next to the log line and is recorded as
posted all the same, which keeps the idempotency rules exercised.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
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

    def post(
        self, text: str, image: bytes | None = None, filename: str = "chart.png"
    ) -> Posted: ...


class Slack:
    def __init__(
        self,
        token: str | TokenSource | None,
        channel: str,
        dry_run_directory: Path = Path("/tmp"),
        client: Any = None,
    ) -> None:
        self._token_source: TokenSource
        if token is None or isinstance(token, str):
            self._token_source = lambda: token
        else:
            self._token_source = token
        self._token: str | None = None
        self._looked_up = False
        self._channel = channel
        self._dry_run_directory = dry_run_directory
        self._client = client

    @property
    def token(self) -> str | None:
        """The bot token, looked up once and kept for the life of the instance."""
        if not self._looked_up:
            self._token = self._token_source()
            self._looked_up = True
        return self._token

    @property
    def enabled(self) -> bool:
        return self._client is not None or bool(self.token)

    @property
    def client(self) -> Any:
        if self._client is None:
            from slack_sdk import WebClient

            self._client = WebClient(token=self.token)
        return self._client

    def post(self, text: str, image: bytes | None = None, filename: str = "chart.png") -> Posted:
        """Post ``text``, with ``image`` attached when there is one."""
        if not self.enabled:
            return self._dry_run(text, image, filename)
        try:
            if image is None:
                response = self.client.chat_postMessage(channel=self._channel, text=text)
            else:
                response = self.client.files_upload_v2(
                    channel=self._channel, file=image, filename=filename, initial_comment=text
                )
        except Exception as exc:  # classified, then re-raised
            raise self._classify(exc) from exc
        return Posted(slack_timestamp=_timestamp_of(response), dry_run=False)

    def _dry_run(self, text: str, image: bytes | None, filename: str) -> Posted:
        log.info("slack dry run for %s:\n%s", self._channel, text)
        if image is not None:
            path = self._dry_run_directory / filename
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(image)
                log.info("slack dry run wrote %s (%d bytes)", path, len(image))
            except OSError as exc:
                log.warning("slack dry run could not write %s: %s", path, exc)
        return Posted(slack_timestamp=None, dry_run=True)

    @staticmethod
    def _classify(exc: Exception) -> Exception:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in _TRANSIENT_STATUS or isinstance(exc, OSError):
            return Transient(f"Slack is unavailable: {exc}")
        return exc


def _timestamp_of(response: Any) -> str | None:
    """The message timestamp, wherever the answered method put it."""
    if response is None:
        return None
    data = response.data if hasattr(response, "data") else response
    if not isinstance(data, dict):
        return None
    if timestamp := data.get("ts"):
        return str(timestamp)
    files = data.get("files") or []
    for file in files:
        for shares in (file.get("shares") or {}).values():
            for entries in shares.values():
                for entry in entries:
                    if timestamp := entry.get("ts"):
                        return str(timestamp)
    return None

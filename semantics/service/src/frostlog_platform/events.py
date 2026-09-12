"""Putting an event on the topic both components announce themselves on.

The topic and its messages are contracts/signals.odcs.yaml. What goes on it is
each component's own business — the transform's ``semantic_updated``, the
contract test's ``quality_report`` — so this module knows only that an event can
say its name and turn itself into JSON.
"""

import logging
from typing import Any, Protocol

log = logging.getLogger(__name__)


class Event(Protocol):
    @property
    def name(self) -> str: ...

    def model_dump_json(self) -> str: ...


class Publisher(Protocol):
    def publish(self, event: Event) -> None: ...


class PubSubPublisher:
    """:class:`Publisher` over google-cloud-pubsub."""

    def __init__(self, client: Any, topic: str) -> None:
        self._client = client
        self._topic = topic

    def publish(self, event: Event) -> None:
        body = event.model_dump_json().encode()
        self._client.publish(self._topic, body, event=event.name).result()
        log.info("published %s to %s", event.name, self._topic)


class NoPublisher:
    """Used when no topic is configured: the event is logged and goes nowhere."""

    def publish(self, event: Event) -> None:
        log.info("no topic configured; %s not published: %s", event.name, event.model_dump_json())

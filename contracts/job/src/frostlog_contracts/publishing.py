"""Putting an event on the topic this job announces its findings on.

The topic and its messages are contracts/signals.odcs.yaml. What goes on it is
each producer's own business -- this one publishes ``quality_report`` -- so this
module knows only that an event can say its name and turn itself into JSON.

The semantics transform has its own copy of this (semantics/service): the two
deploy separately and the contract, not a shared library, is what binds them. The
one thing both must decide the same way is what happens when no topic is
configured, or an event would be dropped in one and published in the other.
"""

import logging
from typing import Any, Protocol

from frostlog_contracts.project import topic_path

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


def publisher_for(project: str, topic: str | None) -> Publisher:
    """A publisher on ``topic``, or one that goes nowhere when there is no topic.

    Local runs and tests have no topic; production does.
    """
    if not topic:
        return NoPublisher()
    from google.cloud import pubsub_v1

    return PubSubPublisher(pubsub_v1.PublisherClient(), topic_path(project, topic))

"""Fakes for everything the transform service talks to, so it can be run whole."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

import pytest

from frostlog_semantics.app import Services
from frostlog_semantics.publishing import Event
from frostlog_semantics.settings import Settings
from frostlog_semantics.transform import BuildResult
from frostlog_semantics.warehouse import Arrivals

#: The collection product's bucket, as the settings carry it.
COLLECTION_BUCKET = "chanyou-frostlog-collection"


class FakeWarehouse:
    def __init__(self) -> None:
        self.arrived = Arrivals(rows=3, counts={"raw_cooler": 50, "raw_events": 5})
        self.asked_since: list[datetime] = []
        self.asked_built_through: list[dict[str, int]] = []

    def arrivals(self, built_through: Mapping[str, int]) -> Arrivals:
        self.asked_built_through.append(dict(built_through))
        return self.arrived


class FakeBuiltThrough:
    def __init__(self) -> None:
        self.mark: dict[str, int] = {}
        self.written: list[dict[str, int]] = []

    def read(self) -> dict[str, int]:
        return self.mark

    def write(self, counts: dict[str, int]) -> None:
        self.written.append(counts)
        self.mark = counts


class FakeTransform:
    def __init__(self) -> None:
        self.passed = True
        self.builds = 0

    def build(self) -> BuildResult:
        self.builds += 1
        return BuildResult(passed=self.passed, command=["dbt", "build"], output="")


class FakePublisher:
    def __init__(self) -> None:
        self.published: list[Event] = []
        self.fails = False

    def publish(self, event: Event) -> None:
        if self.fails:
            raise RuntimeError("pub/sub is unhappy")
        self.published.append(event)


@dataclass
class Fakes:
    """The transform service's wiring, plus a handle on each fake."""

    services: Services
    warehouse: FakeWarehouse
    built_through: FakeBuiltThrough
    transform: FakeTransform
    publisher: FakePublisher


@pytest.fixture
def fakes() -> Fakes:
    warehouse, built_through = FakeWarehouse(), FakeBuiltThrough()
    transform, publisher = FakeTransform(), FakePublisher()
    services = Services(
        settings=Settings(collection_bucket=COLLECTION_BUCKET, signals_topic="frostlog-signals"),
        warehouse=warehouse,
        built_through=built_through,
        transform=transform,
        publisher=publisher,
    )
    return Fakes(services, warehouse, built_through, transform, publisher)

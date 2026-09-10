"""Fakes for everything the service talks to, so the endpoints can be run whole."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from frostlog_semantic.app import Services
from frostlog_semantic.contracts import ContractTestResult
from frostlog_semantic.events import Event
from frostlog_semantic.raw_objects import RawObject
from frostlog_semantic.settings import Settings
from frostlog_semantic.transform import BuildResult
from frostlog_semantic.warehouse import LoadResult


class FakeWarehouse:
    def __init__(self) -> None:
        self.loaded: list[tuple[str, datetime]] = []
        self.already_loaded = False

    def load(self, chunk: RawObject, uploaded_at: datetime) -> LoadResult:
        self.loaded.append((chunk.name, uploaded_at))
        return LoadResult(table=chunk.table, rows=3, already_loaded=self.already_loaded)


class FakeMetadata:
    def __init__(self) -> None:
        self.uploaded: datetime | None = None

    def uploaded_at(self, chunk: RawObject) -> datetime | None:
        return self.uploaded


class FakeTransform:
    def __init__(self) -> None:
        self.passed = True
        self.builds: list[list[str]] = []

    def build(self, target_dates: Sequence[date]) -> BuildResult:
        self.builds.append([day.isoformat() for day in target_dates])
        return BuildResult(passed=self.passed, command=["dbt", "build"], output="")


class FakePublisher:
    def __init__(self) -> None:
        self.published: list[Event] = []

    def publish(self, event: Event) -> None:
        self.published.append(event)


class FakeTester:
    def __init__(self) -> None:
        self.results: dict[str, ContractTestResult] = {}
        self.tested: list[tuple[str, str]] = []

    def test(self, contract: Path, server: str, contract_id: str) -> ContractTestResult:
        self.tested.append((contract.name, server))
        return self.results.get(contract_id, ContractTestResult(contract_id, passed=True))


@dataclass
class Fakes:
    """The wiring plus a handle on each fake, so a test can look at what happened."""

    services: Services
    warehouse: FakeWarehouse
    metadata: FakeMetadata
    transform: FakeTransform
    publisher: FakePublisher
    tester: FakeTester
    pinged: list[str] = field(default_factory=list)


@pytest.fixture
def fakes() -> Fakes:
    warehouse, metadata = FakeWarehouse(), FakeMetadata()
    transform, publisher, tester = FakeTransform(), FakePublisher(), FakeTester()
    pinged: list[str] = []
    services = Services(
        settings=Settings(
            pubsub_topic="projects/frostlog-chanyou/topics/frostlog-semantic-updated",
            contract_test_healthcheck_url="https://hc.example/uuid",
        ),
        warehouse=warehouse,
        metadata=metadata,
        transform=transform,
        publisher=publisher,
        tester=tester,
        ping=pinged.append,
    )
    return Fakes(services, warehouse, metadata, transform, publisher, tester, pinged)


@pytest.fixture
def finalized() -> dict:
    """The body Eventarc sends for a finalized object."""
    return {
        "bucket": "frostlog-raw",
        "name": "v1/cooler/dt=2026-09-06/000000122880.jsonl.gz",
        "timeCreated": datetime(2026, 9, 6, 12, tzinfo=UTC).isoformat(),
    }

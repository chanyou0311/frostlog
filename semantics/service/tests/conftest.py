"""Fakes for everything the two components talk to, so each can be run whole."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pytest

from frostlog_contracts.__main__ import Services as ContractServices
from frostlog_contracts.settings import Settings as ContractSettings
from frostlog_contracts.tester import ContractTestResult
from frostlog_platform.events import Event
from frostlog_semantics.app import Services
from frostlog_semantics.settings import Settings
from frostlog_semantics.transform import BuildResult
from frostlog_semantics.warehouse import Arrivals

#: The collection product's bucket, as the settings carry it.
COLLECTION_BUCKET = "chanyou-frostlog-collection"
#: The HMAC secret the contract test reads before it can look in that bucket.
HMAC_SECRET = "frostlog-collection-hmac"


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


class FakeTester:
    def __init__(self) -> None:
        self.results: dict[str, ContractTestResult] = {}
        self.raises: dict[str, Exception] = {}
        self.tested: list[tuple[str, str]] = []
        self.environments: list[dict[str, str]] = []
        self.checks: list[str | None] = []

    def test(
        self,
        contract: Path,
        server: str,
        contract_id: str,
        environment: dict[str, str] | None = None,
        checks: str | None = None,
    ) -> ContractTestResult:
        self.tested.append((contract.name, server))
        self.checks.append(checks)
        self.environments.append(environment or {})
        if contract_id in self.raises:
            raise self.raises[contract_id]
        return self.results.get(contract_id, ContractTestResult(contract_id, passed=True))


class FakeSecrets:
    def __init__(self, payloads: dict[str, str] | None = None) -> None:
        self.payloads = payloads or {}
        self.read_names: list[str] = []

    def read(self, name: str) -> str | None:
        self.read_names.append(name)
        return self.payloads.get(name)


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


@dataclass
class ContractFakes:
    """The contract-test job's wiring, plus a handle on each fake."""

    services: ContractServices
    tester: FakeTester
    publisher: FakePublisher
    secrets: FakeSecrets
    pinged: list[str] = field(default_factory=list)


@pytest.fixture
def job() -> ContractFakes:
    tester, publisher = FakeTester(), FakePublisher()
    secrets = FakeSecrets({HMAC_SECRET: '{"access_id": "GOOG1", "secret": "s3cret"}'})
    pinged: list[str] = []
    services = ContractServices(
        settings=ContractSettings(
            signals_topic="frostlog-signals",
            contract_test_healthcheck_url="https://hc.example/uuid",
            collection_hmac_secret=HMAC_SECRET,
        ),
        tester=tester,
        publisher=publisher,
        secrets=secrets,
        ping=pinged.append,
    )
    return ContractFakes(services, tester, publisher, secrets, pinged)

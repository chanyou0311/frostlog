"""Fakes for everything the two components talk to, so each can be run whole."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from frostlog_contracts.__main__ import Services as ContractServices
from frostlog_contracts.settings import Settings as ContractSettings
from frostlog_contracts.tester import ContractTestResult
from frostlog_platform.events import Event
from frostlog_semantics.app import Services
from frostlog_semantics.events import UploadRun
from frostlog_semantics.raw_objects import RawObject
from frostlog_semantics.settings import Settings
from frostlog_semantics.transform import BuildResult
from frostlog_semantics.warehouse import LoadResult

#: The bucket the deployment watches; anything else is somebody else's event.
COLLECTION_BUCKET = "chanyou-frostlog-collection"
#: The HMAC secret the contract test reads before it can look in that bucket.
HMAC_SECRET = "frostlog-collection-hmac"


class FakeWarehouse:
    def __init__(self) -> None:
        self.loaded: list[tuple[str, datetime]] = []
        self.already_loaded = False
        self.runs: list[UploadRun] = []
        self.arrived: list[date] = [date(2026, 9, 6), date(2026, 9, 7)]
        self.asked_since: list[datetime] = []

    def load(self, chunk: RawObject, uploaded_at: datetime) -> LoadResult:
        self.loaded.append((chunk.name, uploaded_at))
        return LoadResult(table=chunk.table, rows=3, already_loaded=self.already_loaded)

    def arrived_dates(self, since: datetime) -> list[date]:
        self.asked_since.append(since)
        return self.arrived

    def upload_runs(self, since: datetime) -> list[UploadRun]:
        self.asked_since.append(since)
        return self.runs


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
    metadata: FakeMetadata
    transform: FakeTransform
    publisher: FakePublisher


@pytest.fixture
def fakes() -> Fakes:
    warehouse, metadata = FakeWarehouse(), FakeMetadata()
    transform, publisher = FakeTransform(), FakePublisher()
    services = Services(
        settings=Settings(collection_bucket=COLLECTION_BUCKET, signals_topic="frostlog-signals"),
        warehouse=warehouse,
        metadata=metadata,
        transform=transform,
        publisher=publisher,
    )
    return Fakes(services, warehouse, metadata, transform, publisher)


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


@pytest.fixture
def finalized() -> dict:
    """The body Eventarc sends for a finalized object."""
    return {
        "bucket": COLLECTION_BUCKET,
        "name": "v1/cooler/dt=2026-09-06/000000122880.jsonl.gz",
        "timeCreated": datetime(2026, 9, 6, 12, tzinfo=UTC).isoformat(),
    }

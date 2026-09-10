"""Fakes for everything the service talks to, so the endpoints can be run whole."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from frostlog_semantic.app import Services
from frostlog_semantic.contracts import ContractTestResult
from frostlog_semantic.events import Event, UploadRun
from frostlog_semantic.raw_objects import RawObject
from frostlog_semantic.settings import Settings
from frostlog_semantic.transform import BuildResult
from frostlog_semantic.warehouse import LoadResult

#: The bucket the deployment watches; anything else is somebody else's event.
RAW_BUCKET = "chanyou-frostlog-raw"


class FakeWarehouse:
    def __init__(self) -> None:
        self.loaded: list[tuple[str, datetime]] = []
        self.already_loaded = False
        self.runs: list[UploadRun] = []
        self.asked_for_runs: list[str] = []

    def load(self, chunk: RawObject, uploaded_at: datetime) -> LoadResult:
        self.loaded.append((chunk.name, uploaded_at))
        return LoadResult(table=chunk.table, rows=3, already_loaded=self.already_loaded)

    def upload_runs(self, chunk: RawObject) -> list[UploadRun]:
        self.asked_for_runs.append(chunk.name)
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

    def test(
        self,
        contract: Path,
        server: str,
        contract_id: str,
        environment: dict[str, str] | None = None,
    ) -> ContractTestResult:
        self.tested.append((contract.name, server))
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
    """The wiring plus a handle on each fake, so a test can look at what happened."""

    services: Services
    warehouse: FakeWarehouse
    metadata: FakeMetadata
    transform: FakeTransform
    publisher: FakePublisher
    tester: FakeTester
    secrets: FakeSecrets
    pinged: list[str] = field(default_factory=list)


@pytest.fixture
def fakes() -> Fakes:
    warehouse, metadata = FakeWarehouse(), FakeMetadata()
    transform, publisher, tester = FakeTransform(), FakePublisher(), FakeTester()
    secrets = FakeSecrets({"frostlog-raw-hmac": '{"access_id": "GOOG1", "secret": "s3cret"}'})
    pinged: list[str] = []
    services = Services(
        settings=Settings(
            raw_bucket=RAW_BUCKET,
            semantic_updated_topic="frostlog-semantic-updated",
            contract_test_healthcheck_url="https://hc.example/uuid",
            raw_hmac_secret="frostlog-raw-hmac",
        ),
        warehouse=warehouse,
        metadata=metadata,
        transform=transform,
        publisher=publisher,
        tester=tester,
        secrets=secrets,
        ping=pinged.append,
    )
    return Fakes(services, warehouse, metadata, transform, publisher, tester, secrets, pinged)


@pytest.fixture
def finalized() -> dict:
    """The body Eventarc sends for a finalized object."""
    return {
        "bucket": RAW_BUCKET,
        "name": "v1/cooler/dt=2026-09-06/000000122880.jsonl.gz",
        "timeCreated": datetime(2026, 9, 6, 12, tzinfo=UTC).isoformat(),
    }

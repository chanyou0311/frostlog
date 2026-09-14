"""Fakes for everything the contract-test job talks to, so it can be run whole."""

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from frostlog_contracts.__main__ import Services as ContractServices
from frostlog_contracts.publishing import Event
from frostlog_contracts.settings import Settings as ContractSettings
from frostlog_contracts.tester import ContractTestResult

#: The HMAC secret the contract test reads before it can look in the bucket.
HMAC_SECRET = "frostlog-collection-hmac"


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

"""The daily contract test: a Cloud Run job, started by Cloud Scheduler.

Both data contracts are tested against the data that is really there, each
result is published as a ``quality_report`` so the Slack application can say so,
and a run in which everything passed pings the dead man's switch.

It is a job rather than an endpoint of either service because it belongs to
neither data product: it is the check that both are keeping their word. It ships
in the semantics image all the same — the contracts and datacontract-cli are
already in there, and a second image would cost Artifact Registry storage that
the free tier does not have.

A contract that fails, and one that could not be tested at all, are findings:
they are reported, and they make the run exit non-zero so the execution itself
is marked failed. Nothing is retried — tomorrow's run is the retry.
"""

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from frostlog_contracts import healthcheck
from frostlog_contracts.events import QualityReport
from frostlog_contracts.secret_manager import SecretManagerReader, SecretReader
from frostlog_contracts.settings import Settings
from frostlog_contracts.tester import (
    ContractTester,
    ContractTestResult,
    DatacontractTester,
    s3_credentials,
)
from frostlog_platform.events import NoPublisher, Publisher, PubSubPublisher
from frostlog_platform.project import resolve_project, topic_path

log = logging.getLogger(__name__)


@dataclass
class Services:
    """Everything the run talks to. Tests pass fakes; production passes clients."""

    settings: Settings
    tester: ContractTester
    publisher: Publisher
    secrets: SecretReader
    ping: Callable[[str], None] = healthcheck.ping


def build_services(settings: Settings | None = None) -> Services:
    """The production wiring: Pub/Sub and Secret Manager through ADC."""
    from google.cloud import pubsub_v1, secretmanager

    settings = settings or Settings()
    project = resolve_project(settings.gcp_project)
    environment = {
        "FROSTLOG_BQ_PROJECT": project,
        "FROSTLOG_BQ_DATASET": settings.bq_dataset,
        "FROSTLOG_BQ_RAW_DATASET": settings.raw_dataset,
        "FROSTLOG_BQ_LOCATION": settings.bq_location,
    }
    topic = settings.events_topic
    publisher: Publisher = (
        PubSubPublisher(pubsub_v1.PublisherClient(), topic_path(project, topic))
        if topic
        else NoPublisher()
    )
    return Services(
        settings=settings,
        tester=DatacontractTester(environment),
        publisher=publisher,
        secrets=SecretManagerReader(secretmanager.SecretManagerServiceClient(), project),
    )


def run(services: Services) -> bool:
    """Test both contracts, report what failed, and say whether everything passed."""
    settings = services.settings
    run_id = uuid4().hex
    wanted = [
        (
            "frostlog-collection",
            settings.contracts_dir / "collection.odcs.yaml",
            settings.collection_contract_server,
            _collection_test_environment(services),
        ),
        (
            "frostlog-semantics",
            settings.contracts_dir / "semantics.odcs.yaml",
            settings.semantics_contract_server,
            {},
        ),
    ]
    results = [
        _test_contract(services, contract_id, path, server, environment)
        for contract_id, path, server, environment in wanted
    ]
    for result in results:
        _publish_report(services, run_id, result)
    passed = all(result.passed for result in results)
    if passed:
        _ping(services)
    log.info("run %s: %s", run_id, "all contracts passed" if passed else "a contract did not pass")
    return passed


def _test_contract(
    services: Services,
    contract_id: str,
    path: Path,
    server: str,
    environment: dict[str, str] | None,
) -> ContractTestResult:
    """One contract's result, whatever happens: a crash is a finding, not a stack trace."""
    if environment is None:
        return ContractTestResult(
            contract_id, passed=False, failed_checks=["not tested: no collection HMAC secret"]
        )
    try:
        return services.tester.test(path, server, contract_id, environment)
    except Exception as exc:  # a timeout, a missing binary, anything
        log.exception("testing %s failed", contract_id)
        return ContractTestResult(
            contract_id, passed=False, failed_checks=[f"not tested: {type(exc).__name__}"]
        )


def _publish_report(services: Services, run_id: str, result: ContractTestResult) -> None:
    try:
        services.publisher.publish(
            QualityReport(
                run_id=run_id,
                published_at=datetime.now(UTC),
                contract_id=result.contract_id,
                passed=result.passed,
                failed_checks=result.failed_checks,
            )
        )
    except Exception:
        log.exception("publishing the quality report for %s failed", result.contract_id)


def _collection_test_environment(services: Services) -> dict[str, str] | None:
    """The HMAC key datacontract-cli needs for the bucket; ``None`` means: cannot test."""
    name = services.settings.collection_hmac_secret
    if not name:
        log.info("no collection HMAC secret configured; that contract is not tested")
        return None
    try:
        payload = services.secrets.read(name)
    except Exception:
        log.exception("reading the collection HMAC secret failed")
        return None
    if payload is None:
        return None
    return s3_credentials(payload)


def _ping(services: Services) -> None:
    """Report a clean run to the dead man's switch, if we know where it is."""
    settings = services.settings
    url = settings.contract_test_healthcheck_url
    if not url and settings.contract_test_healthcheck_url_secret:
        url = services.secrets.read(settings.contract_test_healthcheck_url_secret)
    if not url:
        log.info("no healthcheck URL available; the clean run is not reported")
        return
    try:
        services.ping(url)
    except Exception:
        log.exception("pinging the dead man's switch failed")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    return 0 if run(build_services()) else 1


if __name__ == "__main__":
    sys.exit(main())

"""The daily contract test: a Cloud Run job, started by Cloud Scheduler.

Both data contracts are tested against the data that is really there, each
result is published as a ``quality_report`` so the Slack application can say so,
and a run in which everything passed pings the dead man's switch.

It is a job rather than an endpoint of either service because it belongs to
neither data product: it is the check that both are keeping their word. It has an
image of its own, carrying the contracts and datacontract-cli and nothing else.

A contract that fails, and one that could not be tested at all, are findings:
they are reported, and they make the run exit non-zero so the execution itself
is marked failed. Nothing is retried — tomorrow's run is the retry.

``--contract`` narrows the run to the ones named; without it every contract is
tested, which is what the daily schedule does. The two data products go live at
different times and are fixed one at a time, so being able to ask about one of
them alone is worth an option. A narrowed run still reports what it found, but it
never pings the dead man's switch: that switch says every contract passed, and a
run that did not look at every contract cannot say so.
"""

import argparse
import logging
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from frostlog_contracts import healthcheck
from frostlog_contracts.events import QualityReport
from frostlog_contracts.project import resolve_project
from frostlog_contracts.publishing import Publisher, publisher_for
from frostlog_contracts.secret_manager import SecretManagerReader, SecretReader
from frostlog_contracts.settings import Settings
from frostlog_contracts.tester import (
    ContractTester,
    ContractTestResult,
    DatacontractTester,
    s3_credentials,
)

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
    from google.cloud import secretmanager

    settings = settings or Settings()
    project = resolve_project(settings.gcp_project)
    return Services(
        settings=settings,
        tester=DatacontractTester(),
        publisher=publisher_for(project, settings.signals_topic),
        secrets=SecretManagerReader(secretmanager.SecretManagerServiceClient(), project),
    )


#: The contracts this job can test, by the name --contract takes. The order is the
#: order they are tested in, and it is the order they depend on each other: what the
#: semantics product says is only as good as what the collection product gave it.
CONTRACTS = ("collection", "semantics")

#: Which categories of check the collection contract is held to in the bucket.
#:
#: The chunks there are gzipped, and datacontract-cli has two engines that disagree
#: about that: the DuckDB one decompresses them, the JSON Schema one reads the bytes
#: as text and dies on the gzip magic number before any check has run. There is no
#: way to declare the compression, so the run is narrowed to the categories DuckDB
#: answers — which is where the contract's own rules live, and the only place a
#: whole-table rule can be checked at all.
#:
#: Nothing is lost that is not checked elsewhere: the per-field schema checks run in
#: CI against the sample chunks (uncompressed, for this same reason), and every field
#: that reaches the warehouse is declared again in the semantics contract, which is
#: tested here in full.
COLLECTION_CHECKS = "quality"


def run(services: Services, contracts: Sequence[str] = CONTRACTS) -> bool:
    """Test the contracts named, report what failed, and say whether they all passed."""
    settings = services.settings
    run_id = uuid4().hex
    wanted = {
        "collection": (
            "frostlog-collection",
            settings.contracts_dir / "collection.odcs.yaml",
            settings.collection_contract_server,
            _collection_test_environment,
            COLLECTION_CHECKS,
        ),
        "semantics": (
            "frostlog-semantics",
            settings.contracts_dir / "semantics.odcs.yaml",
            settings.semantics_contract_server,
            lambda _: {},
            None,
        ),
    }
    chosen = [name for name in CONTRACTS if name in contracts]
    results = []
    for name in chosen:
        contract_id, path, server, environment_for, checks = wanted[name]
        results.append(
            _test_contract(services, contract_id, path, server, environment_for(services), checks)
        )
    for result in results:
        _publish_report(services, run_id, result)
    passed = all(result.passed for result in results)
    if passed and len(chosen) == len(CONTRACTS):
        _ping(services)
    elif passed:
        log.info("only %s tested; the dead man's switch is left alone", ", ".join(chosen))
    log.info("run %s: %s", run_id, "all contracts passed" if passed else "a contract did not pass")
    return passed


def _test_contract(
    services: Services,
    contract_id: str,
    path: Path,
    server: str,
    environment: dict[str, str] | None,
    checks: str | None = None,
) -> ContractTestResult:
    """One contract's result, whatever happens: a crash is a finding, not a stack trace."""
    if environment is None:
        return ContractTestResult(
            contract_id, passed=False, failed_checks=["not tested: no collection HMAC secret"]
        )
    try:
        return services.tester.test(path, server, contract_id, environment, checks)
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="frostlog-contracts", description=__doc__)
    parser.add_argument(
        "--contract",
        action="append",
        choices=CONTRACTS,
        metavar="NAME",
        help=f"test only this contract ({', '.join(CONTRACTS)}); repeatable. Default: all.",
    )
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    return 0 if run(build_services(), arguments.contract or CONTRACTS) else 1


if __name__ == "__main__":
    sys.exit(main())

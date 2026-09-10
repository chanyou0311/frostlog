"""Testing a data contract against the data that is actually there.

``datacontract test`` reads the contract, runs every schema and quality check it
declares against the named server and writes a report. It runs once a day, not
on every arrival: the checks are cross-row rules over whole tables, and the
per-arrival guarantees (types, not-null, uniqueness) are enforced by dbt itself.
"""

import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

TEST_TIMEOUT_SECONDS = 1800

#: Check results that mean the contract is not being met.
_FAILING = {"failed", "error"}


@dataclass(frozen=True)
class ContractTestResult:
    contract_id: str
    passed: bool
    failed_checks: list[str] = field(default_factory=list)
    output: str = ""


class ContractTester(Protocol):
    def test(self, contract: Path, server: str, contract_id: str) -> ContractTestResult: ...


class DatacontractTester:
    """:class:`ContractTester` that shells out to datacontract-cli."""

    def __init__(self, environment: dict[str, str] | None = None) -> None:
        self._environment = environment or {}

    def test(self, contract: Path, server: str, contract_id: str) -> ContractTestResult:
        with tempfile.TemporaryDirectory() as workspace:
            report = Path(workspace) / "test-results.json"
            command = [
                "datacontract",
                "test",
                str(contract),
                "--server",
                server,
                "--output",
                str(report),
                "--output-format",
                "json",
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=TEST_TIMEOUT_SECONDS,
                env={**os.environ, **self._environment},
                check=False,
            )
            output = completed.stdout + completed.stderr
            failed = failed_checks(report.read_text()) if report.exists() else None
        if failed is None:
            log.error("datacontract test %s (%s) produced no report\n%s", contract, server, output)
            return ContractTestResult(contract_id, passed=False, failed_checks=[], output=output)
        if failed:
            log.error("%s failed %d check(s): %s", contract_id, len(failed), ", ".join(failed))
        return ContractTestResult(
            contract_id=contract_id,
            passed=completed.returncode == 0 and not failed,
            failed_checks=failed,
            output=output,
        )


def failed_checks(report: str) -> list[str]:
    """The names of the checks that did not pass, from a datacontract JSON report."""
    run = json.loads(report)
    names = []
    for check in run.get("checks", []):
        if check.get("result") in _FAILING:
            names.append(check.get("name") or check.get("type") or check.get("id") or "unnamed")
    return names

"""Running the dbt project.

Every table is rebuilt in full, so ``dbt build`` typed by hand does exactly what
the service does. The caller supplies no dates or row selection: later arrivals
can change earlier reports as well as add new ones.

Only one run may be in flight at a time: two runs rebuilding the same tables
could overwrite a newer result with an older one. Nothing here enforces that — the
service is deployed with concurrency 1 and max-instances 1, which makes Cloud Run
the lock.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from frostlog_platform import shell

log = logging.getLogger(__name__)

#: The current warehouse builds in minutes; fifteen minutes leaves room for job startup.
BUILD_TIMEOUT_SECONDS = 900


@dataclass(frozen=True)
class BuildResult:
    passed: bool
    command: list[str]
    output: str


class Transform(Protocol):
    def build(self) -> BuildResult: ...


class DbtTransform:
    """:class:`Transform` that shells out to dbt."""

    def __init__(
        self,
        project_dir: Path,
        profiles_dir: Path,
        target: str,
        environment: dict[str, str],
        select: str | None = None,
    ) -> None:
        self._project_dir = project_dir
        self._profiles_dir = profiles_dir
        self._target = target
        self._environment = environment
        self._select = select

    def build(self) -> BuildResult:
        command = [
            "dbt",
            "build",
            "--project-dir",
            str(self._project_dir),
            "--profiles-dir",
            str(self._profiles_dir),
            "--target",
            self._target,
            # Unit tests are about the SQL, not about the data, and every one of
            # them is a query the warehouse charges its ten-mebibyte minimum for.
            # They belong to the build that changed the SQL: `make ci-warehouse`
            # runs them on their own, against the CI dataset, before anything is
            # merged. Running them again every hour in production buys nothing.
            "--exclude-resource-type",
            "unit_test",
        ]
        if self._select:
            command += ["--select", self._select]
        completed = shell.run(command, timeout=BUILD_TIMEOUT_SECONDS, environment=self._environment)
        if not completed.ok:
            log.error("dbt build failed (%s)\n%s", completed.returncode, completed.output)
        return BuildResult(passed=completed.ok, command=command, output=completed.output)

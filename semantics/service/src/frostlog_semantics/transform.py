"""Running the dbt project for the dates a chunk touched.

dbt is a command line tool, so it is run as one. The dates to rebuild are handed
over as the ``target_dates`` variable; the fact models turn them into the static
partition list of their ``insert_overwrite`` strategy, so a run rewrites exactly
those JST dates and leaves the rest of the table alone.

Only one run may be in flight at a time: two runs rebuilding the same partition
would overwrite each other. Nothing here enforces that — the service is deployed
with concurrency 1 and max-instances 1, which makes Cloud Run the lock.
"""

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from frostlog_platform import shell

log = logging.getLogger(__name__)

#: A build of a few date partitions takes seconds; anything beyond this is stuck.
BUILD_TIMEOUT_SECONDS = 900


@dataclass(frozen=True)
class BuildResult:
    passed: bool
    command: list[str]
    output: str


class Transform(Protocol):
    def build(self, target_dates: Sequence[date]) -> BuildResult: ...


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

    def build(self, target_dates: Sequence[date]) -> BuildResult:
        command = [
            "dbt",
            "build",
            "--project-dir",
            str(self._project_dir),
            "--profiles-dir",
            str(self._profiles_dir),
            "--target",
            self._target,
            "--vars",
            json.dumps({"target_dates": [day.isoformat() for day in target_dates]}),
        ]
        if self._select:
            command += ["--select", self._select]
        completed = shell.run(command, timeout=BUILD_TIMEOUT_SECONDS, environment=self._environment)
        if not completed.ok:
            log.error("dbt build failed (%s)\n%s", completed.returncode, completed.output)
        return BuildResult(passed=completed.ok, command=command, output=completed.output)

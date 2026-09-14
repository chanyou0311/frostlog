"""Running a command line tool and keeping what it printed."""

import os
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Completed:
    returncode: int
    #: stdout followed by stderr, the way a person would have seen it.
    output: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run(
    command: list[str], *, timeout: float, environment: dict[str, str] | None = None
) -> Completed:
    """Run ``command`` with the process environment plus ``environment``.

    A non-zero exit code is a result, not an exception.
    """
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, **(environment or {})},
        check=False,
    )
    return Completed(completed.returncode, completed.stdout + completed.stderr)

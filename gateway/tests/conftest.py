import logging
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _no_logging_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    # The CLI configures logging on stderr at startup; keep that out of the test runner's streams.
    monkeypatch.setattr(logging, "basicConfig", lambda **_: None)


@pytest.fixture
def socket_dir() -> Iterator[Path]:
    """A directory to put sockets in, short enough to bind.

    Not ``tmp_path``: an AF_UNIX path is about a hundred bytes at most, and pytest's
    directory carries the name of the test, which spends them.
    """
    with tempfile.TemporaryDirectory(prefix="frostlog-") as directory:
        yield Path(directory)

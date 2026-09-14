import logging
import signal
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _leave_the_process_alone(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # The CLI configures logging on stderr at startup; keep that out of the test runner's
    # streams. It also hands SIGPIPE back to the kernel, which is right for a process and
    # wrong for a test runner: the next socket test that writes to a reader which hung up
    # would kill pytest instead of raising.
    monkeypatch.setattr(logging, "basicConfig", lambda **_: None)
    handler = signal.getsignal(signal.SIGPIPE)
    yield
    signal.signal(signal.SIGPIPE, handler)


@pytest.fixture
def socket_dir() -> Iterator[Path]:
    """A directory to put sockets in, short enough to bind.

    Not ``tmp_path``: an AF_UNIX path is about a hundred bytes at most, and pytest's
    directory carries the name of the test, which spends them.
    """
    with tempfile.TemporaryDirectory(prefix="frostlog-") as directory:
        yield Path(directory)

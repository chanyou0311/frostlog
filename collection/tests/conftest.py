import logging
import signal

import pytest


@pytest.fixture(autouse=True)
def _leave_the_process_alone(monkeypatch: pytest.MonkeyPatch):
    # The CLI configures logging on stderr at startup; keep that out of the test runner's
    # streams. It also hands SIGPIPE back to the kernel, which is right for a process and
    # wrong for a test runner: the next socket test that writes to a reader which hung up
    # would kill pytest instead of raising.
    monkeypatch.setattr(logging, "basicConfig", lambda **_: None)
    handler = signal.getsignal(signal.SIGPIPE)
    yield
    signal.signal(signal.SIGPIPE, handler)

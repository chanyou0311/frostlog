import logging

import pytest


@pytest.fixture(autouse=True)
def _no_logging_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    # The CLI configures logging on stderr at startup; keep that out of the test runner's streams.
    monkeypatch.setattr(logging, "basicConfig", lambda **_: None)

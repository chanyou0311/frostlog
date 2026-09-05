import os

import pytest

from frostlog.settings import Settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("FROSTLOG_"):
            monkeypatch.delenv(name)
    settings = Settings()
    assert settings.i2c_bus == 1
    assert settings.ambient_sensor == "dht20"
    assert settings.ambient_address is None
    assert settings.s3_bucket == "frostlog"


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FROSTLOG_AMBIENT_SENSOR", "am2320")
    monkeypatch.setenv("FROSTLOG_AMBIENT_ADDRESS", "0x5c")
    monkeypatch.setenv("FROSTLOG_COOLER_ADDRESS", "")
    settings = Settings()
    assert settings.ambient_sensor == "am2320"
    assert settings.ambient_address == 0x5C
    assert settings.cooler_address is None

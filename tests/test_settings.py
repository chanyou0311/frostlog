import os

import pytest

from frostlog.settings import Settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("FROSTLOG_"):
            monkeypatch.delenv(name)
    settings = Settings()
    assert settings.i2c_bus == 1
    assert settings.cooler_model == "everfrost"
    assert settings.s3_bucket == "frostlog-raw"
    assert settings.s3_endpoint == "https://storage.googleapis.com"
    assert settings.healthcheck_url is None


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FROSTLOG_I2C_BUS", "3")
    monkeypatch.setenv("FROSTLOG_COOLER_ADDRESS", "")
    settings = Settings()
    assert settings.i2c_bus == 3
    assert settings.cooler_address is None

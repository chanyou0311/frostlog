import os

import pytest

from frostlog.settings import Settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("FROSTLOG_"):
            monkeypatch.delenv(name)
    settings = Settings()
    assert settings.i2c_bus == 1
    assert settings.gateway_socket_dir is None
    assert settings.s3_bucket == "chanyou-frostlog-collection"
    assert settings.s3_endpoint == "https://storage.googleapis.com"
    assert settings.healthcheck_url is None


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FROSTLOG_I2C_BUS", "3")
    # An empty value in the shared env file means "the default", not "this".
    monkeypatch.setenv("FROSTLOG_GATEWAY_SOCKET_DIR", "")
    settings = Settings()
    assert settings.i2c_bus == 3
    assert settings.gateway_socket_dir is None

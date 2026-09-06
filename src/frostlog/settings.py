"""Machine-specific configuration read from FROSTLOG_* environment variables.

Only values that are fixed for a given machine live here (which sensor is wired,
which bucket to upload to). Anything that describes a single run (paths,
intervals, counts) is a CLI argument.
"""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FROSTLOG_", env_ignore_empty=True)

    i2c_bus: int = 1
    ambient_sensor: str = "dht20"
    ambient_address: int | None = None

    cooler_model: str = "everfrost"
    cooler_address: str | None = None

    s3_endpoint: str | None = None
    s3_bucket: str = "frostlog"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None

    #: Pinged after every upload run that reached the bucket and left nothing behind.
    healthcheck_url: str | None = None

    @field_validator("ambient_address", mode="before")
    @classmethod
    def _parse_address(cls, value: object) -> object:
        # Allow the usual "0x38" spelling of I2C addresses.
        if isinstance(value, str):
            return int(value, 0)
        return value

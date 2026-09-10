"""Machine-specific configuration read from FROSTLOG_* environment variables.

Only values that are fixed for a given machine live here (which I2C bus, which
cooler, which bucket to upload to). Anything that describes a single run
(paths, intervals, counts, which sensor to read) is a CLI argument.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FROSTLOG_", env_ignore_empty=True)

    i2c_bus: int = 1

    cooler_model: str = "everfrost"
    cooler_address: str | None = None

    #: Google Cloud Storage through its S3-compatible XML API; the keys are HMAC keys.
    s3_endpoint: str | None = "https://storage.googleapis.com"
    s3_bucket: str = "chanyou-frostlog-raw"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None

    #: Pinged after every upload run that reached the bucket and left nothing behind.
    healthcheck_url: str | None = None

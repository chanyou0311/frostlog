"""Machine-specific configuration read from FROSTLOG_* environment variables.

Terraform sets these on the Cloud Run job. The two secrets are named, never
valued: what the job may read is decided by IAM, not by what is in its
environment.

Which warehouse to look in is not among them: each contract names its own servers,
and the job only picks which of them to hold the data to.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FROSTLOG_", env_ignore_empty=True)

    #: Overrides the project Application Default Credentials belong to.
    gcp_project: str | None = None

    #: Short name of the Pub/Sub topic the signals go to (Terraform passes the short
    #: name; :func:`frostlog_contracts.project.topic_path` turns it into the full one).
    signals_topic: str | None = None

    #: The contracts directory this package sits in -- true in a checkout and in the
    #: image, which keeps the package under it for exactly this reason.
    contracts_dir: Path = Path(__file__).resolve().parents[3]

    #: Dead man's switch pinged after a run in which every contract passed. The direct
    #: value is for local runs; in production the URL is a secret and only its Secret
    #: Manager name is in the environment.
    contract_test_healthcheck_url: str | None = None
    contract_test_healthcheck_url_secret: str | None = None

    #: Secret Manager name of the HMAC key datacontract-cli needs to read the collection
    #: bucket through its S3-compatible API. Payload: {"access_id": …, "secret": …}.
    collection_hmac_secret: str | None = None

    #: Server of contracts/collection.odcs.yaml to test against.
    collection_contract_server: str = "gcs"
    #: Server of contracts/semantics.odcs.yaml to test against.
    semantics_contract_server: str = "production"

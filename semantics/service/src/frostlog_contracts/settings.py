"""Machine-specific configuration read from FROSTLOG_* environment variables.

Terraform sets these on the Cloud Run job. The two secrets are named, never
valued: what the job may read is decided by IAM, not by what is in its
environment.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

#: Repository root as laid out both in the container and in a checkout:
#: <root>/semantics/service/src/frostlog_contracts/settings.py.
_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FROSTLOG_", env_ignore_empty=True)

    #: Overrides the project Application Default Credentials belong to.
    gcp_project: str | None = None

    #: The warehouse the semantics contract is tested against.
    bq_dataset: str = "frostlog"
    bq_raw_dataset: str | None = None
    bq_location: str = "us-central1"

    #: Short name of the Pub/Sub topic the quality reports go to.
    events_topic: str | None = None

    #: Dead man's switch pinged after a run in which every contract passed. The direct
    #: value is for local runs; in production the URL is a secret and only its Secret
    #: Manager name is in the environment.
    contract_test_healthcheck_url: str | None = None
    contract_test_healthcheck_url_secret: str | None = None

    #: Secret Manager name of the HMAC key datacontract-cli needs to read the collection
    #: bucket through its S3-compatible API. Payload: {"access_id": …, "secret": …}.
    collection_hmac_secret: str | None = None

    contracts_dir: Path = _ROOT / "contracts"
    #: Server of contracts/collection.odcs.yaml to test against.
    collection_contract_server: str = "gcs"
    #: Server of contracts/semantics.odcs.yaml to test against.
    semantics_contract_server: str = "production"

    @property
    def raw_dataset(self) -> str:
        """Dataset the raw tables live in; the same one unless told otherwise."""
        return self.bq_raw_dataset or self.bq_dataset

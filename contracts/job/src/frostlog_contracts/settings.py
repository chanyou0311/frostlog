"""Machine-specific configuration read from FROSTLOG_* environment variables.

Terraform sets these on the Cloud Run job. The two secrets are named, never
valued: what the job may read is decided by IAM, not by what is in its
environment.

One of these is counted rather than named: the path back to the repository root
is a number of directories, so a copy of it in a module at another depth would be
wrong without looking wrong. It is what a run from a checkout uses; the image
says where the contracts are outright, because the package is not under the
repository tree there.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

#: Repository root as laid out in a checkout:
#: <root>/contracts/job/src/frostlog_contracts/settings.py.
ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FROSTLOG_", env_ignore_empty=True)

    #: Overrides the project Application Default Credentials belong to.
    gcp_project: str | None = None

    #: Dataset the contracts are tested against.
    bq_dataset: str = "frostlog"
    #: Dataset the raw tables are loaded into; the same one unless told otherwise.
    bq_raw_dataset: str | None = None
    bq_location: str = "us-central1"

    #: Short name of the Pub/Sub topic the signals go to (Terraform passes the short
    #: name; :func:`frostlog_contracts.project.topic_path` turns it into the full one).
    signals_topic: str | None = None

    #: Where the contracts are.
    contracts_dir: Path = ROOT / "contracts"

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

    @property
    def raw_dataset(self) -> str:
        return self.bq_raw_dataset or self.bq_dataset

    def warehouse_environment(self, project: str) -> dict[str, str]:
        """What a subprocess needs to find the warehouse: datacontract-cli's."""
        return {
            "FROSTLOG_BQ_PROJECT": project,
            "FROSTLOG_BQ_DATASET": self.bq_dataset,
            "FROSTLOG_BQ_RAW_DATASET": self.raw_dataset,
            "FROSTLOG_BQ_LOCATION": self.bq_location,
        }

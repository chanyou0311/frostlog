"""What the service and the job are both told, and where the repository is.

The two components ship in one image and read the same ``FROSTLOG_*`` environment
Terraform sets: which project, which datasets, which topic. Saying that twice
invited the two copies to drift, and one of them — the path back to the
repository root — is counted in directories, so a copy in a module at another
depth would be wrong without looking wrong.

Each component adds its own settings on top of this by subclassing.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

#: Repository root as laid out both in the container and in a checkout:
#: <root>/semantics/service/src/frostlog_platform/settings.py.
ROOT = Path(__file__).resolve().parents[4]


class PlatformSettings(BaseSettings):
    """The warehouse, the topic and the contracts, as every component sees them."""

    model_config = SettingsConfigDict(env_prefix="FROSTLOG_", env_ignore_empty=True)

    #: Overrides the project Application Default Credentials belong to.
    gcp_project: str | None = None

    #: Dataset the dbt models are built into and the contracts are tested against.
    bq_dataset: str = "frostlog"
    #: Dataset the raw tables are loaded into; the same one unless told otherwise.
    bq_raw_dataset: str | None = None
    bq_location: str = "us-central1"

    #: Short name of the Pub/Sub topic the signals go to (Terraform passes the short
    #: name; :func:`frostlog_platform.project.topic_path` turns it into the full one).
    signals_topic: str | None = None

    #: Where the contracts are.
    contracts_dir: Path = ROOT / "contracts"

    @property
    def raw_dataset(self) -> str:
        return self.bq_raw_dataset or self.bq_dataset

    def warehouse_environment(self, project: str) -> dict[str, str]:
        """What a subprocess needs to find the warehouse: dbt's, datacontract-cli's."""
        return {
            "FROSTLOG_BQ_PROJECT": project,
            "FROSTLOG_BQ_DATASET": self.bq_dataset,
            "FROSTLOG_BQ_RAW_DATASET": self.raw_dataset,
            "FROSTLOG_BQ_LOCATION": self.bq_location,
        }

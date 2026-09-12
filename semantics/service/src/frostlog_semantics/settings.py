"""Machine-specific configuration read from FROSTLOG_* environment variables.

Everything here is fixed for a deployment (which bucket, which dataset, which
topic) and is set by Terraform on the Cloud Run service. What changes per
request — the object that arrived and the dates it touches — comes from the
request itself.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

#: Repository root as laid out both in the container and in a checkout:
#: <root>/semantics/service/src/frostlog_semantics/settings.py.
_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FROSTLOG_", env_ignore_empty=True)

    #: The collection product's bucket, which Eventarc watches. An event about any
    #: other bucket is not ours.
    collection_bucket: str | None = None

    #: Overrides the project Application Default Credentials belong to.
    gcp_project: str | None = None

    #: Dataset the dbt models are built into.
    bq_dataset: str = "frostlog"
    #: Dataset the CI warehouse check builds into.
    bq_dataset_ci: str = "frostlog_ci"
    #: Dataset the raw tables are loaded into; the same one unless told otherwise.
    bq_raw_dataset: str | None = None
    bq_location: str = "us-central1"

    #: Short name of the Pub/Sub topic the events go to (Terraform passes the short
    #: name; :func:`frostlog_platform.project.topic_path` turns it into the full one).
    signals_topic: str | None = None

    dbt_project_dir: Path = _ROOT / "semantics" / "dbt"
    dbt_profiles_dir: Path = _ROOT / "semantics" / "dbt"
    dbt_target: str = "prod"
    #: dbt selector for the on-arrival build. Empty means the whole project, which is
    #: what production uses: the dimensions and the seeds are tiny, and rebuilding them
    #: on every run keeps the facts' surrogate keys resolvable.
    dbt_select: str | None = None

    #: Where the contracts are, for the sample chunks the CI warehouse check loads.
    contracts_dir: Path = _ROOT / "contracts"

    @property
    def raw_dataset(self) -> str:
        return self.bq_raw_dataset or self.bq_dataset

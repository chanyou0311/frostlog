"""Machine-specific configuration read from FROSTLOG_* environment variables.

Everything here is fixed for a deployment (which bucket, which dataset, which
topic) and is set by Terraform on the Cloud Run service; a request carries
nothing, it only says "now".

One of these is counted rather than named: the path back to the repository root
is a number of directories, so a copy of it in a module at another depth would be
wrong without looking wrong.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

#: Repository root as laid out in a checkout:
#: <root>/semantics/service/src/frostlog_semantics/settings.py.
ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FROSTLOG_", env_ignore_empty=True)

    #: Overrides the project Application Default Credentials belong to.
    gcp_project: str | None = None

    #: Dataset the dbt models are built into.
    bq_dataset: str = "frostlog"
    #: Dataset the raw tables are loaded into; the same one unless told otherwise.
    bq_raw_dataset: str | None = None
    bq_location: str = "us-central1"

    #: Short name of the Pub/Sub topic the signals go to (Terraform passes the short
    #: name; :func:`frostlog_semantics.project.topic_path` turns it into the full one).
    signals_topic: str | None = None

    #: The collection product's bucket. Nothing here loads from it any more -- a
    #: transfer does that -- but the mark saying how far the transform has built lives
    #: in it, because a bucket object is free to read and a warehouse row is not.
    collection_bucket: str = "chanyou-frostlog-collection"

    #: Dataset the CI warehouse check builds into.
    bq_dataset_ci: str = "frostlog_ci"

    dbt_project_dir: Path = ROOT / "semantics" / "dbt"
    dbt_profiles_dir: Path = ROOT / "semantics" / "dbt"
    dbt_target: str = "prod"
    #: dbt selector for the scheduled build. Empty means the whole project, which is
    #: what production uses: the dimensions and the seeds are tiny, and rebuilding them
    #: on every run keeps the facts' surrogate keys resolvable.
    dbt_select: str | None = None

    @property
    def raw_dataset(self) -> str:
        return self.bq_raw_dataset or self.bq_dataset

    def warehouse_environment(self, project: str) -> dict[str, str]:
        """What a subprocess needs to find the warehouse: dbt's."""
        return {
            "FROSTLOG_BQ_PROJECT": project,
            "FROSTLOG_BQ_DATASET": self.bq_dataset,
            "FROSTLOG_BQ_RAW_DATASET": self.raw_dataset,
            "FROSTLOG_BQ_LOCATION": self.bq_location,
        }

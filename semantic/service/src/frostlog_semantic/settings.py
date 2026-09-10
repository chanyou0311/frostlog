"""Machine-specific configuration read from FROSTLOG_* environment variables.

Everything here is fixed for a deployment (which project, which dataset, which
topic). What changes per request — the object that arrived and the dates it
touches — comes from the request itself.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

#: Repository root as laid out both in the container and in a checkout:
#: <root>/semantic/service/src/frostlog_semantic/settings.py.
_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FROSTLOG_", env_ignore_empty=True)

    #: BigQuery project holding both the raw tables and the semantic tables.
    bq_project: str = "frostlog-chanyou"
    #: Dataset the dbt models are built into.
    bq_dataset: str = "frostlog"
    #: Dataset the raw tables are loaded into; the same one unless told otherwise.
    bq_raw_dataset: str | None = None
    bq_location: str = "us-central1"

    #: Full topic name, e.g. projects/frostlog-chanyou/topics/frostlog-semantic-updated.
    pubsub_topic: str | None = None

    #: Dead man's switch pinged after a contract test run in which everything passed.
    contract_test_healthcheck_url: str | None = None

    dbt_project_dir: Path = _ROOT / "semantic" / "dbt"
    dbt_profiles_dir: Path = _ROOT / "semantic" / "dbt"
    dbt_target: str = "prod"
    #: dbt selector for the on-arrival build. Empty means the whole project, which is
    #: what production uses: the dimensions and the seeds are tiny, and rebuilding them
    #: on every run keeps the facts' surrogate keys resolvable.
    dbt_select: str | None = None

    contracts_dir: Path = _ROOT / "contracts"
    #: Server of contracts/raw.odcs.yaml the daily contract test runs against.
    raw_contract_server: str = "gcs"
    #: Server of contracts/semantic.odcs.yaml the daily contract test runs against.
    semantic_contract_server: str = "production"

    @property
    def raw_dataset(self) -> str:
        return self.bq_raw_dataset or self.bq_dataset

"""Machine-specific configuration read from FROSTLOG_* environment variables.

Everything here is fixed for a deployment (which bucket, which dataset, which
topic) and is set by Terraform on the Cloud Run service. What changes per
request — the object that arrived and the dates it touches — comes from the
request itself.

The project id is deliberately not a setting with a default: it is whatever the
credentials the service runs with belong to (``google.auth.default()``), with
``FROSTLOG_GCP_PROJECT`` as an override for the odd case where they differ.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

#: Repository root as laid out both in the container and in a checkout:
#: <root>/semantic/service/src/frostlog_semantic/settings.py.
_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FROSTLOG_", env_ignore_empty=True)

    #: The raw bucket Eventarc watches. An event about any other bucket is not ours.
    raw_bucket: str | None = None

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
    #: name; :func:`topic_path` turns it into the full one).
    semantic_updated_topic: str | None = None

    #: Dead man's switch pinged after a contract test run in which everything passed.
    #: The direct value is for local runs; in production the URL is a secret and only
    #: its Secret Manager name is in the environment.
    contract_test_healthcheck_url: str | None = None
    contract_test_healthcheck_url_secret: str | None = None

    #: Secret Manager name of the HMAC key the raw contract test needs to read the
    #: bucket through its S3-compatible API. Payload: {"access_id": …, "secret": …}.
    raw_hmac_secret: str | None = None

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


def resolve_project(override: str | None = None) -> str:
    """The GCP project to work in: the override, else the one ADC belong to."""
    if override:
        return override
    import google.auth

    _, project = google.auth.default()
    if not project:
        raise RuntimeError("no project in the ambient credentials; set FROSTLOG_GCP_PROJECT")
    return str(project)


def topic_path(project: str, topic: str) -> str:
    """Full Pub/Sub topic name from the short name Terraform passes."""
    if topic.startswith("projects/"):
        return topic
    return f"projects/{project}/topics/{topic}"

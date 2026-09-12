"""Machine-specific configuration read from FROSTLOG_* environment variables.

Everything here is fixed for a deployment (which bucket, which dataset, which
topic) and is set by Terraform on the Cloud Run service. What changes per
request — the object that arrived and the dates it touches — comes from the
request itself.
"""

from pathlib import Path

from frostlog_platform.settings import ROOT, PlatformSettings


class Settings(PlatformSettings):
    #: The collection product's bucket, which Eventarc watches. An event about any
    #: other bucket is not ours.
    collection_bucket: str | None = None

    #: Dataset the CI warehouse check builds into.
    bq_dataset_ci: str = "frostlog_ci"

    #: How far back the transform looks for chunks that have arrived. Longer than the
    #: half hour between runs on purpose: a run the scheduler missed, or one that failed
    #: every retry, is made good by the next one instead of leaving a date unbuilt. A day
    #: and an hour covers any single outage worth repairing this way; a longer one is a
    #: --full-refresh, not a wider window.
    transform_lookback_hours: float = 25.0

    dbt_project_dir: Path = ROOT / "semantics" / "dbt"
    dbt_profiles_dir: Path = ROOT / "semantics" / "dbt"
    dbt_target: str = "prod"
    #: dbt selector for the on-arrival build. Empty means the whole project, which is
    #: what production uses: the dimensions and the seeds are tiny, and rebuilding them
    #: on every run keeps the facts' surrogate keys resolvable.
    dbt_select: str | None = None

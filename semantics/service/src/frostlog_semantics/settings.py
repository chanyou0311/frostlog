"""Machine-specific configuration read from FROSTLOG_* environment variables.

Everything here is fixed for a deployment (which bucket, which dataset, which
topic) and is set by Terraform on the Cloud Run service. What changes per
request — the object that arrived — comes from the request itself.
"""

from pathlib import Path

from frostlog_platform.settings import ROOT, PlatformSettings


class Settings(PlatformSettings):
    #: The collection product's bucket. Nothing here reads it since a transfer took
    #: over the loading; it stays so that the environment Terraform sets still parses.
    collection_bucket: str | None = None

    #: Dataset the CI warehouse check builds into.
    bq_dataset_ci: str = "frostlog_ci"

    #: How far back the transform looks for chunks that have arrived. Longer than the
    #: hour between runs on purpose: a run the scheduler missed, or one that failed
    #: every retry, is made good by the next one. The window only decides whether a
    #: build is due; each build reads all history, so a longer outage can be repaired
    #: by a plain dbt build or by the next arrival that triggers one.
    transform_lookback_hours: float = 25.0

    dbt_project_dir: Path = ROOT / "semantics" / "dbt"
    dbt_profiles_dir: Path = ROOT / "semantics" / "dbt"
    dbt_target: str = "prod"
    #: dbt selector for the scheduled build. Empty means the whole project, which is
    #: what production uses: the dimensions and the seeds are tiny, and rebuilding them
    #: on every run keeps the facts' surrogate keys resolvable.
    dbt_select: str | None = None

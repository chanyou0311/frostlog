"""Machine-specific configuration read from FROSTLOG_* environment variables.

Everything here is fixed for a deployment (which bucket, which dataset, which
topic) and is set by Terraform on the Cloud Run service. What changes per
request — the object that arrived — comes from the request itself.
"""

from pathlib import Path

from frostlog_platform.settings import ROOT, PlatformSettings


class Settings(PlatformSettings):
    #: The collection product's bucket. Nothing here loads from it any more -- a
    #: transfer does that -- but the mark saying how far the transform has built lives
    #: in it, because a bucket object is free to read and a warehouse row is not.
    collection_bucket: str = "chanyou-frostlog-collection"

    #: Dataset the CI warehouse check builds into.
    bq_dataset_ci: str = "frostlog_ci"

    #: How far back a build looks for the collector's upload runs to announce. It no
    #: longer decides whether to build -- the row counts do that -- so it only has to
    #: be long enough that a run finishing between two builds is still reported. A day
    #: and an hour covers an outage of any length worth reporting this way.
    transform_lookback_hours: float = 25.0

    dbt_project_dir: Path = ROOT / "semantics" / "dbt"
    dbt_profiles_dir: Path = ROOT / "semantics" / "dbt"
    dbt_target: str = "prod"
    #: dbt selector for the scheduled build. Empty means the whole project, which is
    #: what production uses: the dimensions and the seeds are tiny, and rebuilding them
    #: on every run keeps the facts' surrogate keys resolvable.
    dbt_select: str | None = None

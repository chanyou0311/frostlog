"""The Cloud Run service of the semantics data product: transforming.

``POST /jobs/transform`` is what Cloud Scheduler calls every hour: when rows have
arrived lately, dbt rebuilds every table and a ``semantic_updated`` event says what
changed. Running it on every chunk instead would cost 288 builds a day, which leaves
both BigQuery's and Cloud Run's free tiers inside a couple of months; the contract's
promise is a day, so an hour is not a loss.

Getting the chunks into raw is no longer done here, or anywhere in this repository:
a BigQuery Data Transfer Service config reads the bucket on its own schedule. The
chunks are newline-delimited JSON whose fields are the table's, so a load needs
nothing added to them, and loads are not billed. What used to be here was a load
followed by a statement whose only purpose was to add two columns, and statements
are billed -- about 11 GiB a day of a 1 TiB month, for two columns.

Failing with 500 is how the endpoint asks Cloud Scheduler to try again, and a
transform that fails publishes nothing -- consumers hear about a build only once it
stands.

Whether the contracts are being kept is a different question, asked once a day by
a job of its own (:mod:`frostlog_contracts`), because it is about both data
products rather than this one.

The endpoint does not authenticate: only Cloud Run IAM (OIDC) may call it.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException

from frostlog_platform.events import Publisher, publisher_for
from frostlog_platform.project import resolve_project
from frostlog_semantics import raw_objects
from frostlog_semantics.events import SemanticUpdated
from frostlog_semantics.settings import Settings
from frostlog_semantics.transform import DbtTransform, Transform
from frostlog_semantics.warehouse import (
    BigQueryWarehouse,
    Warehouse,
)

log = logging.getLogger(__name__)


@dataclass
class Services:
    """Everything the endpoints talk to. Tests pass fakes; production passes clients."""

    settings: Settings
    warehouse: Warehouse
    transform: Transform
    publisher: Publisher


def build_services(settings: Settings | None = None) -> Services:
    """The production wiring: BigQuery, Cloud Storage and Pub/Sub through ADC."""
    from google.cloud import bigquery

    settings = settings or Settings()
    project = resolve_project(settings.gcp_project)
    client = bigquery.Client(project=project)
    return Services(
        settings=settings,
        warehouse=BigQueryWarehouse(client, project, settings.raw_dataset, settings.bq_location),
        transform=DbtTransform(
            project_dir=settings.dbt_project_dir,
            profiles_dir=settings.dbt_profiles_dir,
            target=settings.dbt_target,
            environment=settings.warehouse_environment(project),
            select=settings.dbt_select,
        ),
        publisher=publisher_for(project, settings.signals_topic),
    )


def create_app(services: Services | None = None) -> FastAPI:
    app = FastAPI(title="frostlog-semantics")
    resolved = services

    def current() -> Services:
        nonlocal resolved
        if resolved is None:
            resolved = build_services()
        return resolved

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/jobs/transform")
    def transform_job() -> dict[str, Any]:
        return transform_due(current())

    return app


def transform_due(services: Services) -> dict[str, Any]:
    """Rebuild the warehouse when chunks have arrived lately, and say so if any did.

    The window is on arrival, not on the dates in the data: a trip's records are days
    old when they land, but still need a build. The window decides whether to run,
    while the build reads all the raw history. It reaches back further than the
    schedule steps so that a run the scheduler missed is made good by the next one.
    """
    since = datetime.now(UTC) - timedelta(hours=services.settings.transform_lookback_hours)
    dates = services.warehouse.arrived_dates(since)
    if not dates:
        # Nothing came in. Replacing every table would repeat work with no new input,
        # and publishing would announce a change that did not happen. The dates
        # themselves only travel on the event; they do not limit the build.
        log.info("no chunk arrived since %s; nothing to rebuild", since)
        return {"status": "idle", "since": since.isoformat()}

    build = services.transform.build()
    if not build.passed:
        # Scheduler retries; the build is idempotent and the window still holds.
        raise HTTPException(status_code=500, detail="dbt build failed")

    # The collector writes upload_done after its PUTs, so a run's own completion travels
    # in the next run's events chunk. Which chunks were processed in which order says
    # nothing — they arrive on their own and a failed one comes back later — so this
    # announces the runs that ended and leaves what they covered to whoever reads the
    # model.
    upload_runs = services.warehouse.upload_runs(since)
    services.publisher.publish(
        SemanticUpdated(
            run_id=uuid4().hex,
            published_at=datetime.now(UTC),
            date_keys=[raw_objects.date_key(day) for day in dates],
            raw_loaded_since=since,
            build_passed=True,
            upload_runs=upload_runs,
        )
    )
    return {
        "status": "built",
        "since": since.isoformat(),
        "date_keys": [raw_objects.date_key(day) for day in dates],
        "upload_runs": len(upload_runs),
    }

"""The Cloud Run service of the semantics data product: one endpoint, one job.

``POST /events/gcs`` is what Eventarc calls when a chunk lands in the collection
bucket: the chunk goes into its BigQuery raw table, dbt rebuilds the JST dates it
touched and a ``semantic_updated`` event says what changed. Failing with 500 is
how the service asks Eventarc to deliver again, and a run that fails publishes
nothing — consumers hear about a build only once it stands.

Whether the contracts are being kept is a different question, asked once a day by
a job of its own (:mod:`frostlog_contracts`), because it is about both data
products rather than this one.

The endpoint does not authenticate: only Cloud Run IAM (OIDC) may call it.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException

from frostlog_platform.events import NoPublisher, Publisher, PubSubPublisher
from frostlog_platform.project import resolve_project, topic_path
from frostlog_semantics import raw_objects
from frostlog_semantics.events import SemanticUpdated
from frostlog_semantics.settings import Settings
from frostlog_semantics.transform import DbtTransform, Transform
from frostlog_semantics.warehouse import (
    BigQueryWarehouse,
    ObjectMetadata,
    StorageObjectMetadata,
    Warehouse,
)

log = logging.getLogger(__name__)


@dataclass
class Services:
    """Everything the endpoints talk to. Tests pass fakes; production passes clients."""

    settings: Settings
    warehouse: Warehouse
    metadata: ObjectMetadata
    transform: Transform
    publisher: Publisher


def build_services(settings: Settings | None = None) -> Services:
    """The production wiring: BigQuery, Cloud Storage and Pub/Sub through ADC."""
    from google.cloud import bigquery, pubsub_v1, storage

    settings = settings or Settings()
    project = resolve_project(settings.gcp_project)
    environment = {
        "FROSTLOG_BQ_PROJECT": project,
        "FROSTLOG_BQ_DATASET": settings.bq_dataset,
        "FROSTLOG_BQ_RAW_DATASET": settings.raw_dataset,
        "FROSTLOG_BQ_LOCATION": settings.bq_location,
    }
    client = bigquery.Client(project=project)
    topic = settings.signals_topic
    publisher: Publisher = (
        PubSubPublisher(pubsub_v1.PublisherClient(), topic_path(project, topic))
        if topic
        else NoPublisher()
    )
    return Services(
        settings=settings,
        warehouse=BigQueryWarehouse(client, project, settings.raw_dataset, settings.bq_location),
        metadata=StorageObjectMetadata(storage.Client(project=project)),
        transform=DbtTransform(
            project_dir=settings.dbt_project_dir,
            profiles_dir=settings.dbt_profiles_dir,
            target=settings.dbt_target,
            environment=environment,
            select=settings.dbt_select,
        ),
        publisher=publisher,
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

    @app.post("/events/gcs")
    def gcs_event(
        payload: dict[str, Any],
        ce_time: Annotated[str | None, Header(alias="ce-time")] = None,
    ) -> dict[str, Any]:
        return chunk_arrived(current(), payload, ce_time)

    return app


def chunk_arrived(
    services: Services, payload: dict[str, Any], ce_time: str | None = None
) -> dict[str, Any]:
    """Load the chunk the event describes and rebuild the dates it touched."""
    # Eventarc sends the object either as the body (binary mode) or under `data`.
    nested = payload.get("data")
    body: dict[str, Any] = nested if isinstance(nested, dict) else payload
    bucket, name = body.get("bucket"), body.get("name")
    if not bucket or not name:
        raise HTTPException(status_code=400, detail="event carries no bucket/name")

    wanted = services.settings.collection_bucket
    if wanted and bucket != wanted:
        log.info("%s: not the collection bucket, ignored", bucket)
        return {"status": "ignored", "bucket": bucket, "object": name}

    chunk = raw_objects.parse(bucket, name)
    if chunk is None:
        log.info("%s: not a raw chunk, ignored", name)
        return {"status": "ignored", "object": name}

    uploaded_at = services.metadata.uploaded_at(chunk) or _event_time(payload, ce_time)
    load = services.warehouse.load(chunk, uploaded_at)
    dates = raw_objects.target_dates(chunk.dt)
    build = services.transform.build(dates)
    if not build.passed:
        # Eventarc delivers again; the load and the build are both idempotent.
        raise HTTPException(status_code=500, detail="dbt build failed")

    # Only an events chunk carries upload runs: the collector writes upload_done after
    # its PUTs, so it travels in the next run. That a run's cooler chunks were put in the
    # bucket first says nothing about the order they are processed in — chunks arrive on
    # their own and a failed one comes back later — so this announces that a run ended
    # and leaves what it covered to whoever reads the model.
    upload_runs = services.warehouse.upload_runs(chunk) if chunk.stream == "events" else []
    services.publisher.publish(
        SemanticUpdated(
            run_id=uuid4().hex,
            published_at=datetime.now(UTC),
            date_keys=[raw_objects.date_key(day) for day in dates],
            raw_uploaded_at_max=uploaded_at,
            build_passed=True,
            upload_runs=upload_runs,
        )
    )
    return {
        "status": "built",
        "object": name,
        "table": load.table,
        "rows": load.rows,
        "already_loaded": load.already_loaded,
        "date_keys": [raw_objects.date_key(day) for day in dates],
        "upload_runs": len(upload_runs),
    }


def _event_time(payload: dict[str, Any], ce_time: str | None) -> datetime:
    """When the object was finalized, as a last resort for its upload time."""
    for candidate in (payload.get("time"), ce_time, payload.get("timeCreated")):
        if isinstance(candidate, str):
            try:
                return datetime.fromisoformat(candidate)
            except ValueError:
                log.warning("unreadable event time %r", candidate)
    return datetime.now(UTC)


app = create_app()

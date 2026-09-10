"""The Cloud Run service: two endpoints, both of them jobs rather than queries.

``POST /events/gcs`` is what Eventarc calls when a chunk lands in the raw bucket:
the chunk goes into its BigQuery raw table, dbt rebuilds the JST dates it touched
and a ``semantic_updated`` event says what changed. Failing with 500 is how the
service asks Eventarc to deliver again.

``POST /jobs/contract-test`` is what Cloud Scheduler calls once a day: both data
contracts are tested against production, each result is published as a
``quality_report`` event, and a clean run pings the dead man's switch. It always
answers 200 — a failing contract is a finding, not a broken request.

Neither endpoint authenticates: only Cloud Run IAM (OIDC) may call them.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException

from frostlog_semantic import healthcheck, raw_objects
from frostlog_semantic.contracts import ContractTester, DatacontractTester
from frostlog_semantic.events import (
    NoPublisher,
    Publisher,
    PubSubPublisher,
    QualityReport,
    SemanticUpdated,
)
from frostlog_semantic.settings import Settings
from frostlog_semantic.transform import DbtTransform, Transform
from frostlog_semantic.warehouse import (
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
    tester: ContractTester
    ping: Callable[[str], None] = healthcheck.ping


def build_services(settings: Settings | None = None) -> Services:
    """The production wiring: BigQuery, Cloud Storage and Pub/Sub through ADC."""
    from google.cloud import bigquery, pubsub_v1, storage

    settings = settings or Settings()
    environment = {
        "FROSTLOG_BQ_PROJECT": settings.bq_project,
        "FROSTLOG_BQ_DATASET": settings.bq_dataset,
        "FROSTLOG_BQ_RAW_DATASET": settings.raw_dataset,
        "FROSTLOG_BQ_LOCATION": settings.bq_location,
    }
    client = bigquery.Client(project=settings.bq_project)
    return Services(
        settings=settings,
        warehouse=BigQueryWarehouse(
            client, settings.bq_project, settings.raw_dataset, settings.bq_location
        ),
        metadata=StorageObjectMetadata(storage.Client(project=settings.bq_project)),
        transform=DbtTransform(
            project_dir=settings.dbt_project_dir,
            profiles_dir=settings.dbt_profiles_dir,
            target=settings.dbt_target,
            environment=environment,
            select=settings.dbt_select,
        ),
        publisher=(
            PubSubPublisher(pubsub_v1.PublisherClient(), settings.pubsub_topic)
            if settings.pubsub_topic
            else NoPublisher()
        ),
        tester=DatacontractTester(environment),
    )


def create_app(services: Services | None = None) -> FastAPI:
    app = FastAPI(title="frostlog-semantic")
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

    @app.post("/jobs/contract-test")
    def contract_test_job() -> dict[str, Any]:
        return contract_test(current())

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

    chunk = raw_objects.parse(bucket, name)
    if chunk is None:
        log.info("%s: not a raw chunk, ignored", name)
        return {"status": "ignored", "object": name}

    uploaded_at = services.metadata.uploaded_at(chunk) or _event_time(payload, ce_time)
    load = services.warehouse.load(chunk, uploaded_at)
    dates = raw_objects.target_dates(chunk.dt)
    build = services.transform.build(dates)
    services.publisher.publish(
        SemanticUpdated(
            run_id=uuid4().hex,
            published_at=datetime.now(UTC),
            date_keys=[raw_objects.date_key(day) for day in dates],
            raw_uploaded_at_max=uploaded_at,
            build_passed=build.passed,
        )
    )
    if not build.passed:
        raise HTTPException(status_code=500, detail="dbt build failed")
    return {
        "status": "built",
        "object": name,
        "table": load.table,
        "rows": load.rows,
        "already_loaded": load.already_loaded,
        "date_keys": [raw_objects.date_key(day) for day in dates],
    }


def contract_test(services: Services) -> dict[str, Any]:
    """Test both contracts against production and report what failed."""
    settings = services.settings
    run_id = uuid4().hex
    wanted = [
        ("frostlog-raw", settings.contracts_dir / "raw.odcs.yaml", settings.raw_contract_server),
        (
            "frostlog-semantic",
            settings.contracts_dir / "semantic.odcs.yaml",
            settings.semantic_contract_server,
        ),
    ]
    reports = []
    for contract_id, path, server in wanted:
        result = services.tester.test(path, server, contract_id)
        services.publisher.publish(
            QualityReport(
                run_id=run_id,
                published_at=datetime.now(UTC),
                contract_id=result.contract_id,
                passed=result.passed,
                failed_checks=result.failed_checks,
            )
        )
        reports.append(
            {
                "contract_id": result.contract_id,
                "passed": result.passed,
                "failed_checks": result.failed_checks,
            }
        )

    passed = all(report["passed"] for report in reports)
    if passed and settings.contract_test_healthcheck_url:
        services.ping(settings.contract_test_healthcheck_url)
    return {"run_id": run_id, "passed": passed, "contracts": reports}


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

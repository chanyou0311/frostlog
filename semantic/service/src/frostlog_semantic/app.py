"""The Cloud Run service: two endpoints, both of them jobs rather than queries.

``POST /events/gcs`` is what Eventarc calls when a chunk lands in the raw bucket:
the chunk goes into its BigQuery raw table, dbt rebuilds the JST dates it touched
and a ``semantic_updated`` event says what changed. Failing with 500 is how the
service asks Eventarc to deliver again, and a run that fails publishes nothing —
consumers hear about a build only once it stands.

``POST /jobs/contract-test`` is what Cloud Scheduler calls once a day: both data
contracts are tested against production, each result is published as a
``quality_report`` event, and a clean run pings the dead man's switch. It always
answers 200 — a failing contract, and a contract that could not be tested at all,
are findings rather than broken requests.

Neither endpoint authenticates: only Cloud Run IAM (OIDC) may call them.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException

from frostlog_semantic import healthcheck, raw_objects
from frostlog_semantic.contracts import (
    ContractTester,
    ContractTestResult,
    DatacontractTester,
    s3_credentials,
)
from frostlog_semantic.events import (
    NoPublisher,
    Publisher,
    PubSubPublisher,
    QualityReport,
    SemanticUpdated,
)
from frostlog_semantic.secret_manager import NoSecrets, SecretManagerReader, SecretReader
from frostlog_semantic.settings import Settings, resolve_project, topic_path
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
    secrets: SecretReader = field(default_factory=NoSecrets)
    ping: Callable[[str], None] = healthcheck.ping


def build_services(settings: Settings | None = None) -> Services:
    """The production wiring: BigQuery, Cloud Storage, Pub/Sub and Secret Manager through ADC."""
    from google.cloud import bigquery, pubsub_v1, secretmanager, storage

    settings = settings or Settings()
    project = resolve_project(settings.gcp_project)
    environment = {
        "FROSTLOG_BQ_PROJECT": project,
        "FROSTLOG_BQ_DATASET": settings.bq_dataset,
        "FROSTLOG_BQ_RAW_DATASET": settings.raw_dataset,
        "FROSTLOG_BQ_LOCATION": settings.bq_location,
    }
    client = bigquery.Client(project=project)
    topic = settings.semantic_updated_topic
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
        tester=DatacontractTester(environment),
        secrets=SecretManagerReader(secretmanager.SecretManagerServiceClient(), project),
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

    raw_bucket = services.settings.raw_bucket
    if raw_bucket and bucket != raw_bucket:
        log.info("%s: not the raw bucket, ignored", bucket)
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

    # Only an events chunk carries upload runs, and it always arrives after every
    # cooler chunk of the same return: that is what lets a consumer summarize one.
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


def contract_test(services: Services) -> dict[str, Any]:
    """Test both contracts against production and report what failed."""
    settings = services.settings
    run_id = uuid4().hex
    wanted = [
        (
            "frostlog-raw",
            settings.contracts_dir / "raw.odcs.yaml",
            settings.raw_contract_server,
            _raw_test_environment(services),
        ),
        (
            "frostlog-semantic",
            settings.contracts_dir / "semantic.odcs.yaml",
            settings.semantic_contract_server,
            {},
        ),
    ]
    reports = []
    for contract_id, path, server, environment in wanted:
        result = _test_contract(services, contract_id, path, server, environment)
        _publish_report(services, run_id, result)
        reports.append(
            {
                "contract_id": result.contract_id,
                "passed": result.passed,
                "failed_checks": result.failed_checks,
            }
        )

    passed = all(report["passed"] for report in reports)
    if passed:
        _ping(services)
    return {"run_id": run_id, "passed": passed, "contracts": reports}


def _test_contract(
    services: Services,
    contract_id: str,
    path: Path,
    server: str,
    environment: dict[str, str] | None,
) -> ContractTestResult:
    """One contract's result, whatever happens: a crash is a finding, not a 500."""
    if environment is None:
        return ContractTestResult(
            contract_id, passed=False, failed_checks=["not tested: no raw HMAC secret"]
        )
    try:
        return services.tester.test(path, server, contract_id, environment)
    except Exception as exc:  # a timeout, a missing binary, anything
        log.exception("testing %s failed", contract_id)
        return ContractTestResult(
            contract_id, passed=False, failed_checks=[f"not tested: {type(exc).__name__}"]
        )


def _publish_report(services: Services, run_id: str, result: ContractTestResult) -> None:
    try:
        services.publisher.publish(
            QualityReport(
                run_id=run_id,
                published_at=datetime.now(UTC),
                contract_id=result.contract_id,
                passed=result.passed,
                failed_checks=result.failed_checks,
            )
        )
    except Exception:
        log.exception("publishing the quality report for %s failed", result.contract_id)


def _raw_test_environment(services: Services) -> dict[str, str] | None:
    """The HMAC key datacontract-cli needs for the bucket; ``None`` means: cannot test."""
    name = services.settings.raw_hmac_secret
    if not name:
        log.info("no raw HMAC secret configured; the raw contract is not tested")
        return None
    payload = services.secrets.read(name)
    if payload is None:
        return None
    return s3_credentials(payload)


def _ping(services: Services) -> None:
    """Report a clean run to the dead man's switch, if we know where it is."""
    settings = services.settings
    url = settings.contract_test_healthcheck_url
    if not url and settings.contract_test_healthcheck_url_secret:
        url = services.secrets.read(settings.contract_test_healthcheck_url_secret)
    if not url:
        log.info("no healthcheck URL available; the clean run is not reported")
        return
    try:
        services.ping(url)
    except Exception:
        log.exception("pinging the dead man's switch failed")


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

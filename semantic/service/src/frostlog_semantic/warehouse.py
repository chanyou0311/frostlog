"""Getting one raw chunk into its BigQuery table, exactly once.

Eventarc delivers at least once, so the same chunk arrives again after any
failure and the second run must leave the table as the first one did. The chunk
also has to gain two columns that are not in its bytes (which object it came
from, and when it was uploaded), and a load job cannot add a constant column.

So the chunk is loaded into a staging table named after the object and then
appended to the raw table by a query job whose id is derived from the object
name. The append is the only statement that touches the raw table, and BigQuery
refuses a second job with the same id: a redelivery therefore reloads the
staging table (harmless, it is truncated) and has its append rejected as already
done. Job ids are remembered for six months, far longer than any retry.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from google.api_core.exceptions import Conflict, NotFound
from google.cloud import bigquery

from frostlog_semantic import raw_schema
from frostlog_semantic.raw_objects import RawObject

log = logging.getLogger(__name__)

#: Staging tables are dropped after use; the expiry only covers a crash in between.
STAGE_EXPIRY = timedelta(hours=24)


@dataclass(frozen=True)
class LoadResult:
    table: str
    rows: int
    #: True when the append job already existed, i.e. this delivery was a repeat.
    already_loaded: bool


class Warehouse(Protocol):
    def load(self, chunk: RawObject, uploaded_at: datetime) -> LoadResult: ...


def append_job_id(object_name: str) -> str:
    """Id of the job that appends ``object_name`` to its raw table."""
    return f"load-{_digest(object_name)}"


def stage_table_name(chunk: RawObject) -> str:
    """Name of the staging table for a chunk; deterministic, so a retry reuses it."""
    return f"stage_{chunk.table}_{_digest(chunk.name)[:24]}"


def _digest(object_name: str) -> str:
    return hashlib.sha1(object_name.encode(), usedforsecurity=False).hexdigest()


class BigQueryWarehouse:
    """:class:`Warehouse` over google-cloud-bigquery."""

    def __init__(self, client: bigquery.Client, project: str, dataset: str, location: str) -> None:
        self._client = client
        self._project = project
        self._dataset = dataset
        self._location = location

    def load(self, chunk: RawObject, uploaded_at: datetime) -> LoadResult:
        schema = raw_schema.SCHEMAS[chunk.table]
        target = self._ensure_table(chunk.table, schema)
        stage = f"{self._project}.{self._dataset}.{stage_table_name(chunk)}"
        try:
            rows = self._stage(stage, chunk)
            already_loaded = self._append(target, stage, chunk, uploaded_at, schema)
        finally:
            self._client.delete_table(stage, not_found_ok=True)
        return LoadResult(table=chunk.table, rows=rows, already_loaded=already_loaded)

    def _ensure_table(self, name: str, schema: list[bigquery.SchemaField]) -> str:
        """Create the raw table if it is not there yet; return its full name."""
        full_name = f"{self._project}.{self._dataset}.{name}"
        table = bigquery.Table(full_name, schema=schema)
        table.time_partitioning = bigquery.TimePartitioning(field="ts")
        table.clustering_fields = ["boot_id"]
        self._client.create_table(table, exists_ok=True)
        return full_name

    def _stage(self, stage: str, chunk: RawObject) -> int:
        schema = raw_schema.loaded_schema(chunk.table)
        table = bigquery.Table(stage, schema=schema)
        table.expires = datetime.now(UTC) + STAGE_EXPIRY
        self._client.create_table(table, exists_ok=True)
        config = bigquery.LoadJobConfig(
            schema=schema,
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            ignore_unknown_values=True,
        )
        job = self._client.load_table_from_uri(
            chunk.uri, stage, job_config=config, location=self._location
        )
        job.result()
        return int(job.output_rows or 0)

    def _append(
        self,
        target: str,
        stage: str,
        chunk: RawObject,
        uploaded_at: datetime,
        schema: list[bigquery.SchemaField],
    ) -> bool:
        """Append the staged rows to the raw table. True when it had already been done."""
        job_id = append_job_id(chunk.name)
        config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("source_key", "STRING", chunk.name),
                bigquery.ScalarQueryParameter("uploaded_at", "TIMESTAMP", uploaded_at),
            ]
        )
        try:
            job = self._client.query(
                append_sql(target, stage, schema),
                job_config=config,
                job_id=job_id,
                location=self._location,
                job_retry=None,
            )
        except Conflict:
            log.info("%s: already appended to %s", chunk.name, target)
            self._client.get_job(job_id, location=self._location).result()
            return True
        job.result()
        return False


def append_sql(target: str, stage: str, schema: list[bigquery.SchemaField]) -> str:
    """``INSERT`` that copies the staged chunk into the raw table, adding its two columns."""
    parameters = {"source_key": "@source_key", "uploaded_at": "@uploaded_at"}
    columns = [field.name for field in schema]
    selected = [parameters.get(name, f"`{name}`") + f" AS `{name}`" for name in columns]
    return (
        f"INSERT INTO `{target}` ({', '.join(f'`{name}`' for name in columns)})\n"
        f"SELECT {', '.join(selected)} FROM `{stage}`"
    )


class ObjectMetadata(Protocol):
    def uploaded_at(self, chunk: RawObject) -> datetime | None: ...


class StorageObjectMetadata:
    """The chunk's ``uploaded-at`` metadata, read with google-cloud-storage."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def uploaded_at(self, chunk: RawObject) -> datetime | None:
        try:
            blob = self._client.bucket(chunk.bucket).get_blob(chunk.name)
        except NotFound:
            return None
        if blob is None:
            return None
        recorded = (blob.metadata or {}).get("uploaded-at")
        if recorded is None:
            return None
        try:
            return datetime.fromisoformat(recorded)
        except ValueError:
            log.warning("%s: unreadable uploaded-at metadata %r", chunk.name, recorded)
            return None

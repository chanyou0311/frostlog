"""Getting one raw chunk into its BigQuery table, exactly once.

Eventarc delivers at least once, so the same chunk arrives again after any
failure and the second run must leave the table as the first one did. The chunk
also has to gain two columns that are not in its bytes (which object it came
from, and when it was uploaded), and a load job cannot add a constant column.

So the chunk is loaded into a staging table named after the object and then
appended to the raw table by a query job whose id is derived from the object
name. The append is the only statement that touches the raw table, and BigQuery
refuses a second job with the same id, so a redelivery reloads the staging table
(harmless, it is truncated) and finds its append already done. Job ids are
remembered for six months, far longer than any retry.

A job id that already exists is not proof that the append succeeded: the first
attempt may have failed. Its outcome is therefore inspected — finished cleanly,
still running, or finished with an error — and only a clean finish counts as
done; a failed attempt is retried under a suffixed id, so a single bad job can
never poison a chunk forever. The staging table survives until the append has
succeeded, because the retry needs it.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from google.api_core.exceptions import Conflict, GoogleAPIError, NotFound
from google.cloud import bigquery

from frostlog_semantics import raw_schema
from frostlog_semantics.events import UploadRun
from frostlog_semantics.raw_objects import RawObject, target_dates

log = logging.getLogger(__name__)

#: Staging tables are dropped after use; the expiry only covers a crash in between.
STAGE_EXPIRY = timedelta(hours=24)

#: How many append job ids one chunk may burn before we give up and fail the request.
MAX_APPEND_ATTEMPTS = 5


@dataclass(frozen=True)
class LoadResult:
    table: str
    rows: int
    #: True when the append job had already run successfully, i.e. this was a repeat.
    already_loaded: bool


class Warehouse(Protocol):
    def load(self, chunk: RawObject, uploaded_at: datetime) -> LoadResult: ...

    def arrived_dates(self, since: datetime) -> list[date]: ...

    def upload_runs(self, since: datetime) -> list[UploadRun]: ...


def append_job_id(object_name: str, attempt: int = 1) -> str:
    """Id of the job that appends ``object_name`` to its raw table.

    The first attempt owns the plain id; a later one gets a suffix, so a failed
    job never blocks the chunk (BigQuery keeps job ids, not their outcome).
    """
    base = f"load-{_digest(object_name)}"
    return base if attempt == 1 else f"{base}-{attempt}"


def stage_table_name(table: str, object_name: str) -> str:
    """Name of the staging table for a chunk; deterministic, so a retry reuses it."""
    return f"stage_{table}_{_digest(object_name)[:24]}"


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
        """A chunk from the raw bucket, loaded once however often it is delivered."""
        return self._load(chunk.table, chunk.name, uploaded_at, uri=chunk.uri)

    def load_file(
        self, path: Path, table: str, source_key: str, uploaded_at: datetime
    ) -> LoadResult:
        """A local NDJSON file through the same path, for the CI warehouse check.

        Nothing redelivers a local file, so the append gets a fresh job id and the
        table can be rebuilt from the samples as often as one likes.
        """
        return self._load(table, source_key, uploaded_at, path=path, idempotent=False)

    def _load(
        self,
        table: str,
        source_key: str,
        uploaded_at: datetime,
        *,
        uri: str | None = None,
        path: Path | None = None,
        idempotent: bool = True,
    ) -> LoadResult:
        schema = raw_schema.SCHEMAS[table]
        target = self._ensure_table(table, schema)
        stage = self._table(stage_table_name(table, source_key))
        rows = self._stage(stage, table, uri=uri, path=path)
        already_loaded = self._append(
            target, stage, source_key, uploaded_at, schema, idempotent=idempotent
        )
        self._client.delete_table(stage, not_found_ok=True)
        return LoadResult(table=table, rows=rows, already_loaded=already_loaded)

    def reset_table(self, table: str) -> None:
        """Drop a raw table and create it empty (the CI dataset is rebuilt, not added to)."""
        self._client.delete_table(self._table(table), not_found_ok=True)
        self._ensure_table(table, raw_schema.SCHEMAS[table])

    def arrived_dates(self, since: datetime) -> list[date]:
        """The JST dates covered by chunk names that arrived since ``since``.

        A chunk's own day is in its name, and what is in it can fall on that JST date
        or the next one, so both travel on the event. They decide whether a build is
        due but do not limit its scope. Reading the name rather than the rows keeps
        this to the two columns the question needs.
        """
        config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("since", "TIMESTAMP", since)]
        )
        job = self._client.query(
            arrived_dates_sql(self._table("raw_cooler"), self._table("raw_events")),
            job_config=config,
            location=self._location,
        )
        days: set[date] = set()
        for row in job.result():
            days.update(target_dates(row["dt"]))
        return sorted(days)

    def upload_runs(self, since: datetime) -> list[UploadRun]:
        """The collector's upload runs reported finished by chunks that arrived since."""
        config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("since", "TIMESTAMP", since)]
        )
        job = self._client.query(
            upload_runs_sql(self._table("raw_events")),
            job_config=config,
            location=self._location,
        )
        return [
            UploadRun(
                finished_at=row["finished_at"],
                started_at=row["started_at"],
                previous_finished_at=row["previous_finished_at"],
                chunk_count=row["chunk_count"],
                line_count=row["line_count"],
            )
            for row in job.result()
        ]

    def _table(self, name: str) -> str:
        return f"{self._project}.{self._dataset}.{name}"

    def _ensure_table(self, name: str, schema: list[bigquery.SchemaField]) -> str:
        """Create the raw table if it is not there yet; return its full name."""
        full_name = self._table(name)
        table = bigquery.Table(full_name, schema=schema)
        table.time_partitioning = bigquery.TimePartitioning(field="ts")
        table.clustering_fields = ["boot_id"]
        self._client.create_table(table, exists_ok=True)
        return full_name

    def _stage(
        self, stage: str, table: str, uri: str | None = None, path: Path | None = None
    ) -> int:
        """Put the chunk's lines in a staging table, replacing whatever was there."""
        schema = raw_schema.loaded_schema(table)
        staging = bigquery.Table(stage, schema=schema)
        staging.expires = datetime.now(UTC) + STAGE_EXPIRY
        self._client.create_table(staging, exists_ok=True)
        config = bigquery.LoadJobConfig(
            schema=schema,
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            ignore_unknown_values=True,
        )
        if uri is not None:
            job = self._client.load_table_from_uri(
                uri, stage, job_config=config, location=self._location
            )
        else:
            assert path is not None
            with path.open("rb") as lines:
                job = self._client.load_table_from_file(
                    lines, stage, job_config=config, location=self._location
                )
        job.result()
        return int(job.output_rows or 0)

    def _append(
        self,
        target: str,
        stage: str,
        source_key: str,
        uploaded_at: datetime,
        schema: list[bigquery.SchemaField],
        idempotent: bool = True,
    ) -> bool:
        """Append the staged rows to the raw table. True when it had already been done."""
        config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("source_key", "STRING", source_key),
                bigquery.ScalarQueryParameter("uploaded_at", "TIMESTAMP", uploaded_at),
            ]
        )
        sql = append_sql(target, stage, schema)
        if not idempotent:
            self._client.query(sql, job_config=config, location=self._location).result()
            return False

        for attempt in range(1, MAX_APPEND_ATTEMPTS + 1):
            job_id = append_job_id(source_key, attempt)
            try:
                job = self._client.query(
                    sql,
                    job_config=config,
                    job_id=job_id,
                    location=self._location,
                    job_retry=None,
                )
            except Conflict:
                if self._succeeded(job_id, source_key):
                    return True
                continue
            job.result()
            return False
        raise RuntimeError(f"{source_key}: {MAX_APPEND_ATTEMPTS} append attempts all failed")

    def _succeeded(self, job_id: str, source_key: str) -> bool:
        """Whether the append job that already holds ``job_id`` did the work.

        A job still running is waited for — a redelivery overtaking the first
        attempt must not append the same rows twice.
        """
        job = self._client.get_job(job_id, location=self._location)
        if job.state != "DONE":
            try:
                job.result()
            except GoogleAPIError as exc:
                log.warning("%s: append job %s failed while running (%s)", source_key, job_id, exc)
                return False
            log.info("%s: append job %s was already in flight", source_key, job_id)
            return True
        if job.error_result is None:
            log.info("%s: already appended by job %s", source_key, job_id)
            return True
        log.warning(
            "%s: append job %s had failed (%s); retrying under a new id",
            source_key,
            job_id,
            job.error_result.get("reason", "unknown"),
        )
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


def arrived_dates_sql(cooler_table: str, events_table: str) -> str:
    """The days of the chunks loaded since a moment, from both streams.

    Both, because the chunk that anchors a boot's clock is often an event while the
    reports it moves are cooler rows shipped under the date the wrong clock said.
    """
    return f"""
SELECT DISTINCT dt FROM (
  SELECT DATE(REGEXP_EXTRACT(source_key, r'dt=(\\d{{4}}-\\d{{2}}-\\d{{2}})')) AS dt
  FROM `{cooler_table}` WHERE uploaded_at >= @since
  UNION ALL
  SELECT DATE(REGEXP_EXTRACT(source_key, r'dt=(\\d{{4}}-\\d{{2}}-\\d{{2}})')) AS dt
  FROM `{events_table}` WHERE uploaded_at >= @since
)
WHERE dt IS NOT NULL
ORDER BY dt
"""


def upload_runs_sql(events_table: str) -> str:
    """The upload runs the chunks loaded since a moment reported, and what preceded each.

    ``started_at`` is the run's own start (same boot); ``previous_finished_at`` is
    the end of the run before it, whichever boot that was — the gap between the two
    is how long the collector was away from the home network. Both look across the
    whole table, because what came before a run is not restricted to this window.
    """
    return f"""
WITH finished AS (
  SELECT boot_id, ts, uploaded_chunk_count, uploaded_line_count
  FROM `{events_table}`
  WHERE kind = 'upload_done' AND uploaded_at >= @since
)
SELECT
  d.ts AS finished_at,
  (SELECT MAX(s.ts) FROM `{events_table}` s
    WHERE s.kind = 'upload_started' AND s.boot_id = d.boot_id AND s.ts < d.ts) AS started_at,
  (SELECT MAX(f.ts) FROM `{events_table}` f
    WHERE f.kind = 'upload_done' AND f.ts < d.ts) AS previous_finished_at,
  COALESCE(d.uploaded_chunk_count, 0) AS chunk_count,
  COALESCE(d.uploaded_line_count, 0) AS line_count
FROM finished d
ORDER BY d.ts
"""


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

"""What the transform asks the warehouse, and how CI puts samples in it.

Nothing here loads the bucket any more. A BigQuery Data Transfer Service config does
that, every fifteen minutes, with no code of ours in the path: the chunks are
newline-delimited JSON whose fields are the table's, so a load job needs nothing
added to them, and a load job is not billed.

That leaves no arrival timestamp of our own on the row -- a transfer names every
column of the destination in its load job, and BigQuery writes NULL rather than a
default for any column a load job names. So whether a build is due is asked of the tables rather
than of the rows, by counting them. `tables.get` is metadata: an API call, not a
query, so it costs nothing at all.

It is the row count and not the last-modified time, because a transfer that finds
nothing still touches the table and moves that timestamp; raw is append-only, so a
count that has not changed means nothing arrived.

The arrival time itself is not gone, only moved out of reach of a load job: the raw
tables partition on ingestion time, so ``_PARTITIONTIME`` says when a row landed. It
is a pseudo-column, which is exactly why it survives -- a load job cannot name it,
so it cannot null it. The contract reads it to give a row that has arrived time to
be built before calling it missing.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from google.cloud import bigquery

from frostlog_semantics import raw_schema
from frostlog_semantics.events import UploadRun

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoadResult:
    table: str
    rows: int


@dataclass(frozen=True)
class Arrivals:
    """How much arrived since the last build, and where each raw table now stands."""

    rows: int
    counts: dict[str, int]


class Warehouse(Protocol):
    def arrivals(self, built_through: Mapping[str, int]) -> Arrivals: ...

    def upload_runs(self, since: datetime) -> list[UploadRun]: ...


class BigQueryWarehouse:
    """:class:`Warehouse` over google-cloud-bigquery."""

    def __init__(self, client: bigquery.Client, project: str, dataset: str, location: str) -> None:
        self._client = client
        self._project = project
        self._dataset = dataset
        self._location = location

    def load_file(self, path: Path, table: str) -> LoadResult:
        """A local NDJSON file into a raw table, for the CI warehouse check.

        In production a transfer does this, from the bucket, with nothing of ours in
        between. CI has no transfer and no bucket to point one at, so it issues the
        same kind of job by hand: same schema, same format, same disposition.
        """
        target = self._table(table)
        config = bigquery.LoadJobConfig(
            schema=raw_schema.SCHEMAS[table],
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            ignore_unknown_values=True,
        )
        with path.open("rb") as lines:
            job = self._client.load_table_from_file(
                lines, target, job_config=config, location=self._location
            )
        job.result()
        return LoadResult(table=table, rows=int(job.output_rows or 0))

    def reset_table(self, table: str) -> None:
        """Drop a raw table and create it empty (the CI dataset is rebuilt, not added to)."""
        self._client.delete_table(self._table(table), not_found_ok=True)
        self.ensure_table(table)

    def ensure_table(self, table: str) -> str:
        """The raw table, created from the contract's schema if it is not there yet.

        A load job writes to a table but does not make one. In production the tables
        are made once, outside this repository, and then only written by the
        transfer; here it is the sample loader that calls it, on every rebuild.

        The partitioning is on ingestion time rather than on ``ts``. Neither costs
        less at this size, but only ingestion time answers "when did this land", and
        it answers it as ``_PARTITIONTIME`` -- a pseudo-column, which no load job can
        name and therefore none can null. ``ts`` is when the cooler spoke, which for
        a trip's backlog is days before the row reached the bucket.
        """
        name = self._table(table)
        wanted = bigquery.Table(name, schema=raw_schema.SCHEMAS[table])
        wanted.time_partitioning = bigquery.TimePartitioning()
        wanted.clustering_fields = ["boot_id"]
        self._client.create_table(wanted, exists_ok=True)
        return name

    def arrivals(self, built_through: Mapping[str, int]) -> Arrivals:
        """What raw holds now, against what a build has already been given.

        ``built_through`` is the count each raw table stood at when the last build
        ran. Anything above it arrived since. Reading a table's metadata is free, so
        this is asked every time the scheduler calls without costing a query.

        A table holding fewer rows than the mark was rebuilt under us -- raw only
        ever grows otherwise. Then the mark says nothing about what is in the table
        now, so every row in it counts as unbuilt. Clamping the difference at zero
        instead would leave the mark stranded above the count and the transform idle
        until the table grew past its old size, which after a rebuild is never.
        """
        counts = {table: self._rows(table) for table in raw_schema.SCHEMAS}
        fresh = sum(
            rows if rows < built_through.get(table, 0) else rows - built_through.get(table, 0)
            for table, rows in counts.items()
        )
        return Arrivals(rows=fresh, counts=counts)

    def _rows(self, table: str) -> int:
        """How many rows a raw table holds, from its metadata."""
        return int(self._client.get_table(self._table(table)).num_rows or 0)

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


def upload_runs_sql(events_table: str) -> str:
    """The upload runs that arrived since a moment, and what preceded each.

    The window is on arrival, and arrival is ``_PARTITIONDATE``: the Pi's own clock
    is whatever the hardware clock kept while the power was off, until NTP catches
    up, and the run that ends just after the car comes home is exactly the run whose
    clock is least trustworthy. Reading the window on ``ts`` would drop it -- the
    homecoming would be announced by nothing -- and that run is the one a consumer
    most wants. Ingestion time cannot be off, because nothing but the load writes it.

    It is a date and not a timestamp, so the window is rounded outwards to the day.
    A run reported twice is the consumer's to settle (it deduplicates on
    ``finished_at``), and a run never reported is not.

    ``started_at`` is the run's own start (same boot); ``previous_finished_at`` is
    the end of the run before it, whichever boot that was — the gap between the two
    is how long the collector was away from the home network. Both look across the
    whole table, because what came before a run is not restricted to this window.

    ``ts IS NOT NULL`` is not defensive: ``finished_at`` is required downstream, and
    a row without one would fail validation on every retry until it left the window.
    """
    return f"""
WITH finished AS (
  SELECT boot_id, ts, uploaded_chunk_count, uploaded_line_count
  FROM `{events_table}`
  WHERE kind = 'upload_done' AND ts IS NOT NULL AND _PARTITIONDATE >= DATE(@since)
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

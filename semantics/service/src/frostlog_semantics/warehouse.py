"""What the transform asks the warehouse, and how CI puts samples in it.

Nothing here loads the bucket any more. A BigQuery Data Transfer Service config
does that, on its own schedule, with no code of ours in the path: the chunks are
newline-delimited JSON whose fields are the table's, so a load job needs nothing
added to them. The one column that is not in the bytes, ``_loaded_at``, carries a
default expression the load evaluates (see raw_schema).

That is why what is left is small. The transform asks which days the rows that
arrived lately fall on, and what the collector said about its upload runs; the
sample loader puts a file in a table directly, which is the one place a load job
is still issued from Python.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime
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
    """What a window of loading brought in."""

    rows: int
    days: list[date]


class Warehouse(Protocol):
    def arrivals(self, since: datetime) -> Arrivals: ...

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
        same kind of job by hand: same schema, same format, same defaults evaluated
        for ``_loaded_at``.
        """
        target = self._table(table)
        config = bigquery.LoadJobConfig(
            # The file's fields, not the table's: a column the load job names is one
            # BigQuery looks for in the data and writes NULL for when it is absent,
            # default or no default. Leaving _loaded_at out is what lets the default
            # be what fills it.
            schema=raw_schema.chunk_schema(table),
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
        """
        name = self._table(table)
        wanted = bigquery.Table(name, schema=raw_schema.SCHEMAS[table])
        wanted.time_partitioning = bigquery.TimePartitioning(field="ts")
        wanted.clustering_fields = ["boot_id"]
        self._client.create_table(wanted, exists_ok=True)
        return name

    def arrivals(self, since: datetime) -> Arrivals:
        """What the warehouse loaded since ``since``: how much, and on which days.

        The count decides whether a build is due; the days travel on the event that
        announces it. Neither limits what a build rebuilds, which is everything. The
        question is asked of ``_loaded_at`` rather than of the object names, because
        after the move to a transfer there is no object name on the row to ask.
        """
        config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("since", "TIMESTAMP", since)]
        )
        job = self._client.query(
            arrived_dates_sql(self._table("raw_cooler"), self._table("raw_events")),
            job_config=config,
            location=self._location,
        )
        row = next(iter(job.result()))
        return Arrivals(rows=row["rows_loaded"], days=list(row["days"]))

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


def arrived_dates_sql(cooler_table: str, events_table: str) -> str:
    """How many rows were loaded since a moment, and which JST days they fall on.

    Two questions in one answer, because they are two questions. Whether a build is
    due is about rows arriving at all; which days to announce is about what those
    rows say. A row the collector could not stamp has no day but is still an arrival,
    and a run that took it for nothing would leave the build undone until something
    stampable happened to turn up.

    Both streams, because either can be what arrived.
    """
    return f"""
WITH arrived AS (
  SELECT ts FROM `{cooler_table}` WHERE _loaded_at >= @since
  UNION ALL
  SELECT ts FROM `{events_table}` WHERE _loaded_at >= @since
)
SELECT
  (SELECT COUNT(*) FROM arrived) AS rows_loaded,
  ARRAY(
    SELECT DISTINCT DATE(ts, 'Asia/Tokyo')
    FROM arrived WHERE ts IS NOT NULL ORDER BY 1
  ) AS days
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
  WHERE kind = 'upload_done' AND _loaded_at >= @since
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

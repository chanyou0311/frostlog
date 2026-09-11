"""How a chunk gets into BigQuery, and what happens when it arrives twice."""

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from google.api_core.exceptions import BadRequest, Conflict
from google.cloud import bigquery

from frostlog_semantics import raw_objects, raw_schema, warehouse

UPLOADED_AT = datetime(2026, 9, 6, 12, tzinfo=UTC)


def _chunk(name: str = "v1/cooler/dt=2026-09-06/000000122880.jsonl.gz") -> raw_objects.RawObject:
    chunk = raw_objects.parse("chanyou-frostlog-collection", name)
    assert chunk is not None
    return chunk


def test_the_same_object_always_gets_the_same_job_id() -> None:
    # This is what makes a redelivery a no-op: BigQuery refuses the second job.
    first = warehouse.append_job_id(_chunk().name)
    assert first == warehouse.append_job_id(_chunk().name)
    assert first.startswith("load-")


def test_a_different_object_gets_a_different_job_id() -> None:
    other = "v1/cooler/dt=2026-09-06/000000245760.jsonl.gz"
    assert warehouse.append_job_id(_chunk().name) != warehouse.append_job_id(other)


def test_a_later_attempt_gets_its_own_job_id() -> None:
    name = _chunk().name
    assert warehouse.append_job_id(name, 2) == f"{warehouse.append_job_id(name)}-2"


def test_a_job_id_is_within_the_length_bigquery_allows() -> None:
    assert len(warehouse.append_job_id(_chunk().name)) <= 1024


def test_the_staging_table_is_named_after_the_object_and_its_stream() -> None:
    chunk = _chunk()
    name = warehouse.stage_table_name(chunk.table, chunk.name)
    assert name.startswith("stage_raw_cooler_")
    assert name == warehouse.stage_table_name(chunk.table, chunk.name)
    assert name.replace("_", "").isalnum()


def test_the_append_copies_every_column_and_adds_the_two_of_the_chunk() -> None:
    sql = warehouse.append_sql("p.d.raw_cooler", "p.d.stage", raw_schema.RAW_COOLER)
    for field in raw_schema.RAW_COOLER:
        assert f"`{field.name}`" in sql
    assert "@source_key AS `source_key`" in sql
    assert "@uploaded_at AS `uploaded_at`" in sql
    # The chunk columns are parameters, never read from the staged rows.
    assert "`source_key` FROM" not in sql


def test_the_staged_schema_leaves_out_what_the_chunk_does_not_carry() -> None:
    staged = [field.name for field in raw_schema.loaded_schema("raw_cooler")]
    assert "source_key" not in staged
    assert "uploaded_at" not in staged
    assert "payload" in staged


def test_the_upload_run_query_reads_one_chunk_and_the_whole_stream() -> None:
    sql = warehouse.upload_runs_sql("p.d.raw_events")
    # The runs reported by this chunk...
    assert "source_key = @source_key" in sql
    # ...each with its own start and with the end of whatever ran before it.
    assert "s.kind = 'upload_started' AND s.boot_id = d.boot_id AND s.ts < d.ts" in sql
    assert "f.kind = 'upload_done' AND f.ts < d.ts" in sql


class FakeJob:
    """A BigQuery job as the warehouse looks at it: a state, an error, a result."""

    def __init__(self, rows: int = 0, error: dict[str, str] | None = None, state: str = "DONE"):
        self.output_rows = rows
        self.error_result = error
        self.state = state
        self.awaited = 0

    def result(self) -> list[Any]:
        self.awaited += 1
        if self.error_result is not None:
            raise BadRequest(self.error_result.get("message", "job failed"))
        return []


class FakeBigQuery:
    """Enough of google-cloud-bigquery to run a load and see what it did."""

    def __init__(self, rows: int = 3) -> None:
        self.rows = rows
        self.created: list[str] = []
        self.deleted: list[str] = []
        self.jobs: dict[str, FakeJob] = {}
        self.appends: list[str] = []
        #: Job ids whose query fails when this client runs it.
        self.failing: set[str] = set()

    def create_table(self, table: Any, exists_ok: bool = False) -> Any:
        self.created.append(f"{table.project}.{table.dataset_id}.{table.table_id}")
        return table

    def delete_table(self, table: str, not_found_ok: bool = False) -> None:
        self.deleted.append(table)

    def load_table_from_uri(self, uri: str, table: str, **kwargs: Any) -> FakeJob:
        return FakeJob(rows=self.rows)

    def load_table_from_file(self, lines: Any, table: str, **kwargs: Any) -> FakeJob:
        return FakeJob(rows=self.rows)

    def query(self, sql: str, job_id: str | None = None, **kwargs: Any) -> FakeJob:
        if job_id is not None and job_id in self.jobs:
            raise Conflict(f"Already Exists: Job {job_id}")
        job = FakeJob(error={"reason": "invalid"} if job_id in self.failing else None)
        if job_id is not None:
            self.jobs[job_id] = job
        self.appends.append(job_id or "anonymous")
        return job

    def get_job(self, job_id: str, location: str | None = None) -> FakeJob:
        return self.jobs[job_id]


def _warehouse(client: FakeBigQuery) -> warehouse.BigQueryWarehouse:
    return warehouse.BigQueryWarehouse(
        cast(bigquery.Client, client), "chanyou-frostlog", "frostlog", "us-central1"
    )


def test_a_fresh_chunk_is_staged_appended_and_the_stage_dropped() -> None:
    client = FakeBigQuery()
    chunk = _chunk()

    result = _warehouse(client).load(chunk, UPLOADED_AT)

    assert result == warehouse.LoadResult("raw_cooler", 3, already_loaded=False)
    assert client.appends == [warehouse.append_job_id(chunk.name)]
    assert client.deleted == [
        f"chanyou-frostlog.frostlog.{warehouse.stage_table_name('raw_cooler', chunk.name)}"
    ]


def test_a_redelivery_after_a_successful_append_does_nothing_twice() -> None:
    client = FakeBigQuery()
    chunk = _chunk()
    client.jobs[warehouse.append_job_id(chunk.name)] = FakeJob()

    result = _warehouse(client).load(chunk, UPLOADED_AT)

    assert result.already_loaded is True
    assert client.appends == []


def test_an_append_that_failed_is_retried_under_a_new_id() -> None:
    # The old behaviour re-raised the failed job's error on every redelivery, so
    # the chunk could never be loaded again.
    client = FakeBigQuery()
    chunk = _chunk()
    client.jobs[warehouse.append_job_id(chunk.name)] = FakeJob(error={"reason": "invalid"})

    result = _warehouse(client).load(chunk, UPLOADED_AT)

    assert result.already_loaded is False
    assert client.appends == [warehouse.append_job_id(chunk.name, 2)]


def test_an_append_still_running_is_waited_for() -> None:
    client = FakeBigQuery()
    chunk = _chunk()
    running = FakeJob(state="RUNNING")
    client.jobs[warehouse.append_job_id(chunk.name)] = running

    result = _warehouse(client).load(chunk, UPLOADED_AT)

    assert result.already_loaded is True
    assert running.awaited == 1
    assert client.appends == []


def test_the_stage_survives_a_failed_append_so_the_retry_can_use_it() -> None:
    client = FakeBigQuery()
    chunk = _chunk()
    client.failing.add(warehouse.append_job_id(chunk.name))

    with pytest.raises(BadRequest):
        _warehouse(client).load(chunk, UPLOADED_AT)

    assert client.deleted == []


def test_a_local_sample_file_takes_the_same_path(tmp_path: Any) -> None:
    client = FakeBigQuery()
    path = tmp_path / "2026-09-06.json"
    path.write_text('{"ts": "2026-09-06T00:00:00Z"}\n')

    result = _warehouse(client).load_file(path, "raw_events", "samples/events", UPLOADED_AT)

    assert result.table == "raw_events"
    # No job id: nothing redelivers a local file, and CI reloads the samples often.
    assert client.appends == ["anonymous"]

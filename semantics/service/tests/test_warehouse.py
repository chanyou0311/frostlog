"""The questions the transform asks the warehouse, and how CI puts samples in it.

Loading the bucket is not tested here because this repository no longer does it: a
BigQuery Data Transfer Service config reads the chunks straight into raw. What is
left to get wrong is the SQL, so that is what these read.
"""

from pathlib import Path
from typing import Any

from frostlog_semantics import raw_schema
from frostlog_semantics.warehouse import (
    BigQueryWarehouse,
    arrived_dates_sql,
    upload_runs_sql,
)


def test_the_arrived_dates_query_asks_both_streams_what_landed_lately() -> None:
    sql = arrived_dates_sql("p.d.raw_cooler", "p.d.raw_events")

    assert sql.count("_loaded_at >= @since") == 2
    assert "`p.d.raw_cooler`" in sql and "`p.d.raw_events`" in sql
    # The days reported are the days of the records, not of the load.
    assert "DATE(ts, 'Asia/Tokyo')" in sql


def test_the_upload_run_query_reads_a_window_and_the_whole_stream() -> None:
    sql = upload_runs_sql("p.d.raw_events")

    # The runs themselves are the window's; what came before one is not.
    assert "_loaded_at >= @since" in sql
    assert sql.count("kind = 'upload_started'") == 1
    assert sql.count("kind = 'upload_done'") == 2


def test_the_raw_schema_carries_the_column_only_bigquery_fills_in() -> None:
    for table in ("raw_cooler", "raw_events"):
        loaded = {field.name: field for field in raw_schema.SCHEMAS[table]}

        assert loaded["_loaded_at"].default_value_expression == "CURRENT_TIMESTAMP()"
        # A chunk in the bucket has every other column and not this one.
        assert "_loaded_at" not in {f.name for f in raw_schema.chunk_schema(table)}


class _Client:
    """Enough of a BigQuery client to see what a sample load is asked to do."""

    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []

    def load_table_from_file(self, _file: Any, table: str, **kwargs: Any) -> Any:
        self.jobs.append({"table": table, "config": kwargs["job_config"]})
        return _Job()


class _Job:
    output_rows = 3

    def result(self) -> None:
        return None


def test_a_sample_is_loaded_the_way_the_transfer_loads_a_chunk(tmp_path: Path) -> None:
    """CI has no transfer, so it issues the same kind of job itself."""
    sample = tmp_path / "day.json"
    sample.write_bytes(b'{"boot_id": "b"}\n')
    client = _Client()
    warehouse = BigQueryWarehouse(client, "p", "d", "us-central1")  # ty: ignore

    result = warehouse.load_file(sample, "raw_events")

    assert result == type(result)(table="raw_events", rows=3)
    (job,) = client.jobs
    assert job["table"] == "p.d.raw_events"
    # Appending, tolerating fields the contract does not declare, and leaving
    # _loaded_at to the default the schema carries.
    assert job["config"].write_disposition == "WRITE_APPEND"
    assert job["config"].ignore_unknown_values is True
    # The schema names the file's fields only. Naming _loaded_at would make BigQuery
    # look for it in the data and write NULL instead of evaluating its default.
    assert {f.name for f in job["config"].schema} == {
        f.name for f in raw_schema.chunk_schema("raw_events")
    }
    assert "_loaded_at" not in {f.name for f in job["config"].schema}

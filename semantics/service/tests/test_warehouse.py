"""The questions the transform asks the warehouse, and how CI puts samples in it.

Loading the bucket is not tested here because this repository no longer does it: a
BigQuery Data Transfer Service config reads the chunks straight into raw. What is
left to get wrong is the SQL, so that is what these read.
"""

from pathlib import Path
from typing import Any

from frostlog_semantics import raw_schema
from frostlog_semantics.warehouse import Arrivals, BigQueryWarehouse, upload_runs_sql


def test_the_upload_run_query_reads_a_window_and_the_whole_stream() -> None:
    sql = upload_runs_sql("p.d.raw_events")

    # The runs themselves are the window's; what came before one is not. The window
    # is on arrival, because a homecoming run's own clock is the one least to be
    # trusted, and it is the run most worth announcing.
    assert "_PARTITIONDATE >= DATE(@since)" in sql
    assert "ts >= @since" not in sql
    assert "ts IS NOT NULL" in sql
    assert sql.count("kind = 'upload_started'") == 1
    assert sql.count("kind = 'upload_done'") == 2


def test_the_raw_schema_is_the_chunk_and_nothing_else() -> None:
    """A transfer loads the bytes as they are; a column of ours would have to be written.

    And a written column costs a statement, which is what moving to a transfer was
    for. Arrival is asked of the table's row count instead.
    """
    for table in ("raw_cooler", "raw_events"):
        names = {field.name for field in raw_schema.SCHEMAS[table]}

        assert not any(name.startswith("_") for name in names)


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
    # Appending, and tolerating fields the contract does not declare.
    assert job["config"].write_disposition == "WRITE_APPEND"
    assert job["config"].ignore_unknown_values is True
    assert {f.name for f in job["config"].schema} == {
        f.name for f in raw_schema.SCHEMAS["raw_events"]
    }


class _Tables:
    """A client that answers only what a table's metadata says."""

    def __init__(self, rows: dict[str, int]) -> None:
        self.rows = rows
        self.asked: list[str] = []

    def get_table(self, name: str) -> Any:
        self.asked.append(name)
        return type("T", (), {"num_rows": self.rows[name.rsplit(".", 1)[-1]]})()


def _counts(rows: dict[str, int], built: dict[str, int]) -> Arrivals:
    client = _Tables(rows)
    return BigQueryWarehouse(client, "p", "d", "us-central1").arrivals(built)  # ty: ignore


def test_nothing_new_when_the_counts_have_not_moved() -> None:
    """A transfer that finds nothing still touches the table, so the count is asked."""
    arrivals = _counts({"raw_cooler": 100, "raw_events": 5}, {"raw_cooler": 100, "raw_events": 5})

    assert arrivals.rows == 0


def test_rows_above_the_mark_are_what_arrived() -> None:
    arrivals = _counts({"raw_cooler": 120, "raw_events": 7}, {"raw_cooler": 100, "raw_events": 5})

    assert arrivals.rows == 22
    assert arrivals.counts == {"raw_cooler": 120, "raw_events": 7}


def test_no_mark_at_all_means_everything_is_new() -> None:
    """A lost mark rebuilds once more than it had to, which is the safe way to fail."""
    arrivals = _counts({"raw_cooler": 100, "raw_events": 5}, {})

    assert arrivals.rows == 105


def test_a_table_that_shrank_was_rebuilt_and_all_of_it_is_new() -> None:
    """Raw is append-only; a smaller count means it was rebuilt, not that rows left.

    Clamping at zero here would strand the mark above the count and leave the
    transform idle until the table grew past its old size -- after a rebuild, never.
    """
    arrivals = _counts({"raw_cooler": 10, "raw_events": 5}, {"raw_cooler": 100, "raw_events": 5})

    assert arrivals.rows == 10

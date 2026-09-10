"""How a chunk is identified in BigQuery, and what the append statement looks like."""

from frostlog_semantic import raw_objects, raw_schema, warehouse


def _chunk(name: str = "v1/cooler/dt=2026-09-06/000000122880.jsonl.gz") -> raw_objects.RawObject:
    chunk = raw_objects.parse("frostlog-raw", name)
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


def test_a_job_id_is_within_the_length_bigquery_allows() -> None:
    assert len(warehouse.append_job_id(_chunk().name)) <= 1024


def test_the_staging_table_is_named_after_the_object_and_its_stream() -> None:
    name = warehouse.stage_table_name(_chunk())
    assert name.startswith("stage_raw_cooler_")
    assert name == warehouse.stage_table_name(_chunk())
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

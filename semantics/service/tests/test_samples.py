"""The sample chunks CI rebuilds its warehouse from."""

from frostlog_semantics import samples
from frostlog_semantics.settings import Settings


def test_both_streams_have_a_sample_in_the_repository() -> None:
    chunks = samples.sample_chunks(Settings().contracts_dir)

    assert sorted({stream for _, stream in chunks}) == ["cooler", "events"]


def test_a_sample_is_given_the_object_name_its_day_would_have_in_the_bucket() -> None:
    (path, stream) = samples.sample_chunks(Settings().contracts_dir)[0]

    assert samples.source_key(path, stream) == f"v1/{stream}/dt={path.stem}/000000000000.jsonl"

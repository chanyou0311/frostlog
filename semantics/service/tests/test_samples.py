"""The sample chunks CI rebuilds its warehouse from."""

from frostlog_semantics import samples
from frostlog_semantics.settings import ROOT


def test_both_streams_have_a_sample_in_the_repository() -> None:
    chunks = samples.sample_chunks(ROOT / "contracts")

    assert sorted({stream for _, stream in chunks}) == ["cooler", "events"]

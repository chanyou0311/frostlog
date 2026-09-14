"""Where the job's own names and paths come from."""

from frostlog_contracts.project import topic_path
from frostlog_contracts.settings import Settings


def test_the_short_topic_name_becomes_a_full_one() -> None:
    # Terraform passes the short name; Pub/Sub wants the path.
    assert topic_path("chanyou-frostlog", "frostlog-signals") == (
        "projects/chanyou-frostlog/topics/frostlog-signals"
    )


def test_a_full_topic_name_is_left_alone() -> None:
    full = "projects/other/topics/frostlog-signals"
    assert topic_path("chanyou-frostlog", full) == full


def test_the_contracts_are_found_next_to_the_package() -> None:
    assert (Settings().contracts_dir / "collection.odcs.yaml").exists()

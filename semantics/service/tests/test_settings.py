"""Where the deployment's own names come from."""

from frostlog_platform.project import topic_path
from frostlog_semantics.settings import Settings


def test_the_short_topic_name_becomes_a_full_one() -> None:
    # Terraform passes the short name; Pub/Sub wants the path.
    assert topic_path("chanyou-frostlog", "frostlog-signals") == (
        "projects/chanyou-frostlog/topics/frostlog-signals"
    )


def test_a_full_topic_name_is_left_alone() -> None:
    full = "projects/other/topics/frostlog-signals"
    assert topic_path("chanyou-frostlog", full) == full


def test_the_raw_tables_live_in_the_model_dataset_unless_told_otherwise() -> None:
    assert Settings(bq_dataset="frostlog").raw_dataset == "frostlog"
    assert Settings(bq_dataset="frostlog", bq_raw_dataset="frostlog_raw").raw_dataset == (
        "frostlog_raw"
    )

"""Where the job's own names and paths come from."""

from frostlog_contracts.project import topic_path
from frostlog_contracts.settings import ROOT, Settings


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


def test_a_run_from_a_checkout_finds_the_contracts() -> None:
    # ROOT is counted in directories, so moving this package breaks it silently:
    # the default would point at a directory that is simply not there.
    assert (ROOT / "contracts" / "collection.odcs.yaml").exists()
    assert (Settings().contracts_dir / "semantics.odcs.yaml").exists()

"""Put the sample chunks into the CI dataset's raw tables.

The repository keeps one uncompressed day of each stream under
``contracts/samples/raw/<stream>/<UTC date>.json``. ``make ci-warehouse`` loads
them into the CI dataset through the service's own load path — same schema, same
staging table, same append statement — and then lets dbt and datacontract-cli
work on the result. Nothing is faked, so what CI proves is what production does.

The tables are dropped and recreated first: CI rebuilds the dataset, it does not
add to it.
"""

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from frostlog_semantic import raw_objects
from frostlog_semantic.settings import Settings, resolve_project

log = logging.getLogger(__name__)


def sample_chunks(contracts_dir: Path) -> list[tuple[Path, str]]:
    """Every sample file with the stream it belongs to, in a stable order."""
    root = contracts_dir / "samples" / "raw"
    found = [
        (path, path.parent.name)
        for path in sorted(root.glob("*/*.json"))
        if path.parent.name in raw_objects.TABLES
    ]
    if not found:
        raise FileNotFoundError(f"no sample chunks under {root}")
    return found


def source_key(path: Path, stream: str) -> str:
    """The object name this sample would have in the bucket (it is one UTC day)."""
    return f"v1/{stream}/dt={path.stem}/{0:012d}.jsonl"


def target_dates(chunks: list[tuple[Path, str]]) -> list[str]:
    """The JST dates the samples touch, as dbt's ``target_dates`` variable wants them."""
    days: set[str] = set()
    for path, _ in chunks:
        for day in raw_objects.target_dates(datetime.fromisoformat(path.stem).date()):
            days.add(day.isoformat())
    return sorted(days)


def load(settings: Settings) -> None:
    """Rebuild the CI raw tables from the samples."""
    from google.cloud import bigquery

    from frostlog_semantic.warehouse import BigQueryWarehouse

    chunks = sample_chunks(settings.contracts_dir)
    project = resolve_project(settings.gcp_project)
    warehouse = BigQueryWarehouse(
        bigquery.Client(project=project),
        project,
        settings.bq_dataset_ci,
        settings.bq_location,
    )
    uploaded_at = datetime.now(UTC)
    for table in sorted({raw_objects.TABLES[stream] for _, stream in chunks}):
        warehouse.reset_table(table)
        log.info("%s.%s: emptied", settings.bq_dataset_ci, table)
    for path, stream in chunks:
        result = warehouse.load_file(
            path, raw_objects.TABLES[stream], source_key(path, stream), uploaded_at
        )
        log.info("%s: %d row(s) into %s", path.name, result.rows, result.table)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target-dates",
        action="store_true",
        help="print the dbt --vars covering the samples instead of loading them",
    )
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
    settings = Settings()
    if arguments.target_dates:
        print(json.dumps({"target_dates": target_dates(sample_chunks(settings.contracts_dir))}))
        return 0
    load(settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

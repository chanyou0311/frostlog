"""Put the sample chunks into the CI dataset's raw tables.

The repository keeps one uncompressed day of each stream under
``contracts/samples/collection/<stream>/<UTC date>.json``. ``make ci-warehouse`` loads
them into the CI dataset through the service's own load path — same schema, same
staging table, same append statement — and then lets dbt and datacontract-cli
work on the result. Nothing is faked, so what CI proves is what production does.

The tables are dropped and recreated first: CI rebuilds the dataset, it does not
add to it.
"""

import argparse
import logging
import sys
import tempfile
from pathlib import Path

from frostlog_platform.project import resolve_project
from frostlog_semantics import raw_objects
from frostlog_semantics.settings import Settings

log = logging.getLogger(__name__)


def sample_chunks(contracts_dir: Path) -> list[tuple[Path, str]]:
    """Every sample file with the stream it belongs to, in a stable order."""
    root = contracts_dir / "samples" / "collection"
    found = [
        (path, path.parent.name)
        for path in sorted(root.glob("*/*.json"))
        if path.parent.name in raw_objects.TABLES
    ]
    if not found:
        raise FileNotFoundError(f"no sample chunks under {root}")
    return found


def split(path: Path, workspace: Path) -> list[Path]:
    """The sample cut in two where the Pi would have cut it.

    The uploader ships whatever has been written since the last chunk, so a record
    can be the last one in its delivery. Cutting a sample the same way is how CI
    checks that a chunk boundary does not end a state: the report before the cut has
    its successor in the second delivery, and held_seconds and the energy derived
    from it must account for that neighbour across the boundary.
    """
    lines = path.read_bytes().splitlines(keepends=True)
    head, tail = lines[: len(lines) // 2], lines[len(lines) // 2 :]
    parts = []
    for index, part in enumerate((head, tail)):
        cut = workspace / f"{path.stem}.{index}.json"
        cut.write_bytes(b"".join(part))
        parts.append(cut)
    return parts


def load(settings: Settings, part: int | None = None) -> None:
    """Rebuild the CI raw tables from the samples.

    ``part`` loads only the first or only the second half of every sample, as two
    deliveries rather than one. Part 1 empties the tables first, as a whole load
    does; part 2 adds to what is there, because that is the situation being tested.
    """
    from google.cloud import bigquery

    from frostlog_semantics.warehouse import BigQueryWarehouse

    chunks = sample_chunks(settings.contracts_dir)
    project = resolve_project(settings.gcp_project)
    warehouse = BigQueryWarehouse(
        bigquery.Client(project=project),
        project,
        settings.bq_dataset_ci,
        settings.bq_location,
    )
    if part != 2:
        for table in sorted({raw_objects.TABLES[stream] for _, stream in chunks}):
            warehouse.reset_table(table)
            log.info("%s.%s: emptied", settings.bq_dataset_ci, table)
    with tempfile.TemporaryDirectory() as workspace:
        for path, stream in chunks:
            for cut in _deliveries(path, Path(workspace), part):
                result = warehouse.load_file(cut, raw_objects.TABLES[stream])
                log.info("%s: %d row(s) into %s", cut.name, result.rows, result.table)


def _deliveries(path: Path, workspace: Path, part: int | None) -> list[Path]:
    """Which of the sample's halves this load ships."""
    if part is None:
        return [path]
    return [split(path, workspace)[part - 1]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--part",
        type=int,
        choices=(1, 2),
        help="load only this half of every sample, as one of two deliveries",
    )
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
    settings = Settings()
    load(settings, arguments.part)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

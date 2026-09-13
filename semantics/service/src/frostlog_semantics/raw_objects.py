"""The collection data product's streams, and the dates the semantic model keys on.

The collection data product writes immutable chunks named
``v1/<stream>/dt=YYYY-MM-DD/<offset 12 digits>.jsonl.gz`` where ``dt`` is the UTC
date of the records inside. A BigQuery Data Transfer Service config reads them
into the raw tables, so nothing here parses an object name any more; what is left
is which stream lands in which table, and the dim_date key of a JST date.
"""

from datetime import date

#: The streams the semantic data product reads, and the raw table each lands in.
TABLES: dict[str, str] = {"cooler": "raw_cooler", "events": "raw_events"}


def date_key(day: date) -> int:
    """The dim_date key of a JST date (yyyymmdd)."""
    return day.year * 10000 + day.month * 100 + day.day

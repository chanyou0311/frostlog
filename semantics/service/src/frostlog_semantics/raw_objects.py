"""The raw chunks in the bucket: which ones we take, and which dates they touch.

The collection data product writes immutable chunks named
``v1/<stream>/dt=YYYY-MM-DD/<offset 12 digits>.jsonl.gz`` where ``dt`` is the UTC
date of the records inside. Anything else under the bucket (a stray file, a
retired stream) is not ours and is ignored.

The semantic model is keyed on JST dates, so one UTC day spans two of them: UTC
``dt`` covers JST ``dt`` 09:00 through ``dt + 1`` 09:00.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta

#: The streams the semantic data product reads, and the raw table each lands in.
TABLES: dict[str, str] = {"cooler": "raw_cooler", "events": "raw_events"}

_KEY = re.compile(
    r"^v1/(?P<stream>cooler|events)/dt=(?P<dt>\d{4}-\d{2}-\d{2})/(?P<offset>\d{12})\.jsonl\.gz$"
)


@dataclass(frozen=True)
class RawObject:
    """One chunk in the raw bucket."""

    bucket: str
    name: str
    stream: str
    #: UTC date of the records in the chunk, from the ``dt=`` prefix.
    dt: date
    #: Byte offset in the Pi's local day file the chunk starts at.
    offset: int

    @property
    def table(self) -> str:
        return TABLES[self.stream]

    @property
    def uri(self) -> str:
        return f"gs://{self.bucket}/{self.name}"


def parse(bucket: str, name: str) -> RawObject | None:
    """The chunk ``name`` describes, or ``None`` when it is not one of ours."""
    match = _KEY.match(name)
    if match is None:
        return None
    return RawObject(
        bucket=bucket,
        name=name,
        stream=match["stream"],
        dt=date.fromisoformat(match["dt"]),
        offset=int(match["offset"]),
    )


def target_dates(dt: date) -> list[date]:
    """The JST dates a UTC day's records can fall on."""
    return [dt, dt + timedelta(days=1)]


def date_key(day: date) -> int:
    """The dim_date key of a JST date (yyyymmdd)."""
    return day.year * 10000 + day.month * 100 + day.day

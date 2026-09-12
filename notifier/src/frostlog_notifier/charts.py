"""The charts a notification carries, drawn by Slack rather than by us.

Slack's ``data_visualization`` block takes the numbers and draws them itself, so
these functions assemble values instead of pixels. That buys a chart that follows
the reader's theme, says its values on a tap, and costs the image nothing — and
it takes away the things a drawing library would have let us do. Three of its
rules shape everything here:

* a series may not skip a category, so an hour nothing was recorded in cannot be
  drawn as a hole. The caller's answer is to chart a stretch that has no holes;
* there is no way to shade a band, so "plugged in" and "recording stopped" are
  said in words by whoever builds the message;
* at most twenty points to a series, and at most two of these blocks to a
  message.

A series with a gap in it is dropped rather than patched: a carried-over reading
would claim an observation nobody made.
"""

from dataclasses import dataclass
from typing import Any

#: What Slack accepts. https://docs.slack.dev/reference/block-kit/blocks/data-visualization-block/
MAX_POINTS = 20
MAX_SERIES = 12
MAX_TITLE = 50
MAX_SERIES_NAME = 20
#: Blocks of this kind allowed in one message.
MAX_CHARTS = 2


@dataclass(frozen=True)
class Series:
    """One line or one set of bars. ``values`` lines up with the categories."""

    name: str
    values: list[float | None]


def line(title: str, categories: list[str], series: list[Series]) -> dict[str, Any] | None:
    return _chart("line", title, categories, series)


def bar(title: str, categories: list[str], series: list[Series]) -> dict[str, Any] | None:
    return _chart("bar", title, categories, series)


def _chart(
    kind: str, title: str, categories: list[str], series: list[Series]
) -> dict[str, Any] | None:
    """A chart block, or ``None`` when there is nothing Slack could be asked to draw."""
    if not categories or len(categories) > MAX_POINTS:
        return None
    drawable = [item for item in series if _complete(item, len(categories))][:MAX_SERIES]
    if not drawable:
        return None
    return {
        "type": "data_visualization",
        "title": title[:MAX_TITLE],
        "chart": {
            "type": kind,
            "series": [
                {
                    "name": item.name[:MAX_SERIES_NAME],
                    "data": [
                        {"label": label, "value": value}
                        for label, value in zip(categories, item.values, strict=True)
                    ],
                }
                for item in drawable
            ],
            "axis_config": {"categories": categories},
        },
    }


def _complete(series: Series, wanted: int) -> bool:
    """Only a series with a value for every category can be drawn at all."""
    return len(series.values) == wanted and all(value is not None for value in series.values)

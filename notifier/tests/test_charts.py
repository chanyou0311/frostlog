"""The chart blocks, held against what Slack says it will accept.

A block Slack refuses takes the whole message with it, and a chart this module
declines to build disappears without a word — both are worth a test that does not
need a token to run.
"""

import pytest

from frostlog_notifier import charts

#: https://docs.slack.dev/reference/block-kit/blocks/data-visualization-block/
BLOCK_KEYS = {"type", "title", "chart"}
CHART_KEYS = {"type", "series", "axis_config"}


def check(block: dict) -> None:
    """Everything the reference says a data_visualization block must be."""
    assert set(block) == BLOCK_KEYS
    assert block["type"] == "data_visualization"
    assert len(block["title"]) <= charts.MAX_TITLE
    chart = block["chart"]
    assert set(chart) == CHART_KEYS
    assert chart["type"] in {"line", "bar", "area", "pie"}
    categories = chart["axis_config"]["categories"]
    assert 1 <= len(categories) <= charts.MAX_POINTS
    assert len(chart["series"]) <= charts.MAX_SERIES
    names = [series["name"] for series in chart["series"]]
    assert len(names) == len(set(names)), "series names must be unique"
    for series in chart["series"]:
        assert len(series["name"]) <= charts.MAX_SERIES_NAME
        # "Series may not omit data points", and every label must be a category.
        assert [point["label"] for point in series["data"]] == categories
        assert all(point["value"] is not None for point in series["data"])


def test_a_line_chart_is_what_slack_accepts() -> None:
    block = charts.line(
        "バッテリー残量 (%)",
        ["8:00", "8:30"],
        [charts.Series("残量", [62, 60]), charts.Series("設定", [-20.0, -20.0])],
    )
    assert block is not None
    check(block)


def test_a_series_with_a_hole_is_left_out_rather_than_filled() -> None:
    block = charts.line(
        "温度 (°C)",
        ["8:00", "8:30"],
        [charts.Series("庫内", [-18.0, -19.0]), charts.Series("周辺", [24.0, None])],
    )
    assert block is not None
    check(block)
    assert [series["name"] for series in block["chart"]["series"]] == ["庫内"]


def test_nothing_drawable_is_no_block_at_all() -> None:
    assert charts.line("空", [], [charts.Series("残量", [])]) is None
    assert charts.line("穴", ["8:00"], [charts.Series("残量", [None])]) is None
    assert charts.line("長さ違い", ["8:00", "8:30"], [charts.Series("残量", [62])]) is None


@pytest.mark.parametrize("count", [charts.MAX_POINTS, charts.MAX_POINTS + 1])
def test_the_limit_on_points_is_the_line_between_a_chart_and_none(count: int) -> None:
    categories = [str(index) for index in range(count)]
    block = charts.line("点", categories, [charts.Series("値", list(range(count)))])
    if count > charts.MAX_POINTS:
        assert block is None
    else:
        assert block is not None
        check(block)


def test_a_name_too_long_for_slack_is_cut_rather_than_refused() -> None:
    block = charts.line("題" * 80, ["8:00"], [charts.Series("系" * 40, [1.0])])
    assert block is not None
    check(block)

from datetime import timedelta

from conftest import FakeWarehouse, at, pulldown_row, state_update

from frostlog_notifier import pulldowns
from frostlog_notifier.notification import PULLDOWN

NOW = at("2026-09-11", 12, 0)
START = at("2026-09-11", 8, 12)


def episode_updates() -> list[dict]:
    """The interior falling from 12 °C to -19 °C over the 46 minutes."""
    temperatures = [12, 6, 1, -6, -12, -16, -18, -19]
    return [
        state_update(
            START + timedelta(minutes=index * 6),
            interior_temperature_celsius=temperature,
            state_of_charge_percent=100 - index * 2,
            ambient_temperature_celsius=27.3,
        )
        for index, temperature in enumerate(temperatures)
    ]


def warehouse_with(*episodes: dict) -> FakeWarehouse:
    return FakeWarehouse(
        {
            "finished_pulldowns_between": list(episodes),
            "state_updates_between": episode_updates(),
        }
    )


def never_posted(kind: str, key: str) -> bool:
    return False


def test_a_finished_episode_becomes_a_notification() -> None:
    warehouse = warehouse_with(pulldown_row(START))
    [notification] = pulldowns.build_all(warehouse, never_posted, NOW)
    assert notification.kind == PULLDOWN
    assert notification.key == "pd-202609110812"
    assert "到達" in notification.text
    assert "庫内 12 °C → -19 °C" in notification.text
    assert "設定 -20 °C" in notification.text
    assert "所要 46 分" in notification.text
    assert "車内平均 27.3 °C" in notification.text
    assert "消費 52.1 Wh" in notification.text
    assert "SoC 100 % → 88 % (-12 pt)" in notification.text
    assert notification.image is not None
    assert notification.image.startswith(b"\x89PNG")


def test_an_interrupted_episode_says_how_long_it_ran() -> None:
    episode = pulldown_row(START, outcome="interrupted", reached_at=None, duration_seconds=None)
    [notification] = pulldowns.build_all(warehouse_with(episode), never_posted, NOW)
    assert "中断" in notification.text
    assert "経過 46 分" in notification.text


def test_an_episode_already_posted_is_left_alone() -> None:
    warehouse = warehouse_with(pulldown_row(START))
    assert pulldowns.build_all(warehouse, lambda kind, key: key == "pd-202609110812", NOW) == []


def test_a_backlog_drains_oldest_first() -> None:
    episodes = [
        pulldown_row(START + timedelta(hours=index), pulldown_key=f"pd-{index}")
        for index in range(pulldowns.PER_RUN + 3)
    ]
    built = pulldowns.build_all(warehouse_with(*episodes), never_posted, NOW)
    assert [notification.key for notification in built] == ["pd-0", "pd-1", "pd-2", "pd-3", "pd-4"]


def test_the_lookback_is_a_week() -> None:
    warehouse = warehouse_with(pulldown_row(START))
    pulldowns.build_all(warehouse, never_posted, NOW)
    parameters = dict(warehouse.queried)["finished_pulldowns_between"]
    assert parameters["start"] == NOW - timedelta(days=7)
    assert parameters["end"] == NOW

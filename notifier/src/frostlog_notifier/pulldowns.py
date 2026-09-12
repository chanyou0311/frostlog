"""到達: one message per pull-down episode that has finished.

The episodes themselves are the semantic data product's rows; this only decides
which of them have not been spoken about yet.
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from frostlog_notifier import charts, formatting, queries
from frostlog_notifier.notification import PULLDOWN, Notification
from frostlog_notifier.queries import Pulldown, StateUpdate
from frostlog_notifier.warehouse import Warehouse

log = logging.getLogger(__name__)

#: How far back unposted episodes are picked up (a trip may deliver several days at once).
LOOKBACK = timedelta(days=7)
#: At most this many per run, oldest first, so a backlog drains without flooding the channel.
PER_RUN = 5

OUTCOME = {"reached": "到達", "interrupted": "中断"}
TRIGGER = {"start_up": "起動", "setpoint_change": "設定変更", "rise": "温度上昇"}


def build_all(
    warehouse: Warehouse,
    posted_keys: Callable[[str, list[str]], set[str]],
    now: datetime,
) -> list[Notification]:
    """A notification for each finished episode not yet posted, oldest first."""
    finished = queries.finished_pulldowns_between(warehouse, now - LOOKBACK, now)
    already = posted_keys(PULLDOWN, [episode.pulldown_key for episode in finished])
    pending = [episode for episode in finished if episode.pulldown_key not in already]
    if len(pending) > PER_RUN:
        log.info("%d pull-downs pending; posting the oldest %d", len(pending), PER_RUN)
    return [_notification(warehouse, episode) for episode in pending[:PER_RUN]]


def _notification(warehouse: Warehouse, episode: Pulldown) -> Notification:
    updates = queries.state_updates_between(
        warehouse, episode.started_at, episode.ended_at or episode.started_at
    )
    return Notification(
        kind=PULLDOWN,
        key=episode.pulldown_key,
        text=_text(episode, updates),
        blocks=_blocks(episode, updates),
    )


def _blocks(episode: Pulldown, updates: list[StateUpdate]) -> list[dict]:
    outcome = OUTCOME.get(episode.outcome or "", episode.outcome or "進行中")
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"❄️ 設定温度に{outcome}", "emoji": True},
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": _lead(episode)}},
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": "*庫内*\n"
                    f"{formatting.celsius(episode.interior_temperature_start_celsius, 0)}"
                    f" → {formatting.celsius(episode.setpoint_celsius, 0)}",
                },
                {
                    "type": "mrkdwn",
                    "text": "*周辺 (平均)*\n"
                    f"{formatting.celsius(episode.ambient_temperature_celsius)}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*消費*\n{formatting.watt_hours(episode.discharged_watt_hours)}",
                },
                {"type": "mrkdwn", "text": f"*残量*\n{_charge(episode)}"},
            ],
        },
    ]
    chart = _chart(updates)
    if chart is not None:
        blocks.append(chart)
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"きっかけ {TRIGGER.get(episode.trigger, episode.trigger)}"
                    f" ・ 外部電源につないでいた割合 "
                    f"{formatting.percent((episode.external_input_ratio or 0.0) * 100)}"
                    " ・ Anker Solix EverFrost 2",
                }
            ],
        }
    )
    return blocks


def _chart(updates: list[StateUpdate]) -> dict | None:
    """Interior and ambient over the episode, thinned to what Slack will draw."""
    if not updates:
        return None
    start = updates[0].updated_at
    step = (len(updates) + charts.MAX_POINTS - 1) // charts.MAX_POINTS or 1
    sampled = updates[::step][: charts.MAX_POINTS]
    labels = [f"{(u.updated_at - start).total_seconds() / 60:.0f}分" for u in sampled]
    return charts.line(
        "庫内と周辺 (°C)",
        labels,
        [
            charts.Series("庫内", [float(u.interior_temperature_celsius) for u in sampled]),
            charts.Series("周辺", [u.ambient_temperature_celsius for u in sampled]),
        ],
    )


def _charge(episode: Pulldown) -> str:
    start = episode.state_of_charge_start_percent
    delta = episode.state_of_charge_delta_percent
    if delta is None:
        return formatting.percent(start)
    return f"{formatting.percent(start)} → {formatting.percent(start + delta)} ({delta:+d} pt)"


def _lead(episode: Pulldown) -> str:
    if episode.outcome == "reached":
        return (
            f"*{formatting.celsius(episode.interior_temperature_start_celsius, 0)} から"
            f" {formatting.duration(episode.duration_seconds)}で"
            f" {formatting.celsius(episode.setpoint_celsius, 0)} に届きました。*\n"
            f"{formatting.full_stamp(episode.started_at)} に始まった冷却です。"
        )
    return (
        f"*設定温度に届く前に止まりました。*\n"
        f"{formatting.full_stamp(episode.started_at)} から"
        f" {formatting.duration(episode.elapsed_seconds)}、"
        f"{formatting.celsius(episode.interior_temperature_start_celsius, 0)} から"
        f" {formatting.celsius(episode.setpoint_celsius, 0)} を目指していました。"
    )


def _text(episode: Pulldown, updates: list[StateUpdate]) -> str:
    outcome = OUTCOME.get(episode.outcome or "", episode.outcome or "進行中")
    ended_interior = updates[-1].interior_temperature_celsius if updates else None
    span = (
        f"所要 {formatting.duration(episode.duration_seconds)}"
        if episode.outcome == "reached"
        else f"経過 {formatting.duration(episode.elapsed_seconds)}"
    )
    delta = episode.state_of_charge_delta_percent
    start_charge = episode.state_of_charge_start_percent
    charge = f"SoC {formatting.percent(start_charge)}"
    if delta is not None:
        charge += f" → {formatting.percent(start_charge + delta)} ({delta:+d} pt)"
    return "\n".join(
        [
            f":snowflake: {outcome} {formatting.full_stamp(episode.started_at)}"
            f" (きっかけ {episode.trigger})",
            f"庫内 {formatting.celsius(episode.interior_temperature_start_celsius, 0)}"
            f" → {formatting.celsius(ended_interior, 0)}"
            f" (設定 {formatting.celsius(episode.setpoint_celsius, 0)})",
            f"{span} / 周辺平均 {formatting.celsius(episode.ambient_temperature_celsius)}"
            f" / 外部入力 {formatting.percent((episode.external_input_ratio or 0.0) * 100)}",
            f"消費 {formatting.watt_hours(episode.discharged_watt_hours)} / {charge}",
        ]
    )

"""到達: one message per pull-down episode that has finished.

The episodes themselves are the semantic data product's rows; this only decides
which of them have not been spoken about yet.
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from frostlog_notifier import charts, formatting, queries
from frostlog_notifier.clock import to_jst
from frostlog_notifier.notification import PULLDOWN, Notification
from frostlog_notifier.queries import Pulldown, StateUpdate
from frostlog_notifier.warehouse import Warehouse

log = logging.getLogger(__name__)

#: How far back unposted episodes are picked up (a trip may deliver several days at once).
LOOKBACK = timedelta(days=7)
#: At most this many per run, oldest first, so a backlog drains without flooding the channel.
PER_RUN = 5

OUTCOME = {"reached": "到達", "interrupted": "中断"}


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
    title = f"Pull-down {formatting.full_stamp(episode.started_at)}"
    return Notification(
        kind=PULLDOWN,
        key=episode.pulldown_key,
        text=_text(episode, updates),
        image=charts.pulldown(updates, episode.setpoint_celsius, title),
        filename=f"pulldown-{to_jst(episode.started_at):%Y%m%d-%H%M}.png",
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
            f"{span} / 車内平均 {formatting.celsius(episode.ambient_temperature_celsius)}"
            f" / 外部入力 {formatting.percent((episode.external_input_ratio or 0.0) * 100)}",
            f"消費 {formatting.watt_hours(episode.discharged_watt_hours)} / {charge}",
        ]
    )

"""One controller run: read presence, compare it to the stored judgment, act once.

Kept apart from __main__ so a test can drive it with a fake nmcli and a fake
gateway socket instead of a real Pi. No retry loop lives here -- the timer that
schedules the next run is the retry.
"""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from frostlog_controller import gateway, presence, state
from frostlog_controller.config import Config

log = logging.getLogger("frostlog_controller")

SendCommand = Callable[[Path, str, float, str, str], dict[str, Any]]


def run(
    config: Config,
    state_path: Path,
    socket_path: Path,
    nmcli_runner: presence.NmcliRunner | None = None,
    send_command: SendCommand = gateway.send_command,
) -> None:
    home_now = presence.is_home(config.home_ssid, nmcli_runner)
    if home_now is None:
        log.warning("presence unknown this run, doing nothing")
        return

    current = state.load(state_path)
    if current is None:
        log.info("no state yet, adopting current presence (home=%s) without a command", home_now)
        state.save(state_path, state.State(home=home_now, attempts=0))
        return

    if home_now == current.home:
        log.info("presence unchanged (home=%s), nothing to do", home_now)
        if current.attempts:
            # A change that was still being retried has been undone by the presence
            # itself. Its failures are not the next change's to inherit.
            state.save(state_path, state.State(home=current.home, attempts=0))
        return

    setpoint = config.home_setpoint_celsius if home_now else config.away_setpoint_celsius
    reason = "arrived_home" if home_now else "left_home"
    log.info(
        "presence changed to home=%s, requesting setpoint_celsius=%s (%s)",
        home_now,
        setpoint,
        reason,
    )
    try:
        response = send_command(socket_path, "setpoint_celsius", setpoint, "controller", reason)
        accepted = response.get("status") == "accepted"
        if not accepted:
            log.info("gateway rejected the command: %s", response.get("error"))
    except gateway.GatewayUnreachable as exc:
        log.info("could not reach the gateway: %s", exc)
        accepted = False
    except gateway.GatewayError as exc:
        # The request went out and its answer did not come back. Asking again would
        # write the value a second time, over whatever the cooler -- or a hand -- has
        # set since; the outcome is on the gateway's stream, and the judgment stands.
        log.warning("the command's outcome is unknown, not asking again: %s", exc)
        accepted = True

    if accepted:
        log.info("command taken, judgment now home=%s", home_now)
        state.save(state_path, state.State(home=home_now, attempts=0))
        return

    attempts = current.attempts + 1
    if attempts >= config.max_attempts:
        log.error(
            "giving up after %d attempts, adopting home=%s without confirmation",
            attempts,
            home_now,
        )
        state.save(state_path, state.State(home=home_now, attempts=0))
    else:
        log.info(
            "keeping judgment home=%s, attempt %d/%d",
            current.home,
            attempts,
            config.max_attempts,
        )
        state.save(state_path, state.State(home=current.home, attempts=attempts))

"""Is the Pi on the home Wi-Fi right now, according to NetworkManager.

nmcli is asked rather than any lower-level Wi-Fi state because it is what
actually decided which network carries an upload; a wpa_supplicant-level view
could disagree with it. A failing or absent nmcli means "don't know", not
"away" -- guessing away would move the setpoint on a Pi that never left.
"""

import logging
import os
import subprocess
from collections.abc import Callable

log = logging.getLogger("frostlog_controller")

NMCLI_COMMAND = ["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi", "list", "--rescan", "no"]

NmcliRunner = Callable[[], "subprocess.CompletedProcess[str]"]


def _run_nmcli() -> subprocess.CompletedProcess[str]:
    # nmcli translates its yes/no with the locale -- the Pi answers はい/いいえ -- so it
    # is asked in the C locale, where the words are the ones parsed below.
    environment = {**os.environ, "LC_ALL": "C"}
    return subprocess.run(
        NMCLI_COMMAND, capture_output=True, text=True, timeout=10, env=environment
    )


def _unescape(value: str) -> str:
    """Undo nmcli -t's backslash-escaping of ':' (and '\\') within a field."""
    result: list[str] = []
    escaped = False
    for ch in value:
        if escaped:
            result.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        else:
            result.append(ch)
    return "".join(result)


def parse_active_ssids(output: str) -> list[str]:
    """SSIDs nmcli marks ACTIVE.

    A field may itself contain ':', so only the first separator on each line
    splits ACTIVE from SSID; nmcli escapes a literal ':' within a field as '\\:'.
    """
    ssids = []
    for line in output.splitlines():
        if not line:
            continue
        active, _, rest = line.partition(":")
        if active == "yes":
            ssids.append(_unescape(rest))
    return ssids


def is_home(home_ssid: str, runner: NmcliRunner | None = None) -> bool | None:
    """True/False when nmcli answers, None when presence cannot be determined."""
    runner = runner or _run_nmcli
    try:
        result = runner()
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("nmcli unavailable, presence unknown: %s", exc)
        return None
    if result.returncode != 0:
        log.warning(
            "nmcli exited %d, presence unknown: %s", result.returncode, result.stderr.strip()
        )
        return None
    return home_ssid in parse_active_ssids(result.stdout)

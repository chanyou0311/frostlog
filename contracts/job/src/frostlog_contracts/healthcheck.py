"""Report a clean contract test run to a dead man's switch such as healthchecks.io.

The check is expected to alert when no report arrives for a couple of days: a
single failed run is visible in the quality_report event, a run that stops
happening is not.
"""

import logging
import urllib.request

log = logging.getLogger(__name__)


def ping(url: str) -> None:
    """GET ``url``; a failure is logged and otherwise ignored (tomorrow's run reports again)."""
    try:
        with urllib.request.urlopen(url, timeout=10.0):
            pass
    except (OSError, ValueError) as exc:  # URLError/HTTPError are OSErrors; ValueError: bad URL
        log.warning("healthcheck ping failed: %s", exc)

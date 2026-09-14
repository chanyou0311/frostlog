"""Report a successful upload run to a dead man's switch such as healthchecks.io.

The check is expected to alert when no report arrives for a few days: the Pi
being away from home is normal, an upload service that keeps failing is not.
"""

import logging
import urllib.request

log = logging.getLogger(__name__)


def ping(url: str) -> None:
    """GET ``url``; a failure is logged and otherwise ignored (the next run reports again)."""
    try:
        with urllib.request.urlopen(url, timeout=10.0):
            pass
    except (OSError, ValueError) as exc:  # URLError/HTTPError are OSErrors; ValueError: bad URL
        log.warning("healthcheck ping failed: %s", exc)

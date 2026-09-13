"""Where the Slack bot token is kept.

The token is created after the service is first deployed, so a secret that is
unset, missing or still without a version is a dry run rather than a failure.
The token itself is never logged.
"""

import logging
from typing import Any

from frostlog_notifier.errors import Transient

log = logging.getLogger(__name__)


def slack_bot_token(project: str, secret_name: str | None, client: Any = None) -> str | None:
    """The latest version of the named secret, or None while there is no token."""
    if not secret_name:
        log.info("no Slack token secret is configured; posting in dry run")
        return None
    from google.api_core import exceptions
    from google.cloud import secretmanager

    client = client if client is not None else secretmanager.SecretManagerServiceClient()
    name = f"projects/{project}/secrets/{secret_name}/versions/latest"
    try:
        version = client.access_secret_version(name=name)
    except (exceptions.ServerError, exceptions.TooManyRequests, exceptions.RetryError) as exc:
        raise Transient(f"Secret Manager is unavailable: {exc}") from exc
    except (exceptions.NotFound, exceptions.FailedPrecondition, exceptions.PermissionDenied) as exc:
        log.warning(
            "secret %s has no readable version (%s); posting in dry run",
            secret_name,
            type(exc).__name__,
        )
        return None
    return str(version.payload.data.decode("utf-8"))

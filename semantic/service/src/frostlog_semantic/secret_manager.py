"""Secrets the daily contract test needs, read from Secret Manager at run time.

Terraform gives the service the *names* of two secrets, never their values: the
healthchecks.io ping URL and the HMAC key that lets datacontract-cli read the raw
bucket through its S3-compatible API. A secret that is unset, missing or without
a version is not an error — the run goes on without whatever it enables — so a
reader answers ``None`` and says so once rather than raising every time.

Payloads are never logged.
"""

import logging
from typing import Any, Protocol

from google.api_core.exceptions import GoogleAPIError
from google.auth.exceptions import GoogleAuthError

log = logging.getLogger(__name__)


class SecretReader(Protocol):
    def read(self, name: str) -> str | None: ...


class SecretManagerReader:
    """:class:`SecretReader` over google-cloud-secret-manager.

    ``name`` is either the bare secret id or a full resource name; the latest
    version is the one that counts.
    """

    def __init__(self, client: Any, project: str) -> None:
        self._client = client
        self._project = project
        self._reported: set[str] = set()

    def read(self, name: str) -> str | None:
        try:
            response = self._client.access_secret_version(name=self._version(name))
        except (GoogleAPIError, GoogleAuthError, ValueError) as exc:
            self._report(name, exc)
            return None
        return str(response.payload.data.decode())

    def _version(self, name: str) -> str:
        if name.startswith("projects/"):
            return name if "/versions/" in name else f"{name}/versions/latest"
        return f"projects/{self._project}/secrets/{name}/versions/latest"

    def _report(self, name: str, exc: Exception) -> None:
        """Say once per secret that it could not be read; never say what is in it."""
        if name in self._reported:
            return
        self._reported.add(name)
        log.warning("secret %s could not be read (%s)", name, type(exc).__name__)


class NoSecrets:
    """Used where no secret may be read (local runs, tests): everything is missing."""

    def read(self, name: str) -> str | None:
        log.info("no secret reader configured; %s treated as unset", name)
        return None

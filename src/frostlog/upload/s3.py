"""The two S3 operations the uploader needs: HEAD and PUT of one object."""

from typing import Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from botocore.exceptions import ConnectionError as TransportError


class Offline(Exception):
    """The bucket cannot be connected to at all (the Pi is away from home).

    Only failures to connect count; errors after connecting (timeouts, closed
    connections) are ordinary per-object failures, so the other objects are
    still tried.
    """


class ObjectStore(Protocol):
    def head(self, key: str) -> int | None:
        """The size of the object in bytes, or ``None`` if there is no such object."""
        ...

    def put(self, key: str, data: bytes) -> None: ...


class S3ObjectStore:
    """:class:`ObjectStore` over boto3."""

    def __init__(
        self, endpoint: str, bucket: str, access_key_id: str, secret_access_key: str
    ) -> None:
        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
            config=Config(
                connect_timeout=10,
                read_timeout=60,
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

    def head(self, key: str) -> int | None:
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        except TransportError as exc:
            raise Offline(str(exc)) from exc
        return int(response["ContentLength"])

    def put(self, key: str, data: bytes) -> None:
        try:
            self._client.put_object(
                Bucket=self._bucket, Key=key, Body=data, ContentType="application/x-ndjson"
            )
        except TransportError as exc:
            raise Offline(str(exc)) from exc

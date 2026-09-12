"""The S3 operations the uploader needs: LIST under a prefix, HEAD and PUT of one object."""

import logging
from typing import Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from botocore.exceptions import ConnectionError as TransportError

log = logging.getLogger(__name__)


class Offline(Exception):
    """The bucket cannot be connected to at all (the Pi is away from home).

    Only failures to connect count; errors after connecting (timeouts, closed
    connections) are ordinary per-file failures, so the other files are still
    tried.
    """


class ObjectStore(Protocol):
    #: Which bucket this is (endpoint and name). Upload state derived from one bucket
    #: says nothing about another, so what is cached between runs is tied to this.
    location: str

    def list(self, prefix: str) -> list[str]:
        """The keys of all objects whose key starts with ``prefix``."""
        ...

    def head(self, key: str) -> dict[str, str] | None:
        """The user metadata of the object, or ``None`` if there is no such object."""
        ...

    def put(self, key: str, data: bytes, metadata: dict[str, str]) -> None: ...


class S3ObjectStore:
    """:class:`ObjectStore` over boto3."""

    def __init__(
        self, endpoint: str, bucket: str, access_key_id: str, secret_access_key: str
    ) -> None:
        self._bucket = bucket
        self.location = f"{endpoint.rstrip('/')}/{bucket}"
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
                # botocore 1.36 began adding a CRC32 checksum header to every PUT.
                # Google Cloud Storage's S3-compatible API does not take those headers
                # and refuses the request as SignatureDoesNotMatch ("Invalid argument"),
                # which reads as a credentials problem and is not one — LIST and HEAD,
                # which carry no checksum, go through on the same key. R2 accepted them,
                # so this only surfaced when the bucket moved. Ask for a checksum only
                # where the protocol itself requires one.
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    def list(self, prefix: str) -> list[str]:
        keys: list[str] = []
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                keys.extend(item["Key"] for item in page.get("Contents", []))
        except TransportError as exc:
            raise Offline(str(exc)) from exc
        return keys

    def head(self, key: str) -> dict[str, str] | None:
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        except TransportError as exc:
            raise Offline(str(exc)) from exc
        return dict(response.get("Metadata", {}))

    def put(self, key: str, data: bytes, metadata: dict[str, str]) -> None:
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType="application/gzip",
                Metadata=metadata,
            )
        except TransportError as exc:
            raise Offline(str(exc)) from exc
        except ClientError as exc:
            # The key may only create objects, never overwrite them. A PUT whose
            # response was lost is retried by the client, and the retry is refused:
            # if the object is there with the same end offset, the first PUT landed.
            if exc.response.get("Error", {}).get("Code") != "AccessDenied":
                raise
            existing = self.head(key)
            if existing is None or existing.get("end") != metadata.get("end"):
                raise
            log.info("%s: already in the bucket; the refused PUT was a resend", key)

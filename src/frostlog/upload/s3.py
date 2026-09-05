"""The two S3 operations the uploader needs: HEAD and PUT of one object."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

SHA256_METADATA_KEY = "sha256"


@dataclass(frozen=True)
class RemoteObject:
    size: int
    sha256: str | None


class ObjectStore(Protocol):
    def head(self, key: str) -> RemoteObject | None: ...

    def put(self, key: str, path: Path, sha256: str) -> None: ...


class S3ObjectStore:
    """:class:`ObjectStore` over boto3 (imported lazily; only the uploader needs it)."""

    def __init__(
        self, endpoint: str, bucket: str, access_key_id: str, secret_access_key: str
    ) -> None:
        import boto3
        from botocore.config import Config

        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
            config=Config(retries={"max_attempts": 3, "mode": "standard"}),
        )

    def head(self, key: str) -> RemoteObject | None:
        from botocore.exceptions import ClientError

        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        return RemoteObject(
            size=int(response["ContentLength"]),
            sha256=response.get("Metadata", {}).get(SHA256_METADATA_KEY),
        )

    def put(self, key: str, path: Path, sha256: str) -> None:
        with path.open("rb") as body:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=body,
                ContentType="application/x-ndjson",
                Metadata={SHA256_METADATA_KEY: sha256},
            )

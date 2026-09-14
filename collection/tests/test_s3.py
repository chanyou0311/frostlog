"""The S3 object store against a stand-in for the boto3 client."""

import pytest
from botocore.exceptions import ClientError

from frostlog.upload.s3 import S3ObjectStore

KEY = "v1/cooler/dt=2026-09-06/000000000000.jsonl.gz"


def _access_denied() -> ClientError:
    return ClientError({"Error": {"Code": "AccessDenied", "Message": "forbidden"}}, "PutObject")


class _Client:
    """put_object is refused; head_object answers with whatever metadata is set."""

    def __init__(self, metadata: dict[str, str] | None) -> None:
        self.metadata = metadata

    def put_object(self, **_: object) -> None:
        raise _access_denied()

    def head_object(self, **_: object) -> dict:
        if self.metadata is None:
            raise ClientError({"Error": {"Code": "404", "Message": "no"}}, "HeadObject")
        return {"Metadata": self.metadata}


def _store(client: _Client) -> S3ObjectStore:
    store = S3ObjectStore("https://storage.googleapis.com", "bucket", "id", "secret")
    store._client = client  # the network is the one thing these tests must not touch
    return store


def test_a_refused_resend_of_an_object_that_landed_is_not_a_failure() -> None:
    store = _store(_Client({"start": "0", "end": "4096", "uploaded-at": "t"}))
    store.put(KEY, b"data", {"start": "0", "end": "4096", "uploaded-at": "t"})


def test_a_refused_put_of_an_object_that_is_not_there_is_a_failure() -> None:
    store = _store(_Client(None))
    with pytest.raises(ClientError):
        store.put(KEY, b"data", {"start": "0", "end": "4096", "uploaded-at": "t"})


def test_a_refused_put_over_a_different_object_is_a_failure() -> None:
    store = _store(_Client({"start": "0", "end": "1024", "uploaded-at": "t"}))
    with pytest.raises(ClientError):
        store.put(KEY, b"data", {"start": "0", "end": "4096", "uploaded-at": "t"})


def test_the_client_asks_for_no_checksum_google_cloud_storage_would_refuse() -> None:
    """botocore's default CRC32 header on PUT is one GCS's S3 API will not take.

    It answers ``SignatureDoesNotMatch``, which accuses the credentials rather than
    the request — and LIST and HEAD, which carry no checksum, keep working on the
    same key. Nothing in a stand-in client would catch that, so the real client's
    configuration is what this holds.
    """
    store = S3ObjectStore("https://storage.googleapis.com", "bucket", "id", "secret")
    config = store._client.meta.config
    assert config.request_checksum_calculation == "when_required"
    assert config.response_checksum_validation == "when_required"

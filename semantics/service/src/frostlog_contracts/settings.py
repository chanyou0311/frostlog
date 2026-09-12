"""Machine-specific configuration read from FROSTLOG_* environment variables.

Terraform sets these on the Cloud Run job. The two secrets are named, never
valued: what the job may read is decided by IAM, not by what is in its
environment.
"""

from frostlog_platform.settings import PlatformSettings


class Settings(PlatformSettings):
    #: Dead man's switch pinged after a run in which every contract passed. The direct
    #: value is for local runs; in production the URL is a secret and only its Secret
    #: Manager name is in the environment.
    contract_test_healthcheck_url: str | None = None
    contract_test_healthcheck_url_secret: str | None = None

    #: Secret Manager name of the HMAC key datacontract-cli needs to read the collection
    #: bucket through its S3-compatible API. Payload: {"access_id": …, "secret": …}.
    collection_hmac_secret: str | None = None

    #: Server of contracts/collection.odcs.yaml to test against.
    collection_contract_server: str = "gcs"
    #: Server of contracts/semantics.odcs.yaml to test against.
    semantics_contract_server: str = "production"

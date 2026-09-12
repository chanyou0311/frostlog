# The image both frostlog Cloud Run components run from. Build from the repository root:
#   docker build -f semantics/Dockerfile -t frostlog-semantics .
#
# The service is the semantics transform (CMD below). The contract-test job is the
# same image started as `python -m frostlog_contracts`; it ships here rather than in
# an image of its own because the contracts and datacontract-cli are already inside,
# and Artifact Registry's free tier is half a gigabyte.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app/semantics/service

# Dependencies first, so a change to the code does not reinstall dbt and
# datacontract-cli (the latter brings duckdb and pyarrow, which are not small).
COPY semantics/service/pyproject.toml semantics/service/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --group transform

COPY contracts /app/contracts
COPY semantics /app/semantics
RUN uv sync --locked --no-dev --group transform

# Only what is the same in every deployment is set here. The deployment's own
# names come from Terraform: FROSTLOG_COLLECTION_BUCKET, FROSTLOG_BQ_DATASET,
# FROSTLOG_BQ_DATASET_CI, FROSTLOG_SIGNALS_TOPIC (short name),
# FROSTLOG_CONTRACT_TEST_HEALTHCHECK_URL_SECRET and FROSTLOG_COLLECTION_HMAC_SECRET
# (Secret Manager names, read at run time). The project id is not among them: it
# is resolved from the credentials the revision runs with, and
# FROSTLOG_GCP_PROJECT overrides that.
ENV PATH="/app/.venv/bin:${PATH}" \
    FROSTLOG_DBT_PROJECT_DIR=/app/semantics/dbt \
    FROSTLOG_DBT_PROFILES_DIR=/app/semantics/dbt \
    FROSTLOG_CONTRACTS_DIR=/app/contracts \
    DBT_TARGET_PATH=/tmp/dbt-target \
    DBT_LOG_PATH=/tmp/dbt-logs

# One request at a time, one instance: that is what keeps two transform runs from
# rebuilding the same partition at once (deploy with --concurrency=1
# --max-instances=1). The job overrides this command; it serves nothing.
EXPOSE 8080
CMD ["sh", "-c", "uvicorn frostlog_semantics.app:app --host 0.0.0.0 --port ${PORT:-8080}"]

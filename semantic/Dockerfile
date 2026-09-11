# The frostlog-semantic Cloud Run image. Build from the repository root:
#   docker build -f semantic/Dockerfile -t frostlog-semantic .
# The contracts are copied in because the daily contract test reads them.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app/semantic/service

# Dependencies first, so a change to the code does not reinstall dbt and
# datacontract-cli (the latter brings duckdb and pyarrow, which are not small).
COPY semantic/service/pyproject.toml semantic/service/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --group transform

COPY contracts /app/contracts
COPY semantic /app/semantic
RUN uv sync --locked --no-dev --group transform

# Only what is the same in every deployment is set here. The deployment's own
# names come from Terraform: FROSTLOG_RAW_BUCKET, FROSTLOG_BQ_DATASET,
# FROSTLOG_BQ_DATASET_CI, FROSTLOG_SEMANTIC_UPDATED_TOPIC (short name),
# FROSTLOG_CONTRACT_TEST_HEALTHCHECK_URL_SECRET and FROSTLOG_RAW_HMAC_SECRET
# (Secret Manager names, read at run time). The project id is not among them: it
# is resolved from the credentials the revision runs with, and
# FROSTLOG_GCP_PROJECT overrides that.
ENV PATH="/app/.venv/bin:${PATH}" \
    FROSTLOG_DBT_PROJECT_DIR=/app/semantic/dbt \
    FROSTLOG_DBT_PROFILES_DIR=/app/semantic/dbt \
    FROSTLOG_CONTRACTS_DIR=/app/contracts \
    DBT_TARGET_PATH=/tmp/dbt-target \
    DBT_LOG_PATH=/tmp/dbt-logs

# One request at a time, one instance: that is what keeps two transform runs from
# rebuilding the same partition at once (deploy with --concurrency=1
# --max-instances=1).
EXPOSE 8080
CMD ["sh", "-c", "uvicorn frostlog_semantic.app:app --host 0.0.0.0 --port ${PORT:-8080}"]

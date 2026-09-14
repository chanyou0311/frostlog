# The semantics transform service's image. Build from the repository root, because
# the dbt project sits beside the service rather than inside it:
#   docker build -f semantics/Dockerfile -t frostlog-semantics .
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app/semantics/service

# Dependencies first, so a change to the code does not reinstall dbt.
COPY semantics/service/pyproject.toml semantics/service/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --group transform

COPY semantics /app/semantics
RUN uv sync --locked --no-dev --group transform

# Only what is the same in every deployment is set here. The deployment's own
# names come from Terraform: FROSTLOG_COLLECTION_BUCKET, FROSTLOG_BQ_DATASET and
# FROSTLOG_SIGNALS_TOPIC (the topic's short name). The project id is not among them:
# it is resolved from the credentials the revision runs with, and
# FROSTLOG_GCP_PROJECT overrides that.
ENV PATH="/app/.venv/bin:${PATH}" \
    FROSTLOG_DBT_PROJECT_DIR=/app/semantics/dbt \
    FROSTLOG_DBT_PROFILES_DIR=/app/semantics/dbt \
    DBT_TARGET_PATH=/tmp/dbt-target \
    DBT_LOG_PATH=/tmp/dbt-logs

# One request at a time, one instance: that is what keeps two transform runs from
# rebuilding the same partition at once (deploy with --concurrency=1
# --max-instances=1).
EXPOSE 8080
CMD ["sh", "-c", "uvicorn frostlog_semantics.app:app --host 0.0.0.0 --port ${PORT:-8080}"]

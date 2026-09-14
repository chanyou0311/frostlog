# The contract-test job's image. Build from the repository root, because the job
# needs the contracts themselves and they live next to it rather than inside it:
#   docker build -f contracts/Dockerfile -t frostlog-contracts .
#
# Cloud Run runs it to completion once a day; there is nothing to listen on.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app/contracts/job

# Dependencies first, so a change to the code does not reinstall datacontract-cli
# (which brings duckdb and pyarrow, and those are not small).
COPY contracts/job/pyproject.toml contracts/job/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY contracts/*.odcs.yaml /app/contracts/
COPY contracts/job /app/contracts/job
RUN uv sync --locked --no-dev

# The contracts are the one path that is not where a checkout would put it: the
# package is installed into the virtualenv, so the directory it would count back to
# does not exist here. Everything else this job is told -- the datasets, the topic's
# short name, the Secret Manager names of the healthcheck URL and the bucket's HMAC
# key -- differs per deployment and comes from Terraform. The project id is not among
# them: it is resolved from the credentials the execution runs with, and
# FROSTLOG_GCP_PROJECT overrides that.
ENV PATH="/app/.venv/bin:${PATH}" \
    FROSTLOG_CONTRACTS_DIR=/app/contracts

# Terraform sets the same command on the job; keep the two equal, so that running the
# image by hand does what the nightly execution does.
CMD ["python", "-m", "frostlog_contracts"]

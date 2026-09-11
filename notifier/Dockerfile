# The notifier service image. Built and deployed by GitHub Actions; run by Cloud Run,
# which passes the port to listen on in PORT.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    # matplotlib needs a writable home; the container's filesystem is read-only but /tmp is not.
    MPLCONFIGDIR=/tmp/matplotlib \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app

# Dependencies first, so that editing the source does not re-resolve them.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN uv sync --locked --no-dev

EXPOSE 8080
CMD ["sh", "-c", "uvicorn frostlog_notifier.app:app --host 0.0.0.0 --port ${PORT:-8080}"]

# The notifier service image. Built and deployed by GitHub Actions; run by Cloud Run,
# which passes the port to listen on in PORT.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /usr/local/bin/uv

# The charts are labelled in Japanese, like the messages they go with, and the slim
# image has no font that can draw it — a missing glyph is a silent box, not an error.
# IPAexGothic is one file of about 6 MB; the Noto CJK package is ten times that for
# languages this never draws.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-ipaexfont-gothic \
    && rm -rf /var/lib/apt/lists/*

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

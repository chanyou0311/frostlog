# Tasks for the Slack data application. Run them from this directory.
#
# `check` needs nothing but a checkout: the warehouse and Slack are faked in the
# tests. Running the service for real takes Application Default Credentials and
# the FROSTLOG_* environment described in README.md.

.PHONY: install check

install:
	uv sync --locked

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run ty check
	uv run pytest

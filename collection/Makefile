# Tasks for the collection data product. Run them from this directory.
#
# `check` needs nothing but a checkout. `deploy` reaches the Pi over ssh; TARGET
# names it (user@host) when it is not the usual one.

#: Where the Pi is, when it is not where deploy.sh looks by default.
TARGET ?=

.PHONY: install check deploy

install:
	uv sync --locked

# Everything that needs no Pi. The sensors and the radio are faked in the tests,
# so this passes on a Mac.
check:
	uv run ruff check .
	uv run ruff format --check .
	uv run ty check
	uv run pytest

deploy:
	scripts/deploy.sh $(TARGET)

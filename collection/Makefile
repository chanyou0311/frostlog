# Tasks for the collection data product. Run them from this directory.
#
# `check` needs nothing but a checkout. `deploy` reaches the Pi over ssh.

#: The Pi, as user@host, when it is not the one deploy.sh looks for by default. Not
#: called TARGET: that word is dbt's in semantics/Makefile, and an exported TARGET
#: from one would leak into the other.
PI ?=

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
	scripts/deploy.sh $(PI)

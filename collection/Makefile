# Tasks for the collection data product. Run them from this directory.
#
# `check` needs nothing but a checkout. `deploy` reaches the Pi over ssh.

#: The Pi, as user@host, when it is not the one deploy.sh looks for by default. Not
#: called TARGET: that word is dbt's in semantics/Makefile, and an exported TARGET
#: from one would leak into the other.
PI ?=

.PHONY: install check samples deploy

install:
	uv sync --locked

# The sample chunks under ../contracts/samples, rebuilt from the captured messages.
# Deterministic: a run that changes nothing leaves git clean.
samples:
	uv run python scripts/build_samples.py

# Everything that needs no Pi. The sensors and the radio are faked in the tests,
# so this passes on a Mac.
check:
	uv run ruff check .
	uv run ruff format --check .
	uv run ty check
	uv run pytest

deploy:
	scripts/deploy.sh $(PI)

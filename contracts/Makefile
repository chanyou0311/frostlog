# Tasks for the data contracts and the job that holds the two data products to them.
# Run them from this directory.
#
# `check` and `lint` need nothing but a checkout. `test` reaches the data itself,
# through Application Default Credentials; testing the collection contract reads the
# bucket through its S3 API, so it wants DATACONTRACT_S3_ACCESS_KEY_ID /
# DATACONTRACT_S3_SECRET_ACCESS_KEY (the job reads them from Secret Manager; a person
# exports their own HMAC key).

UV := uv run --project job
#: Which contract to test. Empty means all of them, which is what the daily run does.
CONTRACT ?=

.PHONY: install check lint test

install:
	uv sync --project job --locked

# Everything that needs no data.
check:
	cd job && uv run ruff check .
	cd job && uv run ruff format --check .
	cd job && uv run ty check
	cd job && uv run pytest

lint:
	for path in *.odcs.yaml; do $(UV) datacontract lint "$$path" || exit 1; done

# Always through the job, never straight through datacontract-cli: what passes here
# is then what passes at night. CONTRACT=collection (or semantics) narrows it, which
# is how one product is held to its word before the other exists. Another server --
# the CI dataset, say -- is asked for the way the job itself takes it, by setting the
# contract's server in the environment:
#   make test CONTRACT=semantics FROSTLOG_SEMANTICS_CONTRACT_SERVER=ci
test:
	$(UV) python -m frostlog_contracts $(if $(CONTRACT),--contract $(CONTRACT))

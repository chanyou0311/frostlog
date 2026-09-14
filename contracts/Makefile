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
#: Which server of the contract to test against. Empty means the one the job picks per
#: contract, which is production -- see below.
SERVER ?=

.PHONY: install check lint test

install:
	uv sync --project job

# Everything that needs no data.
check:
	cd job && uv run ruff check .
	cd job && uv run ruff format --check .
	cd job && uv run ty check
	cd job && uv run pytest

lint:
	for path in *.odcs.yaml; do $(UV) datacontract lint "$$path" || exit 1; done

# Two ways to ask, because there are two questions.
#
# Without SERVER: through the job, the same code path the nightly Cloud Run execution
# takes -- what passes here is what passes there. CONTRACT=collection (or semantics)
# narrows it, which is how one product is held to its word before the other exists.
#
# With SERVER: one contract against one named server, straight through
# datacontract-cli. This is for a server the job never picks, such as the CI dataset
# that `make -C semantics ci-warehouse` has just rebuilt from the sample chunks.
test:
ifeq ($(SERVER),)
	$(UV) python -m frostlog_contracts $(if $(CONTRACT),--contract $(CONTRACT))
else
	$(UV) datacontract test $(CONTRACT).odcs.yaml --server $(SERVER)
endif

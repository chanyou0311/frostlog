# Tasks for the semantic data product. Run them from this directory.
#
# Everything goes through the service's uv project, which holds dbt and
# datacontract-cli in its `transform` dependency group.
#
# `check` needs nothing but a checkout. Every other target talks to BigQuery
# through Application Default Credentials and reads FROSTLOG_BQ_PROJECT (the
# project), FROSTLOG_BQ_DATASET (prod) or FROSTLOG_BQ_DATASET_CI (ci).

UV := uv run --project service --group transform
DBT := $(UV) dbt
DIRS := --project-dir dbt --profiles-dir dbt
CONTRACTS := ../contracts
TARGET ?= prod

.PHONY: install check lint test parse build unit-test contract-test ci-warehouse lint-contracts

install:
	uv sync --project service --group transform

# Everything that needs no warehouse.
check: lint test parse

lint:
	cd service && uv run ruff check .
	cd service && uv run ruff format --check .
	cd service && uv run ty check

test:
	cd service && uv run pytest

parse:
	$(DBT) parse $(DIRS)

# A full rebuild; on arrival the service passes the dates instead.
build:
	$(DBT) build $(DIRS) --target $(TARGET) --full-refresh

# The dbt unit tests. They run their fixtures as queries, so they need BigQuery.
unit-test:
	$(DBT) test $(DIRS) --target $(TARGET) --select test_type:unit

# The data contracts against the data that is really there. Testing the raw
# contract reads the bucket through its S3 API, so it wants
# DATACONTRACT_S3_ACCESS_KEY_ID / DATACONTRACT_S3_SECRET_ACCESS_KEY (the service
# reads them from Secret Manager; a person exports their own HMAC key).
contract-test:
	$(UV) datacontract test $(CONTRACTS)/raw.odcs.yaml --server gcs
	$(UV) datacontract test $(CONTRACTS)/semantic.odcs.yaml --server production

# The whole warehouse, end to end, on the sample chunks in the repository: the CI
# dataset's raw tables are rebuilt from them through the service's own load path,
# the model is built from that, its unit tests run, and the result is held against
# the contract. Any step that fails stops the target. Needs Application Default
# Credentials and FROSTLOG_BQ_DATASET_CI.
ci-warehouse:
	$(UV) python -m frostlog_semantic.samples
	$(DBT) build $(DIRS) --target ci --full-refresh \
		--vars "$$($(UV) python -m frostlog_semantic.samples --target-dates)"
	$(DBT) test $(DIRS) --target ci --select test_type:unit
	$(UV) datacontract test $(CONTRACTS)/semantic.odcs.yaml --server ci

lint-contracts:
	$(UV) datacontract lint $(CONTRACTS)/raw.odcs.yaml
	$(UV) datacontract lint $(CONTRACTS)/semantic.odcs.yaml

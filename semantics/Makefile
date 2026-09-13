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
#: Passed through to the contract-test job, e.g. CONTRACT_ARGS="--contract collection".
CONTRACT_ARGS ?=

.PHONY: install check lint test parse build unit-test contract-test ci-warehouse lint-contracts

install:
	uv sync --project service --group transform

# Everything that needs no warehouse.
check: lint test parse

lint:
	cd service && uv run ruff check .
	cd service && uv run ruff format --check .
	cd service && uv run ty check

# The contract-rule tests transpile the contract SQL and run it on DuckDB (transform group).
test:
	cd service && uv run --group transform pytest

parse:
	$(DBT) parse $(DIRS)

# Every table is rebuilt on each run, so this is plain `dbt build`: no refresh flag
# and no date variables. The service runs the same thing without the unit tests
# (--exclude-resource-type unit_test), which belong to CI rather than to every hour.
build:
	$(DBT) build $(DIRS) --target $(TARGET)

# The dbt unit tests. They run their fixtures as queries, so they need BigQuery.
unit-test:
	$(DBT) test $(DIRS) --target $(TARGET) --select test_type:unit

# The data contracts against the data that is really there. Testing the raw
# contract reads the bucket through its S3 API, so it wants
# DATACONTRACT_S3_ACCESS_KEY_ID / DATACONTRACT_S3_SECRET_ACCESS_KEY (the service
# job reads them from Secret Manager; a person exports their own HMAC key).
# Both contracts against production, through the job that does it daily — the same
# code path, so what passes here is what passes there. `--contract collection` (or
# semantics) narrows it, which is how one product is held to its word before the
# other exists. Needs Application Default Credentials.
contract-test:
	$(UV) python -m frostlog_contracts $(CONTRACT_ARGS)

# The whole warehouse, end to end, on the sample chunks in the repository: the CI
# dataset's raw tables are rebuilt from them with the same kind of load job the
# transfer issues in production (there is no transfer pointed at a CI bucket), the
# model is built from that, its unit tests run -- this is where they run, the hourly
# build leaves them out -- and the result is held against the contract. Any step that
# fails stops the target. Needs Application Default Credentials and
# FROSTLOG_BQ_DATASET_CI.
#
# Two deliveries keep the real chunk boundary: the report before the cut gets its
# successor from another chunk, and the contract checks the resulting held_seconds.
# Build once after both arrive, because a full rebuild uses no previous fact state;
# building between deliveries would add no coverage of that boundary.
ci-warehouse:
	$(UV) python -m frostlog_semantics.samples --part 1
	$(UV) python -m frostlog_semantics.samples --part 2
	$(DBT) build $(DIRS) --target ci
	$(DBT) test $(DIRS) --target ci --select test_type:unit
	$(UV) datacontract test $(CONTRACTS)/semantics.odcs.yaml --server ci

lint-contracts:
	$(UV) datacontract lint $(CONTRACTS)/collection.odcs.yaml
	$(UV) datacontract lint $(CONTRACTS)/semantics.odcs.yaml

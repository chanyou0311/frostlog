# Tasks for the semantic data product. Run them from this directory.
#
# Everything goes through the service's uv project, which holds dbt in its
# `transform` dependency group. The contracts are held to by a job of their own
# (../contracts), which this delegates to.
#
# `check` needs nothing but a checkout. Every other target talks to BigQuery
# through Application Default Credentials and reads FROSTLOG_BQ_PROJECT (the
# project), FROSTLOG_BQ_DATASET (prod) or FROSTLOG_BQ_DATASET_CI (ci).

UV := uv run --project service --group transform
DBT := $(UV) dbt
DIRS := --project-dir dbt --profiles-dir dbt
TARGET ?= prod

.PHONY: install check lint test parse build unit-test ci-warehouse

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

# Every table is rebuilt on each run, so this is plain `dbt build`: no refresh flag
# and no date variables. The service runs the same thing without the unit tests
# (--exclude-resource-type unit_test), which belong to CI rather than to every hour.
build:
	$(DBT) build $(DIRS) --target $(TARGET)

# The dbt unit tests. They run their fixtures as queries, so they need BigQuery.
unit-test:
	$(DBT) test $(DIRS) --target $(TARGET) --select test_type:unit

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
	$(MAKE) -C ../contracts test CONTRACT=semantics SERVER=ci

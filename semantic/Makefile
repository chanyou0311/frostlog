# Tasks for the semantic data product. Run them from this directory.
#
# Everything goes through the service's uv project, which holds dbt and
# datacontract-cli in its `transform` dependency group.

UV := uv run --project service --group transform
DBT := $(UV) dbt
DIRS := --project-dir dbt --profiles-dir dbt
TARGET ?= prod

.PHONY: install check test parse build unit-test contract-test lint breaking

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

# The data contracts against the data that is really there.
contract-test:
	$(UV) datacontract test ../contracts/raw.odcs.yaml --server gcs
	$(UV) datacontract test ../contracts/semantic.odcs.yaml --server ci

breaking:
	$(UV) datacontract lint ../contracts/raw.odcs.yaml
	$(UV) datacontract lint ../contracts/semantic.odcs.yaml

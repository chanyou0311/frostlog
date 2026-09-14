# Tasks for the frostlog-controller unit. Run them from this directory.
#
# `check` needs nothing but a checkout: nmcli and the gateway's command socket
# are both faked in the tests, so this passes on a Mac.

#: The Pi, as user@host, when it is not the one deploy.sh looks for by default.
PI ?=

.PHONY: install check deploy

install:
	uv sync --locked

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run ty check
	uv run pytest

deploy:
	scripts/deploy.sh $(PI)

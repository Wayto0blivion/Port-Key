# Every target runs in containers (compose.dev.yaml). Nothing is installed on the host.
#
# P is the compose project name. Use your own when others run checks at the same time:
#   make check P=portkey-wp3
P ?= portkey
DC := docker compose -f compose.dev.yaml -p $(P)
RUN := $(DC) run --rm --no-deps
HOST_UID ?= $(shell id -u)
HOST_GID ?= $(shell id -g)
export HOST_UID HOST_GID
# Narrow lint/fmt/unit to your own paths: make fmt FILES="portkey/keys tests/unit/keys"
FILES ?= .
TESTS ?= tests/unit

.PHONY: help build lint fmt types unit check integration e2e frontend lint-js lock vendor-xterm clean shell

help:
	@echo "make build | lint | fmt | types | unit | check | lint-js | frontend | integration | e2e | clean"
	@echo "Pass P=<project> for a private compose project (default: portkey)."

build:
	$(DC) build test lint-js

lint:
	$(RUN) test ruff check $(FILES)
	$(RUN) test ruff format --check $(FILES)

fmt:
	$(RUN) test ruff format $(FILES)
	$(RUN) test ruff check --fix $(FILES)

types:
	$(RUN) test mypy

unit:
	$(RUN) test pytest $(TESTS)

check: lint types unit

lint-js:
	$(RUN) lint-js

# Frontend shell tests (tests/frontend): Chromium against a mock API, no network. Narrow with
# FRONTEND="tests/frontend/test_units.py -k contract".
FRONTEND ?= tests/frontend
frontend:
	$(DC) --profile frontend build frontend
	$(DC) --profile frontend run --rm --no-deps frontend pytest $(FRONTEND) -p no:cacheprovider

integration:
	$(DC) --profile integration build sshd integration
	$(DC) --profile integration run --rm integration; rc=$$?; \
	$(DC) --profile integration down -v --remove-orphans; exit $$rc

e2e:
	$(DC) --profile e2e build sshd app evil-origin e2e
	$(DC) --profile e2e run --rm e2e; rc=$$?; \
	$(DC) --profile e2e down -v --remove-orphans; exit $$rc

shell:
	$(RUN) test bash

lock:
	scripts/lock.sh

vendor-xterm:
	scripts/vendor-xterm.sh

clean:
	$(DC) --profile integration --profile e2e --profile frontend down -v --remove-orphans

# Sepsis early warning on FHIR — task entry points.
#
# The canonical path from a clean clone to every reported number:
#
#     make setup && make data && make train && make eval
#
# PYTHONHASHSEED is set here rather than left to the shell: without it, Python's
# string hashing varies per process and any code that iterates a set can reorder
# (AGENTS-ENGINEERING.md §23).

export PYTHONHASHSEED := 0

# Recipes run under bash, not the platform default. On Windows, make would
# otherwise hand recipes to cmd.exe, which has none of the tools used here and
# cannot run scripts/*.sh at all. Git Bash satisfies this.
SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c

UV      := uv
PYTHON  := $(UV) run python
COHORT  := data/interim/cohort.parquet

.DEFAULT_GOAL := help
.PHONY: help setup data fetch fixtures verify train eval up down test test-fast lint format clean

help:  ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-12s\033[0m %s\n", $$1, $$2}'

# --- environment -------------------------------------------------------------

setup:  ## Create the Python environment from uv.lock
	$(UV) sync --all-groups

# --- data (Phase 1) ----------------------------------------------------------

fetch:  ## Download the Challenge 2019 data and the official scorer (idempotent)
	bash scripts/fetch_data.sh

verify:  ## Verify data/CHECKSUMS.sha256 against what is on disk
	bash scripts/verify_checksums.sh

fixtures:  ## Regenerate the synthetic test cohort
	$(PYTHON) scripts/make_fixtures.py

data: verify  ## Build data/interim/cohort.parquet and docs/data_notes.md
	$(PYTHON) scripts/build_cohort.py
	$(PYTHON) scripts/data_notes.py

# --- model (Phases 2-4) ------------------------------------------------------

train:
	@echo "make train — not implemented until Phase 2 (baseline and scoring harness)." && exit 1

eval:
	@echo "make eval — not implemented until Phase 3 (model and cross-site matrix)." && exit 1

# --- services (Phases 5-7) ---------------------------------------------------

up:
	@echo "make up — not implemented until Phase 5 (FHIR layer)." && exit 1

down:
	@echo "make down — not implemented until Phase 5 (FHIR layer)." && exit 1

# --- quality -----------------------------------------------------------------

test:  ## Run the full Python suite (tiers 1-3; no real data needed)
	$(UV) run pytest

test-fast:  ## Tiers 1-3 only, the CI gate that must stay under two minutes
	$(UV) run pytest tests/unit tests/invariant tests/fixture tests/regression

lint:  ## Static checks
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format:  ## Apply formatting
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

clean:  ## Remove derived data, keeping data/raw/ intact
	rm -f $(COHORT)
	rm -rf .pytest_cache .ruff_cache

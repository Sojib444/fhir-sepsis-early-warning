"""Shared pytest fixtures.

Everything here runs off tests/fixtures/mini_cohort/, never the real data, so
the whole suite works with data/raw/ empty (AGENTS-ENGINEERING.md §18).
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from sepsis.io import build_cohort

FIXTURE_COHORT_DIR = Path(__file__).parent / "fixtures" / "mini_cohort"

SITES = {"A": "training_setA", "B": "training_setB"}


@pytest.fixture(scope="session")
def fixture_dir() -> Path:
    if not FIXTURE_COHORT_DIR.is_dir():
        pytest.fail(
            "tests/fixtures/mini_cohort/ is missing — run `uv run python scripts/make_fixtures.py`"
        )
    return FIXTURE_COHORT_DIR


@pytest.fixture(scope="session")
def cohort(fixture_dir: Path) -> pl.DataFrame:
    """The full fixture cohort, both sites, no inclusion rule applied."""
    # max_workers=1 keeps the suite free of process-pool start-up cost.
    return build_cohort(fixture_dir, SITES, max_workers=1)

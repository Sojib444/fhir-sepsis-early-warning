"""Tier 2 — provenance and configuration invariants (§23).

The determinism requirement in §19 is "identical seed produces byte-identical
metrics.json except for the timestamp field". metrics.json arrives in Phase 2;
what is testable now is the provenance block it will be built from.
"""

from __future__ import annotations

import json

from sepsis.config import (
    CLINICAL_VARIABLES,
    DEMOGRAPHICS,
    FEATURE_COLUMNS,
    LABS,
    PSV_COLUMNS,
    VITALS,
    load_config,
)
from sepsis.provenance import provenance

SEED = 20190801


# --- the column vocabulary is a property of the data, not a choice ----------


def test_there_are_forty_feature_columns_and_one_label():
    assert len(VITALS) == 8
    assert len(LABS) == 26
    assert len(DEMOGRAPHICS) == 6
    assert len(FEATURE_COLUMNS) == 40
    assert len(PSV_COLUMNS) == 41


def test_clinical_variables_exclude_demographics():
    """Window statistics belong to time-varying measurements only."""
    assert len(CLINICAL_VARIABLES) == 34
    for column in DEMOGRAPHICS:
        assert column not in CLINICAL_VARIABLES


def test_column_names_are_unique():
    assert len(set(PSV_COLUMNS)) == len(PSV_COLUMNS)


# --- config.yaml is the single source of truth ------------------------------


def test_config_matches_the_recorded_decisions():
    config = load_config()

    assert config.min_icu_hours == 8  # D3
    assert config.windows == [6, 12, 24]  # D4
    assert config.train_fraction == 0.8  # §7.3
    assert isinstance(config.seed, int)


def test_config_paths_resolve_under_the_repository_root():
    config = load_config()
    assert config.path("cohort").is_relative_to(config.root)


# --- provenance -------------------------------------------------------------


def test_provenance_is_stable_apart_from_the_timestamp():
    first = provenance(SEED)
    second = provenance(SEED)

    del first["timestamp_utc"]
    del second["timestamp_utc"]
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_provenance_records_what_a_reader_needs_to_reproduce_a_number():
    block = provenance(SEED)
    for field in (
        "git_commit",
        "git_tree_dirty",
        "seed",
        "python_version",
        "packages",
        "input_data_sha256",
        "timestamp_utc",
    ):
        assert field in block

    assert block["seed"] == SEED
    assert block["packages"]["polars"] != "not installed"


def test_provenance_is_json_serialisable():
    json.dumps(provenance(SEED))

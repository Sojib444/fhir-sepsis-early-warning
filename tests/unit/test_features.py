"""Tier 1/2 — feature-construction invariants (AGENTS.md §2.3).

The forward-fill tests are the ones that matter academically: a feature at
hour t must never depend on data from after hour t.
"""

from __future__ import annotations

import polars as pl

from sepsis.config import CLINICAL_VARIABLES
from sepsis.features import design_matrix, feature_columns, minimal_features, missingness_features


def _example_cohort() -> pl.DataFrame:
    """One patient, five hourly rows, with a gap and an out-of-order feature
    that the builder must not mix up."""
    cols = {
        "patient_id": ["p1"] * 5,
        "site": ["A"] * 5,
        "hour": [0, 1, 2, 3, 4],
        "HR": [70.0, None, None, 75.0, None],
        "Lactate": [None, 3.0, None, None, None],
        "O2Sat": [98.0, 97.0, 99.0, None, 96.0],
    }
    for var in CLINICAL_VARIABLES:
        cols.setdefault(var, [None] * 5)
    cols["Age"] = [65.0] * 5
    cols["Gender"] = [0] * 5
    cols["Unit1"] = [None] * 5
    cols["Unit2"] = [None] * 5
    cols["HospAdmTime"] = [-3.0] * 5
    cols["ICULOS"] = [1.0, 2.0, 3.0, 4.0, 5.0]
    cols["SepsisLabel"] = [0, 0, 0, 0, 0]
    return pl.DataFrame(cols)


def test_forward_fill_never_reads_the_future():
    cohort = _example_cohort()
    # Corrupt every clinical value from hour 3 onward with an absurd sentinel.
    corrupted = cohort.clone()
    for var in CLINICAL_VARIABLES:
        corrupted = corrupted.with_columns(
            pl.when(pl.col("hour") >= 3).then(999.9).otherwise(pl.col(var)).alias(var)
        )

    features = minimal_features(cohort)
    features_corrupted = minimal_features(corrupted)

    for t in (0, 1, 2):
        before = features.filter(pl.col("hour") == t)
        after = features_corrupted.filter(pl.col("hour") == t)
        assert before.select(feature_columns(before)).equals(
            after.select(feature_columns(after))
        ), f"features at hour {t} depend on future data"


def test_hours_since_measured_is_backward_looking_and_correct():
    cohort = _example_cohort()
    features = missingness_features(cohort)
    hsm = (
        features.filter(pl.col("hour") == 1).get_column("Lactate_hours_since_measured").to_list()[0]
    )
    assert hsm == 0  # measured this hour

    hsm_3 = (
        features.filter(pl.col("hour") == 3).get_column("Lactate_hours_since_measured").to_list()[0]
    )
    assert hsm_3 == 2  # last measured 2 hours ago


def test_missingness_indicators_match_the_raw_values():
    cohort = _example_cohort()
    features = minimal_features(cohort)
    for var in CLINICAL_VARIABLES:
        indicator = var + "_missing"
        flag_rows = features.select([indicator, "hour"]).iter_rows()
        raw_rows = cohort.select([var, "hour"]).iter_rows()
        for (flag, fhour), (raw, rhour) in zip(flag_rows, raw_rows, strict=True):
            assert fhour == rhour
            assert flag == (1 if raw is None else 0)


def test_last_is_the_carried_forward_value_within_patient():
    features = minimal_features(_example_cohort())
    last = features.get_column("HR_last").to_list()
    assert last == [70.0, 70.0, 70.0, 75.0, 75.0]

    never_measured = features.get_column("AST_last").to_list()
    assert all(v is None for v in never_measured)


def test_design_matrix_carries_side_columns_and_no_extra_ones():
    design = design_matrix(_example_cohort(), minimal_features)
    assert design.columns[0:5] == ["patient_id", "site", "hour", "SepsisLabel", "HR_missing"]
    assert "patient_id" in design.columns
    footer = feature_columns(design)
    assert "patient_id" not in footer
    assert "hour" not in footer


def test_minimal_features_contains_exactly_the_agree_set():
    design = design_matrix(_example_cohort(), minimal_features)
    cols = feature_columns(design)
    expected = (
        {f"{var}_missing" for var in CLINICAL_VARIABLES}
        | {f"{var}_last" for var in CLINICAL_VARIABLES}
        | {"Age", "Gender", "HospAdmTime", "ICULOS"}
    )
    assert set(cols) == expected

"""Tier 1/2 — feature-construction invariants (AGENTS.md §2.3).

The forward-fill tests are the ones that matter academically: a feature at
hour t must never depend on data from after hour t.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from sepsis.config import CLINICAL_VARIABLES
from sepsis.features import (
    _window_stats_one,
    design_matrix,
    feature_columns,
    minimal_features,
    missingness_features,
    window_feature_columns,
    window_features_frame,
)


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


# --- window statistics (Phase 3) ---------------------------------------------


def _window_cohort_with_known_values() -> pl.DataFrame:
    """One patient with known HR values for manual window arithmetic."""
    cols = {
        "patient_id": ["w1"] * 5,
        "site": ["A"] * 5,
        "hour": [0, 1, 2, 3, 4],
        "HR": [1.0, 2.0, None, 4.0, 5.0],
    }
    for var in CLINICAL_VARIABLES:
        cols.setdefault(var, [None] * 5)
    cols["Age"] = [60.0] * 5
    cols["Gender"] = [0] * 5
    cols["Unit1"] = [None] * 5
    cols["Unit2"] = [None] * 5
    cols["HospAdmTime"] = [-1.0] * 5
    cols["ICULOS"] = [7.0, 8.0, 9.0, 10.0, 11.0]
    cols["SepsisLabel"] = [0] * 5
    return pl.DataFrame(cols)


def test_window_stats_one_matches_hand_arithmetic():
    values = np.array([1.0, 2.0, np.nan, 4.0, 5.0])
    stats = _window_stats_one(values, w=3)
    np.testing.assert_allclose(stats["min"], [1.0, 1.0, 1.0, 2.0, 4.0], equal_nan=True)
    np.testing.assert_allclose(stats["max"], [1.0, 2.0, 2.0, 4.0, 5.0], equal_nan=True)
    np.testing.assert_allclose(stats["mean"], [1.0, 1.5, 1.5, 3.0, 4.5], equal_nan=True)
    np.testing.assert_allclose(stats["last"], [1.0, 2.0, 2.0, 4.0, 5.0], equal_nan=True)
    # slopes: (0,1)-(1,2) -> 1; (1,2)-(3,4) -> 1; (3,4)-(4,5) -> 1
    np.testing.assert_allclose(stats["slope"], [np.nan, 1.0, 1.0, 1.0, 1.0], equal_nan=True)


def test_window_features_never_read_the_future():
    """Corrupting future hours must leave hours <= t untouched (AGENTS.md §2.3)."""
    cohort = _window_cohort_with_known_values()
    corrupted = cohort.clone()
    for var in CLINICAL_VARIABLES:
        corrupted = corrupted.with_columns(
            pl.when(pl.col("hour") >= 3).then(999.9).otherwise(pl.col(var)).alias(var)
        )

    columns = window_feature_columns((6, 12, 24))
    features = window_features_frame(cohort, (6, 12, 24))
    features_corrupted = window_features_frame(corrupted, (6, 12, 24))

    for t in (0, 1, 2):
        before = features.filter(pl.col("hour") == t)
        after = features_corrupted.filter(pl.col("hour") == t)
        assert before.select(columns).equals(after.select(columns)), (
            f"window features at hour {t} depend on future data"
        )


def test_window_feature_columns_are_counted_and_ordered():
    columns = window_feature_columns((6, 12, 24))
    per_var = 2 + 3 * len(("min", "max", "mean", "last", "slope"))
    assert len(columns) == len(CLINICAL_VARIABLES) * per_var + 4
    # the order contract: missingness first, then window stats per window
    assert columns.index("HR_missing") < columns.index("HR_min_6") < columns.index("HR_min_24")
    assert "HR_slope_24" in columns
    assert columns[-4:] == ["Age", "Gender", "HospAdmTime", "ICULOS"]


def test_window_frame_keeps_side_columns_and_hours_since():
    cohort = _window_cohort_with_known_values()
    features = window_features_frame(cohort, (6,))
    # HR measured at hours 0,1,3,4 (missing at 2): hours_since at 2 is 1.
    hsm = features.get_column("HR_hours_since_measured").to_list()
    assert hsm == [0, 0, 1, 0, 0]
    assert features.get_column("HR_missing").to_list() == [0, 0, 1, 0, 0]

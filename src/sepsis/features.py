"""Feature construction (AGENTS.md §8.3, §9.1).

Two builders live here:

  minimal_features — the Phase 2 baseline set: the last observed value of every
    clinical variable carried forward within patient only, a `{var}_missing`
    indicator, and the demographic columns.
  window_features — the Phase 3 set: per-window min/max/mean/last/slope for
    windows of 6h/12h/24h, plus `{var}_missing` and
    `{var}_hours_since_measured`, plus the demographics.

Rule §2.3 is structural rather than tested-after-the-fact: every feature at
hour t is a function of rows with hour <= t of the same patient, computed with
no forward-looking operations. This module carries no global statistics of any
kind, so it cannot leak information across patients either (§2.4).

Each builder returns ONLY the side columns plus its features — raw clinical
columns are consumed by the construction and never reach the design matrix.
"""

from __future__ import annotations

import polars as pl

from sepsis.config import CLINICAL_VARIABLES, LABEL, META_COLUMNS

#: The demographic columns that enter both feature sets. `Unit1`/`Unit2` are
#: kept out of both: they describe where the recording happened, which is
#: exactly the site difference the cross-site matrix is about, and including
#: them would hand the model a site label it should not have.
BASELINE_DEMOGRAPHICS: tuple[str, ...] = ("Age", "Gender", "HospAdmTime", "ICULOS")

#: Side columns carried through feature construction so evaluation can map a
#: prediction back to a patient, hour and label.
SIDE_COLUMNS: tuple[str, ...] = META_COLUMNS + (LABEL,)


def _sorted_by_hour(cohort: pl.DataFrame) -> pl.DataFrame:
    """Forward fills and windows need a deterministic order within a patient."""
    return cohort.sort(["patient_id", "hour"])


def _side(_frame: pl.DataFrame) -> list[pl.Expr]:
    return [pl.col(c) for c in SIDE_COLUMNS if c in _frame.columns]


def missingness_features(cohort: pl.DataFrame) -> pl.DataFrame:
    """Return `{var}_missing` and `{var}_hours_since_measured` per clinical var.

    The measured hour is forward-filled so that `hours_since_measured` at hour
    t is the distance to the most recent measurement at or before t — never to
    one in the future.
    """
    frame = _sorted_by_hour(cohort)
    exprs: list[pl.Expr] = []
    for var in CLINICAL_VARIABLES:
        measured_hour = pl.when(pl.col(var).is_not_null()).then(pl.col("hour")).otherwise(None)
        exprs.append(pl.col(var).is_null().cast(pl.Int8).alias(f"{var}_missing"))
        exprs.append(
            (pl.col("hour") - measured_hour.forward_fill().over("patient_id")).alias(
                f"{var}_hours_since_measured"
            )
        )
    return frame.select(_side(frame) + exprs)


def minimal_features(cohort: pl.DataFrame) -> pl.DataFrame:
    """Phase 2 baseline set.

    `{var}_last` is the last observed value carried forward within patient only
    (never across patients, never from the future). Rows before any measurement
    stay null, so the missingness information is not double counted. No
    imputation happens here; the logistic-regression baseline imputes with
    statistics fit on train only (see src/sepsis/train.py).
    """
    frame = _sorted_by_hour(cohort)
    exprs: list[pl.Expr] = []
    for var in CLINICAL_VARIABLES:
        exprs.append(pl.col(var).is_null().cast(pl.Int8).alias(f"{var}_missing"))
        exprs.append(pl.col(var).forward_fill().over("patient_id").alias(f"{var}_last"))
    exprs += [pl.col(name) for name in BASELINE_DEMOGRAPHICS]
    return frame.select(_side(frame) + exprs)


def design_matrix(cohort: pl.DataFrame, feature_fn) -> pl.DataFrame:
    """Build the modelling frame: side columns first, then the features.

    `cohort` is the long per-patient-hour table; `feature_fn` is one of the
    builders above. Row order is preserved so evaluation can line features up
    with labels and patient ids.
    """
    return feature_fn(_sorted_by_hour(cohort))


def feature_columns(design: pl.DataFrame) -> list[str]:
    """The feature columns of a design matrix, in their frozen order.

    The order is part of the model contract: it is what training fits and what
    serving reproduces (Phase 6). The side columns are excluded by name, never
    by position.
    """
    return [c for c in design.columns if c not in SIDE_COLUMNS]

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

import warnings

import numpy as np
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


# --- window statistics (Phase 3) ---------------------------------------------
#
# All stats are backward-looking over the LAST `w` rows of a patient. Because
# ICULOS is gap-free (docs/data_notes.md), w rows are w hours. The numpy
# helpers here take a single patient's column values (NaN = missing) and return
# per-hour arrays; `window_features_frame` assembles them into a polars frame.

WINDOW_STATISTICS: tuple[str, ...] = ("min", "max", "mean", "last", "slope")


def _window_stats_one(values: np.ndarray, w: int) -> dict[str, np.ndarray]:
    """min/max/mean/last/slope over the trailing `w` rows, ignoring misses.

    `values` is one patient's series with NaN for unmeasured hours, sorted by
    hour. Slopes are in per-row units (a row is one hour).
    """
    n = len(values)
    if n == 0:
        return {stat: np.array([], dtype=np.float64) for stat in WINDOW_STATISTICS}

    x = np.concatenate([np.full(w - 1, np.nan), values])
    win = np.lib.stride_tricks.sliding_window_view(x, w)  # (n, w)
    valid = ~np.isnan(win)
    cnt = valid.sum(axis=1)

    with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN slices -> NaN
        mn = np.where(cnt > 0, np.nanmin(win, axis=1), np.nan)
        mx = np.where(cnt > 0, np.nanmax(win, axis=1), np.nan)
        sm = np.where(cnt > 0, np.nansum(win, axis=1), np.nan)
        mean = np.where(cnt > 0, sm / np.maximum(cnt, 1), np.nan)

        # last = most recent measured value in the window. The first valid column
        # of the reversed window is the most recent valid value.
        first_valid_flipped = valid[:, ::-1].argmax(axis=1)  # w when none valid
        last_col = (w - 1) - first_valid_flipped
        last = np.where(cnt > 0, win[np.arange(n), last_col], np.nan)

        # slope over measured values with their row positions (affine in position,
        # so using row index in place of ICU hour changes nothing).
        positions = np.arange(n, dtype=np.float64)
        hpad = np.concatenate([np.full(w - 1, np.nan), positions])
        win_h = np.lib.stride_tricks.sliding_window_view(hpad, w)
        hh = np.where(valid, win_h, 0.0)
        xx = np.where(valid, win, 0.0)
        sx = xx.sum(axis=1)
        sh = hh.sum(axis=1)
        shx = (hh * xx).sum(axis=1)
        sh2 = (hh * hh).sum(axis=1)
        den = cnt * sh2 - sh * sh
        slope = np.where((cnt >= 2) & (np.abs(den) > 1e-12), (cnt * shx - sh * sx) / den, np.nan)

    return {"min": mn, "max": mx, "mean": mean, "last": last, "slope": slope}


def window_feature_columns(windows: tuple[int, ...] = (6, 12, 24)) -> list[str]:
    """The exact column names and order of the Phase 3 feature set.

    Order is part of the model contract (serving reproduces it), so the list is
    produced here and nothing else is allowed to invent its own order.
    """
    columns: list[str] = []
    for var in CLINICAL_VARIABLES:
        columns.append(f"{var}_missing")
        columns.append(f"{var}_hours_since_measured")
    for window in windows:
        for var in CLINICAL_VARIABLES:
            for stat in WINDOW_STATISTICS:
                columns.append(f"{var}_{stat}_{window}")
    columns += list(BASELINE_DEMOGRAPHICS)
    return columns


def _feature_block(
    patient: pl.DataFrame, columns: list[str], windows: tuple[int, ...]
) -> np.ndarray:
    """One patient's feature block, float32, in the frozen column order.

    `patient` is that patient's rows sorted by hour. Everything here is
    backward-looking within the patient.
    """
    index = {name: i for i, name in enumerate(columns)}
    n = patient.height
    block = np.full((n, len(columns)), np.nan, dtype=np.float32)
    hour = patient.get_column("hour").to_numpy().astype(np.int64)

    for var in CLINICAL_VARIABLES:
        col = patient.get_column(var).to_numpy().astype(np.float64)
        measured = ~np.isnan(col)

        missing = np.isnan(col).astype(np.float32)
        block[:, index[f"{var}_missing"]] = missing

        last_measured_hour = np.maximum.accumulate(np.where(measured, hour, np.iinfo(np.int64).min))
        never_measured = last_measured_hour == np.iinfo(np.int64).min
        hsm = np.where(never_measured, np.nan, hour - last_measured_hour)
        block[:, index[f"{var}_hours_since_measured"]] = hsm.astype(np.float32)

        for w in windows:
            stats = _window_stats_one(col, w)
            for stat in WINDOW_STATISTICS:
                block[:, index[f"{var}_{stat}_{w}"]] = stats[stat].astype(np.float32)

    for name in BASELINE_DEMOGRAPHICS:
        block[:, index[name]] = patient.get_column(name).to_numpy().astype(np.float32)
    return block


def _side_block(patient: pl.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    return (
        patient.get_column("patient_id").to_numpy(),
        patient.get_column("site").to_numpy(),
        patient.get_column("hour").to_numpy().astype(np.int64),
        patient.get_column(LABEL).to_numpy().astype(np.int64),
    )


def window_features_frame(
    cohort: pl.DataFrame, windows: tuple[int, ...] = (6, 12, 24)
) -> pl.DataFrame:
    """The Phase 3 feature set as a frame.

    Exact same numbers as the memory-mapped builder (both call `_feature_block`
    and `window_feature_columns`), kept for tests and small cohorts.
    """
    columns = window_feature_columns(windows)
    frame = _sorted_by_hour(cohort)

    blocks: list[np.ndarray] = []
    sides: list[dict[str, np.ndarray]] = []
    for _, patient in frame.group_by("patient_id", maintain_order=True):
        blocks.append(_feature_block(patient, columns, windows))
        pids, sites, hours, labels = _side_block(patient)
        sides.append({"patient_id": pids, "site": sites, "hour": hours, LABEL: labels})

    x = np.vstack(blocks)
    side = pl.DataFrame({name: np.concatenate([s[name] for s in sides]) for name in sides[0]})
    features = pl.DataFrame({columns[i]: x[:, i] for i in range(x.shape[1])})
    return side.hstack(features)


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

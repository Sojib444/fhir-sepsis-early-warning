"""Cohort inclusion (D3) and patient-level splitting.

Two rules from AGENTS.md §2 are enforced here and nowhere else, so that there
is exactly one place to audit:

  §2.1  Splits are by `patient_id`. A patient's hours never straddle a split.
  §2.2  `training_setB` is a held-out external test set. Loading it emits a
        warning to stderr on every call.
"""

from __future__ import annotations

import sys
import warnings

import numpy as np
import polars as pl

#: Emitted by `load_site_b`. Tests assert on this exact text.
SITE_B_WARNING = (
    "HELD-OUT SITE B LOADED: training_setB is an external test set. "
    "It must never be used for training, hyperparameter tuning, imputation "
    "statistics, scaler fitting, calibration, or any design iteration "
    "(AGENTS.md §2.2)."
)


def patient_hour_counts(cohort: pl.DataFrame) -> pl.DataFrame:
    """One row per patient: site and number of hourly records."""
    return (
        cohort.group_by(["site", "patient_id"])
        .agg(pl.len().alias("n_hours"))
        .sort(["site", "patient_id"])
    )


def apply_inclusion(cohort: pl.DataFrame, min_icu_hours: int) -> pl.DataFrame:
    """D3: drop patients with fewer than `min_icu_hours` hourly records.

    This is the only exclusion in the study. It is applied to whole patients,
    never to individual rows.
    """
    counts = patient_hour_counts(cohort)
    keep = counts.filter(pl.col("n_hours") >= min_icu_hours).get_column("patient_id").to_list()
    return cohort.filter(pl.col("patient_id").is_in(keep))


def inclusion_report(cohort: pl.DataFrame, min_icu_hours: int) -> pl.DataFrame:
    """Per-site count of patients and hours removed by D3.

    D3 requires this to be reported, and flagged if it removes more than 3% of
    patients at either site.
    """
    counts = patient_hour_counts(cohort)
    return (
        counts.group_by("site")
        .agg(
            pl.len().alias("patients_before"),
            (pl.col("n_hours") >= min_icu_hours).sum().alias("patients_after"),
            pl.col("n_hours").sum().alias("hours_before"),
            pl.col("n_hours").filter(pl.col("n_hours") >= min_icu_hours).sum().alias("hours_after"),
        )
        .with_columns(
            (pl.col("patients_before") - pl.col("patients_after")).alias("patients_dropped"),
            (pl.col("hours_before") - pl.col("hours_after")).alias("hours_dropped"),
        )
        .with_columns(
            (
                100.0
                * (pl.col("patients_before") - pl.col("patients_after"))
                / pl.col("patients_before")
            ).alias("patients_dropped_pct"),
            (
                100.0 * (pl.col("hours_before") - pl.col("hours_after")) / pl.col("hours_before")
            ).alias("hours_dropped_pct"),
        )
        .sort("site")
    )


def split_patients(
    patient_ids: list[str] | pl.Series,
    train_fraction: float,
    seed: int,
) -> tuple[list[str], list[str]]:
    """Split patient IDs into train and validation, deterministically.

    The IDs are sorted before shuffling, so the result depends only on the set
    of IDs and the seed — not on the order they arrived in, nor on the
    filesystem, nor on the number of workers that parsed them.
    """
    if not 0.0 < train_fraction < 1.0:
        raise ValueError(f"train_fraction must be in (0, 1), got {train_fraction}")

    ids = sorted(set(patient_ids.to_list() if isinstance(patient_ids, pl.Series) else patient_ids))
    if not ids:
        raise ValueError("no patient IDs to split")

    order = np.random.default_rng(seed).permutation(len(ids))
    n_train = int(round(train_fraction * len(ids)))
    n_train = min(max(n_train, 1), len(ids) - 1)  # both sides always non-empty

    train = sorted(ids[i] for i in order[:n_train])
    val = sorted(ids[i] for i in order[n_train:])
    return train, val


def split_site_a(
    cohort: pl.DataFrame,
    train_fraction: float,
    seed: int,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Split site A into train and validation frames, by patient."""
    site_a = cohort.filter(pl.col("site") == "A")
    if site_a.height == 0:
        raise ValueError("cohort contains no site A rows")

    train_ids, val_ids = split_patients(
        site_a.get_column("patient_id").unique(), train_fraction, seed
    )
    return (
        site_a.filter(pl.col("patient_id").is_in(train_ids)),
        site_a.filter(pl.col("patient_id").is_in(val_ids)),
    )


def load_site_b(cohort: pl.DataFrame) -> pl.DataFrame:
    """Return the site-B rows, warning loudly on every call.

    Site B is scored once per phase, after every design choice is frozen. The
    warning exists so that an accidental call is visible in the logs of any run
    that produced a number.
    """
    print(f"WARNING: {SITE_B_WARNING}", file=sys.stderr, flush=True)
    warnings.warn(SITE_B_WARNING, UserWarning, stacklevel=2)

    site_b = cohort.filter(pl.col("site") == "B")
    if site_b.height == 0:
        raise ValueError("cohort contains no site B rows")
    return site_b


def split_site_b(
    cohort: pl.DataFrame,
    train_fraction: float,
    seed: int,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Split site B into a training frame and a held-out evaluation frame.

    AGENTS.md §2.2 forbids touching `training_setB`; §9 still requires the
    matrix rows B→A and A+B→B. The resolution recorded in docs/decisions.md:
    site B is split by patient into `b_train` (the §9 training data) and
    `b_eval` (the external test set, scored exactly once per phase after every
    design choice is frozen). `b_eval` keeps the §2.2 warning on every load.
    """
    site_b = load_site_b(cohort)  # warns every call, by design
    b_train_ids, b_eval_ids = split_patients(
        site_b.get_column("patient_id").unique(), train_fraction, seed
    )
    return (
        site_b.filter(pl.col("patient_id").is_in(b_train_ids)),
        site_b.filter(pl.col("patient_id").is_in(b_eval_ids)),
    )


def assert_disjoint(*splits: pl.DataFrame) -> None:
    """Raise if any `patient_id` appears in more than one split.

    Called by the pipeline itself, not only by tests: a leak that only the test
    suite catches is a leak that ships when someone skips the test suite.
    """
    seen: dict[str, int] = {}
    for index, split in enumerate(splits):
        for pid in split.get_column("patient_id").unique().to_list():
            if pid in seen:
                raise AssertionError(
                    f"patient {pid} appears in split {seen[pid]} and split {index} "
                    "— patient-level splitting is violated (AGENTS.md §2.1)"
                )
            seen[pid] = index

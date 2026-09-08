"""Tier 2 — invariants. These encode AGENTS.md §2 and outrank every other test.

If one of these is weak, no other result in the repository can be trusted.
"""

from __future__ import annotations

import polars as pl
import pytest

from sepsis.splits import (
    SITE_B_WARNING,
    apply_inclusion,
    assert_disjoint,
    inclusion_report,
    load_site_b,
    patient_hour_counts,
    split_patients,
    split_site_a,
)

SEED = 20190801


# --- §2.1 split by patient, never by row ------------------------------------


def test_no_patient_appears_in_more_than_one_split(cohort: pl.DataFrame):
    train, val = split_site_a(cohort, train_fraction=0.8, seed=SEED)

    train_ids = set(train.get_column("patient_id").to_list())
    val_ids = set(val.get_column("patient_id").to_list())
    assert train_ids & val_ids == set()


def test_every_hour_of_a_patient_lands_on_one_side(cohort: pl.DataFrame):
    """A row-level split would put a patient's hours on both sides. Catch it."""
    train, val = split_site_a(cohort, train_fraction=0.8, seed=SEED)
    site_a = cohort.filter(pl.col("site") == "A")

    per_patient = patient_hour_counts(site_a)
    for split in (train, val):
        counts = patient_hour_counts(split)
        merged = counts.join(per_patient, on=["site", "patient_id"], suffix="_full")
        assert (merged["n_hours"] == merged["n_hours_full"]).all()

    assert train.height + val.height == site_a.height


def test_assert_disjoint_raises_on_an_overlap(cohort: pl.DataFrame):
    site_a = cohort.filter(pl.col("site") == "A")
    with pytest.raises(AssertionError, match="appears in split"):
        assert_disjoint(site_a, site_a)


def test_assert_disjoint_passes_for_a_real_split(cohort: pl.DataFrame):
    train, val = split_site_a(cohort, train_fraction=0.8, seed=SEED)
    assert_disjoint(train, val)


# --- determinism (§23) ------------------------------------------------------


def test_split_is_deterministic_for_a_given_seed():
    ids = [f"p{i:06d}" for i in range(200)]
    first = split_patients(ids, 0.8, SEED)
    second = split_patients(ids, 0.8, SEED)
    assert first == second


def test_split_does_not_depend_on_input_order():
    """The split must depend on the ID set and the seed, nothing else."""
    ids = [f"p{i:06d}" for i in range(200)]
    forward = split_patients(ids, 0.8, SEED)
    backward = split_patients(list(reversed(ids)), 0.8, SEED)
    assert forward == backward


def test_a_different_seed_gives_a_different_split():
    ids = [f"p{i:06d}" for i in range(200)]
    assert split_patients(ids, 0.8, SEED) != split_patients(ids, 0.8, SEED + 1)


def test_split_respects_the_requested_fraction():
    ids = [f"p{i:06d}" for i in range(1000)]
    train, val = split_patients(ids, 0.8, SEED)
    assert len(train) == 800
    assert len(val) == 200
    assert set(train) | set(val) == set(ids)


def test_both_sides_are_non_empty_even_for_a_tiny_cohort():
    train, val = split_patients(["p1", "p2"], 0.8, SEED)
    assert train and val


# --- §2.2 site B is held out ------------------------------------------------


def test_loading_site_b_warns_on_stderr(cohort: pl.DataFrame, capsys):
    with pytest.warns(UserWarning, match="HELD-OUT SITE B"):
        load_site_b(cohort)

    captured = capsys.readouterr()
    assert SITE_B_WARNING in captured.err


def test_loading_site_b_warns_every_time_not_just_once(cohort: pl.DataFrame, capsys):
    """A warning that fires once is a warning nobody sees on the second call."""
    with pytest.warns(UserWarning):
        load_site_b(cohort)
    capsys.readouterr()

    with pytest.warns(UserWarning):
        load_site_b(cohort)
    assert SITE_B_WARNING in capsys.readouterr().err


def test_site_a_split_contains_no_site_b_patients(cohort: pl.DataFrame):
    train, val = split_site_a(cohort, train_fraction=0.8, seed=SEED)
    for split in (train, val):
        assert split.get_column("site").unique().to_list() == ["A"]


# --- D3 inclusion -----------------------------------------------------------


def test_inclusion_keeps_the_eight_hour_boundary_patient(cohort: pl.DataFrame):
    included = apply_inclusion(cohort, min_icu_hours=8)
    kept = set(included.get_column("patient_id").to_list())

    assert "p000003" in kept  # exactly 8 hours
    assert "p100003" in kept
    assert "p000004" not in kept  # 7 hours
    assert "p000005" not in kept  # 1 hour
    assert "p100004" not in kept  # 6 hours


def test_inclusion_drops_whole_patients_not_rows(cohort: pl.DataFrame):
    included = apply_inclusion(cohort, min_icu_hours=8)
    counts = patient_hour_counts(included)
    assert counts.get_column("n_hours").min() >= 8


def test_inclusion_report_accounts_for_every_patient(cohort: pl.DataFrame):
    report = inclusion_report(cohort, min_icu_hours=8)
    for row in report.iter_rows(named=True):
        assert row["patients_before"] == row["patients_after"] + row["patients_dropped"]
        assert row["hours_before"] == row["hours_after"] + row["hours_dropped"]

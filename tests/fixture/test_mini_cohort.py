"""Tier 3 — the whole Phase 1 pipeline over tests/fixtures/mini_cohort/.

One test per row of the §19 case table, named after the case.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from sepsis.config import CLINICAL_VARIABLES, PSV_COLUMNS
from sepsis.io import build_cohort, read_cohort, write_cohort
from sepsis.splits import apply_inclusion, patient_hour_counts

SITES = {"A": "training_setA", "B": "training_setB"}


def _hours(cohort: pl.DataFrame, patient_id: str) -> pl.DataFrame:
    return cohort.filter(pl.col("patient_id") == patient_id).sort("hour")


# --- shape ------------------------------------------------------------------


def test_cohort_has_one_row_per_patient_hour(cohort: pl.DataFrame):
    counts = patient_hour_counts(cohort)
    assert counts.height == 22  # patients in scripts/make_fixtures.py SPECS
    assert cohort.height == counts.get_column("n_hours").sum()


def test_cohort_carries_every_documented_column(cohort: pl.DataFrame):
    assert cohort.columns == ["patient_id", "site", "hour", *PSV_COLUMNS]


def test_hours_are_contiguous_and_zero_based(cohort: pl.DataFrame):
    for pid in cohort.get_column("patient_id").unique().to_list():
        hours = _hours(cohort, pid).get_column("hour").to_list()
        assert hours == list(range(len(hours)))


# --- §19 case table ---------------------------------------------------------


def test_patient_with_exactly_eight_hours_is_included(cohort: pl.DataFrame):
    assert _hours(cohort, "p000003").height == 8
    included = apply_inclusion(cohort, min_icu_hours=8)
    assert "p000003" in included.get_column("patient_id").to_list()


def test_patient_with_seven_hours_is_excluded(cohort: pl.DataFrame):
    assert _hours(cohort, "p000004").height == 7
    included = apply_inclusion(cohort, min_icu_hours=8)
    assert "p000004" not in included.get_column("patient_id").to_list()


def test_patient_with_one_hour_is_excluded(cohort: pl.DataFrame):
    assert _hours(cohort, "p000005").height == 1
    included = apply_inclusion(cohort, min_icu_hours=8)
    assert "p000005" not in included.get_column("patient_id").to_list()


def test_septic_patient_with_onset_at_hour_zero_is_labelled_from_the_first_row(
    cohort: pl.DataFrame,
):
    labels = _hours(cohort, "p000006").get_column("SepsisLabel").to_list()
    assert labels[0] == 1
    assert set(labels) == {1}


def test_non_septic_patient_is_all_zero_and_parses(cohort: pl.DataFrame):
    labels = _hours(cohort, "p000001").get_column("SepsisLabel").to_list()
    assert set(labels) == {0}


def test_variable_never_measured_is_all_null_not_zero(cohort: pl.DataFrame):
    """Never-measured must stay null so the missingness indicator can carry it."""
    rows = _hours(cohort, "p000007")
    assert rows.get_column("Lactate").null_count() == rows.height
    assert rows.get_column("HR").null_count() == 0


def test_variable_measured_only_in_the_final_hour_is_null_before_it(cohort: pl.DataFrame):
    """Guards against a backward fill sneaking a future value into earlier hours."""
    rows = _hours(cohort, "p000008")
    values = rows.get_column("Bilirubin_total").to_list()
    assert values[-1] is not None
    assert all(value is None for value in values[:-1])


def test_all_labs_null_with_vitals_present_completes(cohort: pl.DataFrame):
    rows = _hours(cohort, "p000009")
    assert rows.height == 20
    assert rows.get_column("Lactate").null_count() == rows.height
    assert rows.get_column("HR").null_count() == 0


def test_rows_out_of_order_in_the_file_are_sorted(cohort: pl.DataFrame):
    """Policy: sort by ICULOS. Decided in io.parse_psv, asserted here."""
    rows = _hours(cohort, "p000010")
    iculos = rows.get_column("ICULOS").to_list()
    assert iculos == sorted(iculos)
    assert iculos[0] == 1.0


def test_negative_hosp_adm_time_is_preserved(cohort: pl.DataFrame):
    """A negative value is real: the patient was on a ward before the ICU."""
    values = _hours(cohort, "p000011").get_column("HospAdmTime").unique().to_list()
    assert values == [-240.5]


def test_physiologically_impossible_values_survive_parsing_untouched(cohort: pl.DataFrame):
    """Phase 1 does not clean. D8 decides what happens to these, before Phase 3."""
    rows = _hours(cohort, "p000012")
    assert rows.get_column("HR").to_list()[5] == 300.0
    assert rows.get_column("Temp").to_list()[9] == 0.0
    assert rows.get_column("SBP").to_list()[12] == -5.0


def test_missing_unit_columns_stay_null(cohort: pl.DataFrame):
    rows = _hours(cohort, "p000014")
    assert rows.get_column("Unit1").null_count() == rows.height
    assert rows.get_column("Unit2").null_count() == rows.height


# --- label semantics (AGENTS.md §5, D1) -------------------------------------


def test_labels_are_monotone_once_positive(cohort: pl.DataFrame):
    """For a septic patient the label never returns to 0 after turning 1."""
    for pid in cohort.get_column("patient_id").unique().to_list():
        labels = _hours(cohort, pid).get_column("SepsisLabel").to_list()
        if 1 in labels:
            first = labels.index(1)
            assert set(labels[first:]) == {1}


def test_labels_are_binary(cohort: pl.DataFrame):
    assert set(cohort.get_column("SepsisLabel").unique().to_list()) <= {0, 1}


# --- cache round trip -------------------------------------------------------


def test_parquet_round_trip_preserves_the_frame(cohort: pl.DataFrame, tmp_path: Path):
    path = write_cohort(cohort, tmp_path / "cohort.parquet")
    assert read_cohort(path).equals(cohort)


def test_parallel_and_serial_parsing_agree(fixture_dir: Path, cohort: pl.DataFrame):
    """The worker pool must not change a single value or row order."""
    parallel = build_cohort(fixture_dir, SITES, max_workers=2)
    assert parallel.equals(cohort)


def test_every_clinical_variable_is_present_as_a_column(cohort: pl.DataFrame):
    for variable in CLINICAL_VARIABLES:
        assert variable in cohort.columns

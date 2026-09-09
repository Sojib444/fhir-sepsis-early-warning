"""Tier 2 — window-design files: staleness detection, concatenation, splits."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import polars as pl

from sepsis.features import window_feature_columns
from sepsis.io import build_cohort
from sepsis.splits import SITE_B_WARNING, assert_disjoint, split_site_a, split_site_b
from sepsis.window_design import (
    DesignFiles,
    build_window_design,
    concatenate_designs,
    design_is_current,
    load_design,
)

SITES = {"A": "training_setA", "B": "training_setB"}


def _fixture(fixture_dir: Path) -> pl.DataFrame:
    return build_cohort(fixture_dir, SITES, max_workers=1)


def _build(tmp_path, name, frame, columns=(6, 12, 24), cohort_sha="abc") -> DesignFiles:
    design = DesignFiles(
        name=name,
        npy=tmp_path / f"{name}.npy",
        side=tmp_path / f"{name}.npz",
        meta=tmp_path / f"{name}.meta.json",
    )
    build_window_design(
        frame,
        window_feature_columns(columns),
        columns,
        design.npy,
        design.side,
        design.meta,
        cohort_sha256=cohort_sha,
    )
    return design


def test_design_is_current_flags_stale_or_partial_designs(tmp_path, fixture_dir):
    frame = _fixture(fixture_dir)
    columns = window_feature_columns((6, 12, 24))
    design = _build(tmp_path, "a", frame, cohort_sha="abc")

    assert design_is_current(design, columns, "abc")
    assert not design_is_current(design, columns, "other-sha")
    assert not design_is_current(design, window_feature_columns((6,)), "abc")

    design.meta.unlink()
    assert not design_is_current(design, columns, "abc")


def test_design_rebuild_is_byte_identical(tmp_path, fixture_dir):
    frame = _fixture(fixture_dir)
    first = _build(tmp_path, "a", frame)
    second = _build(tmp_path, "a2", frame)
    x1, side1, _ = load_design(first)
    x2, side2, _ = load_design(second)
    np.testing.assert_array_equal(np.asarray(x1), np.asarray(x2))
    for key in side1.files:
        np.testing.assert_array_equal(side1[key], side2[key])


def test_concatenation_equals_a_direct_build(tmp_path, fixture_dir):
    """a_train + b_train concatenated must equal one direct build of the union."""
    frame = _fixture(fixture_dir)
    a_train, a_val = split_site_a(frame, 0.8, seed=20190801)
    b_train, b_eval = split_site_b(frame, 0.8, seed=20190801)
    assert_disjoint(a_train, a_val, b_train, b_eval)

    da = _build(tmp_path, "a_train", a_train)
    db = _build(tmp_path, "b_train", b_train)
    dab = DesignFiles(
        "ab", tmp_path / "ab.npy", tmp_path / "ab.npz", tmp_path / "ab.meta.json"
    )
    concatenate_designs([da, db], dab)

    direct = _build(tmp_path, "direct", pl.concat([a_train, b_train], how="vertical"))

    xa, sa, _ = load_design(dab)
    xb, sb, _ = load_design(direct)
    np.testing.assert_allclose(np.asarray(xa), np.asarray(xb), equal_nan=True)
    for key in sa.files:
        np.testing.assert_array_equal(sa[key], sb[key])


def test_split_site_b_warns_and_never_overlaps(capsys, fixture_dir):
    frame = _fixture(fixture_dir)
    b_train, b_eval = split_site_b(frame, 0.8, seed=20190801)
    err = capsys.readouterr().err
    assert SITE_B_WARNING in err

    train_ids = set(b_train.get_column("patient_id").unique().to_list())
    eval_ids = set(b_eval.get_column("patient_id").unique().to_list())
    assert not (train_ids & eval_ids)
    assert set(b_train.get_column("site").unique().to_list()) == {"B"}
    assert set(b_eval.get_column("site").unique().to_list()) == {"B"}


def test_split_site_b_is_deterministic(fixture_dir):
    frame = _fixture(fixture_dir)
    first = split_site_b(frame, 0.8, seed=20190801)
    second = split_site_b(frame, 0.8, seed=20190801)
    for a, b in zip(first, second, strict=True):
        assert a.get_column("patient_id").to_list() == b.get_column("patient_id").to_list()


def test_design_meta_records_the_model_contract(tmp_path, fixture_dir):
    frame = _fixture(fixture_dir)
    design = _build(tmp_path, "a", frame)
    meta = json.loads(design.meta.read_text(encoding="utf-8"))
    assert meta["columns"] == window_feature_columns((6, 12, 24))
    assert meta["cohort_sha256"] == "abc"
    assert meta["n_rows"] == frame.height
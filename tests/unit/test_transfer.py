"""Phase 4 transfer-curve internals (AGENTS.md §10.5)."""

from __future__ import annotations

from pathlib import Path

import lightgbm as lgb
import numpy as np
import polars as pl
import pytest

from sepsis.features import window_feature_columns
from sepsis.io import build_cohort
from sepsis.splits import split_site_a, split_site_b
from sepsis.transfer import (
    assert_adaptation_disjoint,
    draw_patient_mask,
    fine_tune,
    recalibrate_isotonic,
    transfer_curve,
)
from sepsis.window_design import DesignFiles, build_window_design, load_design

SITES = {"A": "training_setA", "B": "training_setB"}
WINDOWS = (6, 12, 24)


def _fixture(fixture_dir: Path) -> pl.DataFrame:
    return build_cohort(fixture_dir, SITES, max_workers=1)


def _build(tmp_path, name, frame, columns=(6, 12, 24)) -> DesignFiles:
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
    )
    return design


def _mini_model(x, side, columns, rounds=8) -> lgb.Booster:
    params = {
        "objective": "binary",
        "metric": "binary_logloss",
        "seed": 20190801,
        "deterministic": True,
        "force_row_wise": True,
        "verbosity": -1,
    }
    ds = lgb.Dataset(
        np.asarray(x),
        label=np.asarray(side["SepsisLabel"], dtype=np.int64),
        feature_name=columns,
        free_raw_data=True,
    )
    return lgb.train(params, ds, num_boost_round=rounds, callbacks=[lgb.log_evaluation(0)])


def test_draw_patient_mask_is_by_patient():
    pids = np.array([1, 1, 1, 2, 2, 3, 3, 3], dtype=object)
    mask = draw_patient_mask(pids, 2, np.random.default_rng(0))
    drawn = set(pids[mask])
    kept = set(pids[~mask])
    assert len(drawn) == 2  # two whole patients, not two rows
    assert drawn.isdisjoint(kept)


def test_draw_patient_mask_honours_n_and_rejects_impossible():
    pids = np.array([1, 2, 3, 4], dtype=object)
    mask = draw_patient_mask(pids, 1, np.random.default_rng(3))
    assert mask.sum() == 1
    with pytest.raises(ValueError):
        draw_patient_mask(pids, 9, np.random.default_rng(3))


def test_assert_adaptation_disjoint_detects_overlap():
    assert_adaptation_disjoint(np.array([1, 2]), np.array([3, 4]))  # fine
    with pytest.raises(AssertionError):
        assert_adaptation_disjoint(np.array([1, 2]), np.array([2, 3]))


def test_adaptation_draws_stay_disjoint_from_b_eval(tmp_path, fixture_dir):
    """The invariant §10.5 rests on: adapted-on patients never get scored."""
    frame = _fixture(fixture_dir)
    b_train, b_eval = split_site_b(frame, 0.8, seed=20190801)
    bt = _build(tmp_path, "b_train", b_train)
    be = _build(tmp_path, "b_eval", b_eval)
    train_pids = np.asarray(load_design(bt)[1]["patient_id"])
    eval_pids = np.asarray(load_design(be)[1]["patient_id"])
    assert np.intersect1d(np.unique(train_pids), np.unique(eval_pids)).size == 0
    for seed in range(5):
        mask = draw_patient_mask(train_pids, 2, np.random.default_rng(100 + seed))
        assert_adaptation_disjoint(np.unique(train_pids[mask]), np.unique(eval_pids))


def test_recalibrate_isotonic_outputs_are_probabilities():
    rng = np.random.default_rng(0)
    probs = rng.uniform(0.05, 0.95, size=2000)
    labels = (probs > rng.uniform(0, 1, size=2000)).astype(np.int64)
    calibrator = recalibrate_isotonic(probs, labels)
    out = calibrator.predict(np.array([0.1, 0.5, 0.9]))
    assert (out >= 0.0).all() and (out <= 1.0).all()


def test_fine_tune_continues_and_keeps_column_contract(tmp_path, fixture_dir):
    frame = _fixture(fixture_dir)
    a_train, _ = split_site_a(frame, 0.8, seed=20190801)
    design = _build(tmp_path, "a_train", a_train)
    x, side, columns = load_design(design)
    base = _mini_model(x, side, columns)
    assert base.feature_name() == columns

    mask = draw_patient_mask(side["patient_id"], 2, np.random.default_rng(0))
    params = {"objective": "binary", "seed": 20190801, "deterministic": True,
              "force_row_wise": True, "verbosity": -1}
    adapted = fine_tune(base, np.asarray(x[mask]), side["SepsisLabel"][mask], columns, params)
    assert adapted.feature_name() == columns
    assert adapted.predict(np.asarray(x[:2])).shape == (2,)


def test_transfer_curve_structure_and_site_b_isolation(tmp_path, fixture_dir):
    frame = _fixture(fixture_dir)
    a_train, _ = split_site_a(frame, 0.8, seed=20190801)
    bt, be = split_site_b(frame, 0.8, seed=20190801)
    da = _build(tmp_path, "a_train", a_train)
    dbt = _build(tmp_path, "b_train", bt)
    dbe = _build(tmp_path, "b_eval", be)
    x, side, columns = load_design(da)
    base = _mini_model(x, side, columns)
    params = {"objective": "binary", "seed": 20190801, "deterministic": True,
              "force_row_wise": True, "verbosity": -1}

    data = transfer_curve(
        base_model=base,
        base_params=params,
        b_train_design=dbt,
        b_eval_design=dbe,
        columns=columns,
        threshold=0.5,
        n_values=(0, 1, 2),
        draws=2,
        seed=20190801,
    )
    assert data["n"] == [0, 1, 2]
    assert len(data["recalib"]) == len(data["finetune"]) == 3
    for per_n in data["recalib"] + data["finetune"]:
        assert len(per_n) == 2  # one row per draw
        for row in per_n:
            assert {"auprc", "utility"} <= set(row)
    # n=0 is exactly the base model for both arms. b_eval here is a single
    # non-septic patient, so AUPRC and utility are NaN — but both arms must
    # still report the identical (un-adapted) result.
    def _norm(rows):
        return [{k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in r.items()}
                for r in rows]

    assert _norm(data["finetune"][0]) == _norm(data["recalib"][0])
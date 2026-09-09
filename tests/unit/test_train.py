"""Tier 2 — preprocessing statistics are fit on train only (AGENTS.md §2.4)."""

from __future__ import annotations

import numpy as np
import polars as pl

from sepsis.splits import split_site_a
from sepsis.train import build_baseline_pipeline, fit_baseline


def test_baseline_scaler_is_fit_on_train_only():
    """The scaler fit on train must differ from one fit on validation."""
    X_train = np.array([[1.0, 10.0], [2.0, 20.0], [3.0, 30.0], [np.nan, 40.0]])
    X_val = np.array([[50.0, 5.0], [60.0, 6.0]])

    pipe = build_baseline_pipeline()
    Z_train = pipe.fit_transform(X_train)
    Z_val = pipe.transform(X_val)

    # Imputation + scaling statistics are train statistics.
    assert np.allclose(Z_train.mean(axis=0), 0.0, atol=1e-9)
    assert not np.allclose(Z_val.mean(axis=0), 0.0, atol=1e-6)
    assert not np.allclose(Z_train.std(axis=0), 0.0)

    # A scaler fitted on validation would transform validation differently.
    val_only = build_baseline_pipeline().fit_transform(X_val)
    assert not np.allclose(Z_val, val_only)


def test_baseline_metrics_are_deterministic(cohort: pl.DataFrame):
    """Same seed, same splits, same metrics — twice in a row."""
    import json
    import math

    train, validation = split_site_a(cohort, 0.8, seed=20190801)

    first = fit_baseline(train, validation, seed=20190801)
    second = fit_baseline(train, validation, seed=20190801)

    assert first["model"].get_params() == second["model"].get_params()

    # Floats compare via their JSON form so that NaN on both sides counts as
    # equal (a validation split with no positives is NaN, deterministically).
    def serialized(metrics):
        return json.dumps(metrics["metrics"], sort_keys=True, indent=2)

    assert serialized(first) == serialized(second)
    assert math.isnan(first["metrics"]["auprc"]) or first["metrics"]["auprc"] >= 0

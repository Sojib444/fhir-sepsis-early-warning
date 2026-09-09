"""Training entry point (AGENTS.md §8–§9).

Subcommands:

  baseline — Phase 2. Logistic regression on the minimal feature set, scaler
             fit on the site-A train split only.

  window   — Phase 3. LightGBM on the window-feature set, hyperparameters
             searched over a fixed grid on the site-A validation split only.

Every number written lands in results/ with a provenance block, from the same
evaluation function used everywhere else.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import polars as pl
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from sepsis import evaluate
from sepsis.config import load_config
from sepsis.features import design_matrix, feature_columns, minimal_features
from sepsis.io import read_cohort
from sepsis.provenance import provenance
from sepsis.splits import apply_inclusion, assert_disjoint, split_site_a


def load_split_frames(config):
    """The cohort, D3-included, split by patient into (train, validation).

    The split itself is asserted disjoint inside `split_site_a`'s caller here,
    so a regression in the pipeline is caught by the pipeline, not only by the
    test suite.
    """
    cohort = read_cohort(config.path("cohort"))
    cohort = apply_inclusion(cohort, config.min_icu_hours)
    train, validation = split_site_a(cohort, config.train_fraction, config.seed)
    assert_disjoint(train, validation)
    return train, validation


def _design_arrays(design: pl.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Split a design matrix into features, labels, patient ids and hours."""
    features = design.select(feature_columns(design)).to_numpy()
    labels = design.get_column("SepsisLabel").to_numpy().astype(np.int64)
    patient_ids = design.get_column("patient_id").to_numpy()
    hours = design.get_column("hour").to_numpy().astype(np.int64)
    return features, labels, patient_ids, hours


def build_baseline_pipeline():
    """Mean imputation + standardization, both fit on train only (§2.4).

    Kept as a function (not inlined in `baseline`) so the "scaler fit on train
    only" test can exercise exactly the object the pipeline uses.
    """
    from sklearn.impute import SimpleImputer

    return make_pipeline(SimpleImputer(strategy="mean"), StandardScaler())


def _write_result(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")


def _drop_unobservable_features(design: pl.DataFrame) -> pl.DataFrame:
    """Drop features with no observed value in this frame.

    `SimpleImputer` silently drops such columns at fit time, which would leave
    the recorded `feature_columns` disagreeing with the fitted model. Doing the
    drop by name up front keeps the artifact matchable column-for-column.
    """
    feature_names = feature_columns(design)
    if not feature_names:
        raise ValueError("design matrix has no feature columns")
    null_counts = design.select(feature_names).null_count().row(0)
    used = [name for name, missing in zip(feature_names, null_counts) if missing < design.height]
    side = [c for c in design.columns if c not in feature_names]  # side columns
    return design.select([pl.col(c) for c in side] + [pl.col(c) for c in used])


def fit_baseline(train: pl.DataFrame, validation: pl.DataFrame, seed: int) -> dict:
    """Fit the diagnostic-regression baseline and evaluate it on validation.

    Both splits are polars frames from `split_site_a`. Everything the Phase 2
    acceptance criteria care about happens here: scaler fit on train only,
    evaluations from the single harness, forward-only features.
    """
    train_design = design_matrix(train, minimal_features)
    val_design = design_matrix(validation, minimal_features)

    # A feature that is never observed in train (e.g. EtCO2 at site A) carries
    # nothing the baseline can use; SimpleImputer would drop it anyway. Dropping
    # by name keeps the recorded feature list equal to the fitted columns.
    train_design = _drop_unobservable_features(train_design)
    val_design = val_design.select(train_design.columns)

    X_train, y_train, _, _ = _design_arrays(train_design)

    pipeline = build_baseline_pipeline()
    X_train_scaled = pipeline.fit_transform(X_train)

    model = LogisticRegression(max_iter=2000, random_state=seed)
    model.fit(X_train_scaled, y_train)

    X_val, y_val, pids_val, _ = _design_arrays(val_design)
    X_val_scaled = pipeline.transform(X_val)
    probs_val = model.predict_proba(X_val_scaled)[:, 1]

    metrics = evaluate.run_evaluation(y_val, probs_val, pids_val, threshold=0.5)
    features = feature_columns(val_design)

    return {
        "model": model,
        "pipeline": pipeline,
        "metrics": metrics,
        "features": features,
        "n_train_patients": int(train.get_column("patient_id").n_unique()),
        "n_val_patients": int(validation.get_column("patient_id").n_unique()),
    }


def baseline(config) -> dict:
    """Phase 2: deliberately weak logistic-regression baseline."""
    train, validation = load_split_frames(config)
    fitted = fit_baseline(train, validation, config.seed)

    result = {
        "model": "logistic_regression",
        "phase": "baseline",
        "features": "minimal",
        "feature_columns": fitted["features"],
        "n_train_patients": fitted["n_train_patients"],
        "n_val_patients": fitted["n_val_patients"],
        "hyperparameters": {"max_iter": 2000, "solver": "lbfgs"},
        "metrics": fitted["metrics"],
        "provenance": provenance(config.seed, config.path("cohort")),
    }

    model_dir = config.path("models")
    model_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "pipeline": fitted["pipeline"],
            "model": fitted["model"],
            "feature_columns": fitted["features"],
        },
        model_dir / "baseline_lr.joblib",
    )
    (model_dir / "baseline_features_columns.json").write_text(
        json.dumps(fitted["features"], indent=2) + "\n", encoding="utf-8"
    )

    _write_result(config.path("metrics"), result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("baseline", help=baseline.__doc__)
    sub.add_parser("window", help="Phase 3 LightGBM (implemented in Phase 3)")
    args = parser.parse_args()

    config = load_config(args.config)

    if args.command == "baseline":
        result = baseline(config)
        print(json.dumps(result["metrics"], indent=2, sort_keys=True))
    else:
        raise SystemExit("window subcommand arrives in Phase 3")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

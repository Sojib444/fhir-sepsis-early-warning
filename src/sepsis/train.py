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
import gc
import json
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import polars as pl
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from sepsis import evaluate
from sepsis.config import load_config
from sepsis.features import design_matrix, feature_columns, minimal_features
from sepsis.io import read_cohort
from sepsis.provenance import provenance, sha256_of_file
from sepsis.splits import apply_inclusion, assert_disjoint, split_site_a, split_site_b
from sepsis.window_design import (
    DesignFiles,
    build_window_design,
    concatenate_designs,
    design_files,
    design_is_current,
    load_design,
    window_columns_from_config,
)


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


def _merge_metrics(config, key: str, payload: dict) -> None:
    """Add one model's metrics to results/metrics.json without clobbering others."""
    path = config.path("metrics")
    current: dict = {}
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            current = {}
    current[key] = payload
    _write_result(path, current)


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
    used = [
        name
        for name, missing in zip(feature_names, null_counts, strict=True)
        if missing < design.height
    ]
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

    _merge_metrics(config, "baseline", result)
    return result


# --- Phase 3: window-feature LightGBM ----------------------------------------


def _lgb_base_params(config) -> dict:
    """Deterministic LightGBM scaffold shared by every Phase 3 model (§23)."""
    return {
        "objective": "binary",
        "metric": "binary_logloss",
        "seed": config.seed,
        "deterministic": True,
        "force_row_wise": True,
        "num_threads": int(config.raw["model"]["lightgbm"]["num_threads"]),
        "verbosity": -1,
    }


def make_designs(config) -> dict[str, DesignFiles]:
    """Split the cohort and build or reuse the five window designs (§7, §9).

    Designs are gitignored derived data under data/interim/designs, keyed by
    split. Rebuilding is skipped when the on-disk file is complete, matches the
    frozen column order, and matches the current cohort sha (so a stale design
    after `make data` rebuilds itself).
    """
    cohort = read_cohort(config.path("cohort"))
    cohort = apply_inclusion(cohort, config.min_icu_hours)
    a_train, a_val = split_site_a(cohort, config.train_fraction, config.seed)
    b_train, b_eval = split_site_b(cohort, config.site_b_train_fraction, config.seed)
    assert_disjoint(a_train, a_val, b_train, b_eval)

    columns = window_columns_from_config(config)
    windows = tuple(config.windows)
    cohort_sha = sha256_of_file(config.path("cohort"))

    designs: dict[str, DesignFiles] = {}
    for name, frame in {
        "a_train": a_train,
        "a_val": a_val,
        "b_train": b_train,
        "b_eval": b_eval,
    }.items():
        design = design_files(config, name)
        if not design_is_current(design, columns, cohort_sha):
            build_window_design(
                frame, columns, windows, design.npy, design.side, design.meta, cohort_sha
            )
        designs[name] = design

    # A+B design is the row-wise concatenation of the A-train and B-train
    # designs. Both already hold per-patient blocks, and a patient never
    # straddles a design, so this equals a direct build exactly.
    ab = design_files(config, "ab_train")
    if not design_is_current(ab, columns, cohort_sha):
        concatenate_designs([designs["a_train"], designs["b_train"]], ab)
    designs["ab_train"] = ab
    return designs


def _predict_probs(model, design: DesignFiles) -> dict[str, np.ndarray]:
    """Probabilities, labels and patient ids for one design under one model."""
    x, side, _ = load_design(design)
    return {
        "probs": np.asarray(model.predict(x), dtype=np.float64),
        "labels": side["SepsisLabel"].astype(np.int64),
        "pids": side["patient_id"],
    }


def _utility_max_threshold(
    config, labels: np.ndarray, probs: np.ndarray, pids: np.ndarray
) -> tuple[float, float]:
    """D5: the operating threshold is utility-max on site-A validation."""
    step = config.threshold_sweep_step
    best_t: float | None = None
    best_u: float | None = None
    t = step
    while t < 1.0 - 1e-9:
        metrics = evaluate.run_evaluation(labels, probs, pids, threshold=float(t))
        u = metrics["utility"]
        if u == u and (best_u is None or u > best_u):
            best_u, best_t = u, t
        t += step
    if best_t is None:
        raise ValueError("threshold sweep produced no finite utility")
    return float(best_t), float(best_u)


def search_window(config, designs: dict[str, DesignFiles]) -> tuple[dict, list[dict], object]:
    """The fixed 18-point grid from config.yaml, scored on site-A validation.

    Every number here comes through `run_evaluation`, the one evaluation path.
    The Dataset is constructed once and reused across the grid: the tree
    parameters that vary do not change the histogram construction.
    """
    columns = window_columns_from_config(config)
    base = _lgb_base_params(config)
    n_rounds = int(config.raw["model"]["lightgbm"]["n_estimators"])
    grid = config.raw["model"]["lightgbm"]["grid"]

    x_train, side_train, _ = load_design(designs["a_train"])
    train_set = lgb.Dataset(
        x_train,
        label=side_train["SepsisLabel"].astype(np.int64),
        feature_name=columns,
        free_raw_data=True,
    )

    rows: list[dict] = []
    best_row: dict | None = None
    best_model = None

    for learning_rate in grid["learning_rate"]:
        for num_leaves in grid["num_leaves"]:
            for min_child_samples in grid["min_child_samples"]:
                if best_model is not None:
                    del best_model
                    gc.collect()
                params = dict(
                    base,
                    learning_rate=learning_rate,
                    num_leaves=num_leaves,
                    min_child_samples=min_child_samples,
                )
                model = lgb.train(
                    params, train_set, num_boost_round=n_rounds, callbacks=[lgb.log_evaluation(0)]
                )
                scored = _predict_probs(model, designs["a_val"])
                metrics = evaluate.run_evaluation(
                    scored["labels"], scored["probs"], scored["pids"], threshold=0.5
                )
                row = {
                    "learning_rate": learning_rate,
                    "num_leaves": num_leaves,
                    "min_child_samples": min_child_samples,
                    "auroc": metrics["auroc"],
                    "auprc": metrics["auprc"],
                    "utility": metrics["utility"],
                    "prevalence": metrics["prevalence"],
                }
                rows.append(row)
                if best_row is None or row["auprc"] > best_row["auprc"]:
                    best_row = row
                    best_model = model
                del model

    assert best_row is not None and best_model is not None
    del train_set
    gc.collect()
    return best_row, rows, best_model


def _train_on(config, design: DesignFiles, params: dict, n_rounds: int):
    """Train one Phase 3 model on a design with the given tree parameters."""
    x, side, columns = load_design(design)
    train_set = lgb.Dataset(
        x,
        label=side["SepsisLabel"].astype(np.int64),
        feature_name=columns,
        free_raw_data=True,
    )
    model = lgb.train(
        params, train_set, num_boost_round=n_rounds, callbacks=[lgb.log_evaluation(0)]
    )
    del train_set
    gc.collect()
    return model


def _threshold_doc(config, values: dict[str, tuple[float, float]]) -> dict:
    doc = {"rule": "D5: utility-max on site-A validation, swept at 0.01"}
    for name, (threshold, utility) in sorted(values.items()):
        doc[name] = {"threshold": threshold, "utility_at_threshold": utility}
    doc["provenance"] = provenance(config.seed, config.path("cohort"))
    return doc


def window(config) -> dict:
    """Phase 3: the headline LightGBM model, its grid search, and the training
    side of the cross-site matrix (cells touching site-A validation).

    Site B is used here in two controlled ways only:
      * b_train — the §9 rows, for the B->A and A+B->B matrix cells;
      * to build the b_eval design — which is *scored* exactly once, later,
        in scripts/eval_matrix.py, after every design choice is frozen.
    """
    designs = make_designs(config)
    columns = window_columns_from_config(config)
    n_rounds = int(config.raw["model"]["lightgbm"]["n_estimators"])

    best_row, search_rows, a_model = search_window(config, designs)
    winner_params = {
        key: best_row[key] for key in ("learning_rate", "num_leaves", "min_child_samples")
    }

    a_val = _predict_probs(a_model, designs["a_val"])
    threshold_a, util_a = _utility_max_threshold(
        config, a_val["labels"], a_val["probs"], a_val["pids"]
    )
    metrics_a = evaluate.run_evaluation(
        a_val["labels"], a_val["probs"], a_val["pids"], threshold=threshold_a
    )

    b_model = _train_on(
        config, designs["b_train"], dict(_lgb_base_params(config), **winner_params), n_rounds
    )
    b_val = _predict_probs(b_model, designs["a_val"])  # B->A scored on site A validation
    threshold_b, util_b = _utility_max_threshold(
        config, b_val["labels"], b_val["probs"], b_val["pids"]
    )
    metrics_b = evaluate.run_evaluation(
        b_val["labels"], b_val["probs"], b_val["pids"], threshold=threshold_b
    )

    ab_model = _train_on(
        config, designs["ab_train"], dict(_lgb_base_params(config), **winner_params), n_rounds
    )
    ab_val = _predict_probs(ab_model, designs["a_val"])  # A+B -> A scored on site A validation
    threshold_ab, util_ab = _utility_max_threshold(
        config, ab_val["labels"], ab_val["probs"], ab_val["pids"]
    )
    metrics_ab = evaluate.run_evaluation(
        ab_val["labels"], ab_val["probs"], ab_val["pids"], threshold=threshold_ab
    )

    model_dir = config.path("models")
    model_dir.mkdir(parents=True, exist_ok=True)
    a_model.save_model(model_dir / "window_a.txt")
    b_model.save_model(model_dir / "window_b.txt")
    ab_model.save_model(model_dir / "window_ab.txt")
    (model_dir / "window_features_columns.json").write_text(
        json.dumps(columns, indent=2) + "\n", encoding="utf-8"
    )

    _write_result(
        config.path("threshold"),
        _threshold_doc(
            config,
            {
                "A": (threshold_a, util_a),
                "B": (threshold_b, util_b),
                "A+B": (threshold_ab, util_ab),
            },
        ),
    )
    _grid_cfg = config.raw["model"]["lightgbm"]["grid"]
    _grid = {
        "learning_rate": list(_grid_cfg["learning_rate"]),
        "num_leaves": list(_grid_cfg["num_leaves"]),
        "min_child_samples": list(_grid_cfg["min_child_samples"]),
    }
    _write_result(
        config.path("search"),
        {
            "grid": _grid,
            "n_estimators": n_rounds,
            "winner": best_row,
            "rows": search_rows,
            "selection": "best AUPRC on site-A validation",
            "provenance": provenance(config.seed, config.path("cohort")),
        },
    )

    payload = {
        "model": "lightgbm",
        "phase": "window",
        "features": "window_6_12_24",
        "feature_columns": columns,
        "hyperparameters": {
            "winner": winner_params,
            "n_estimators": n_rounds,
            "grid_dimensions": _grid,
        },
        "cells": {
            "A->A": metrics_a,
            "B->A": metrics_b,
            "A+B->A": metrics_ab,
        },
        "note": (
            "Cells touching site B evaluation (A->B, A+B->B) are computed once "
            "in scripts/eval_matrix.py."
        ),
        "provenance": provenance(config.seed, config.path("cohort")),
    }
    _merge_metrics(config, "window", payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("baseline", help=baseline.__doc__)
    sub.add_parser("window", help=window.__doc__)
    args = parser.parse_args()

    config = load_config(args.config)

    if args.command == "baseline":
        result = baseline(config)
        print(json.dumps(result["metrics"], indent=2, sort_keys=True))
    elif args.command == "window":
        result = window(config)
        print(json.dumps(result["cells"], indent=2, sort_keys=True))
    else:  # pragma: no cover - argparse enforces the choices
        raise SystemExit("unknown command")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

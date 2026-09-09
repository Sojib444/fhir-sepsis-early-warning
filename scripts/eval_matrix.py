"""Phase 3 — the 4-cell cross-site matrix and its diagnostics (AGENTS.md §9).

This is the ONLY place where cells touching the site-B evaluation set are
scored, and it is intended to run exactly once per frozen design. It was
written after the grid search and the utility-max threshold were frozen in
`sepsis.train` (D5, §9.3). It reads the saved LightGBM models and the on-disk
designs; it never retrains and never re-tunes.

Writes:
  results/metrics.json          merged with the window (site-A) cells
  results/cross_site_matrix.md  the 4-cell matrix + drift + SHAP evidence
  results/feature_drift.json    per-feature site distance, ranked
  results/shap_importance.json  global importances per site

The markdown report presents evidence only; the interpretation of the
cross-site drop is the human's job (D6, §3).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import shap
from scipy import stats

from sepsis import evaluate
from sepsis.config import load_config
from sepsis.provenance import (
    git_commit,
    git_tree_is_dirty,
    provenance,
    provenance_markdown,
    sha256_of_file,
)
from sepsis.window_design import design_files, load_design

# Column order of the matrix table (AGENTS.md §9.3).
CELLS = ["A->A", "A->B", "B->A", "A+B->A", "A+B->B"]
# Training artifact name -> the small label used in the threshold file.
TRAIN_LABEL = {"window_a.txt": "A", "window_b.txt": "B", "window_ab.txt": "A+B"}


def _threshold_for(config, train_name: str) -> float:
    """The frozen D5 threshold chosen on site-A validation for a training set."""
    doc = json.loads(config.path("threshold").read_text(encoding="utf-8"))
    return float(doc[TRAIN_LABEL[train_name]]["threshold"])


def _score(model, design, threshold: float) -> dict:
    x, side, _ = load_design(design)
    probs = np.asarray(model.predict(x), dtype=np.float64)
    return evaluate.run_evaluation(
        side["SepsisLabel"].astype(np.int64), probs, side["patient_id"], threshold=threshold
    )


def _matrix(config, designs) -> dict[str, dict]:
    """Score all five cells; site-B cells exactly once."""
    matrix: dict[str, dict] = {}
    for train_name in ("window_a.txt", "window_b.txt", "window_ab.txt"):
        model = lgb.Booster(model_file=str(config.path("models") / train_name))
        threshold = _threshold_for(config, train_name)
        if train_name == "window_a.txt":
            matrix["A->A"] = _score(model, designs["a_val"], threshold)
            matrix["A->B"] = _score(model, designs["b_eval"], threshold)
        elif train_name == "window_b.txt":
            matrix["B->A"] = _score(model, designs["a_val"], threshold)
        else:  # window_ab.txt
            # A+B model scored on site-A validation AND on site-B evaluation.
            matrix["A+B->A"] = _score(model, designs["a_val"], threshold)
            matrix["A+B->B"] = _score(model, designs["b_eval"], threshold)
    return matrix


def _feature_wasserstein(x_a, x_b):
    """Per-feature Wasserstein distance between two sample columns.

    Takes already-sampled 2D arrays (rows x features) and the feature names.
    Missingness is encoded as a distinct value by the model, but for the drift
    diagnostic we compare observed values only; a feature that is rarely
    measured naturally reports a small distance and its missingness feature
    (also in the matrix) reports the ordering-culture gap.
    """
    distances: dict[str, float] = {}
    for j in range(x_a.shape[1]):
        a = x_a[:, j]
        b = x_b[:, j]
        a_obs = a[~np.isnan(a)]
        b_obs = b[~np.isnan(b)]
        if len(a_obs) < 2 or len(b_obs) < 2:
            distances[j] = float("nan")
        else:
            distances[j] = float(stats.wasserstein_distance(a_obs, b_obs))
    return distances


def _shap_importance(model, design, columns: list[str], n: int = 4000) -> dict[str, float]:
    """Global mean |SHAP| over a deterministic subsample of a design."""
    x, _, _ = load_design(design)
    rng = np.random.default_rng(20190801)
    idx = rng.integers(0, x.shape[0], size=min(n, x.shape[0]))
    sample = np.asarray(x[idx])
    explainer = shap.TreeExplainer(model)
    values = explainer.shap_values(sample)
    mean_abs = np.abs(values).mean(axis=0)
    return {col: float(mean_abs[j]) for j, col in enumerate(columns)}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")


def _score_fingerprint(config, designs) -> dict:
    """Hash of everything that determines the site-B cell values."""
    names = ("window_a.txt", "window_b.txt", "window_ab.txt")
    model_hashes = {
        name: sha256_of_file(config.path("models") / name)[:16] for name in names
    }
    b_eval = designs["b_eval"]
    design_meta = b_eval.npy, b_eval.side, b_eval.meta
    design_hashes = {
        part.suffix or part.name: sha256_of_file(part)[:16] for part in design_meta
    }
    threshold_hash = sha256_of_file(config.path("threshold"))[:16]
    columns_hash = sha256_of_file(config.path("models") / "window_features_columns.json")[:16]
    return {
        "git_commit": git_commit(),
        "git_tree_dirty": bool(git_tree_is_dirty()),
        "models": model_hashes,
        "b_eval_design": design_hashes,
        "threshold": threshold_hash,
        "feature_columns": columns_hash,
    }


def _site_b_scoring_decision(config, fingerprints: dict, force: bool) -> str:
    """Return 'score' or 'reuse'; require --force to override a mismatch.

    Site B is scored exactly once per frozen design (AGENTS.md §2.2, §9.3).
    If nothing that determines the site-B numbers has changed, re-running the
    matrix script reuses the stored score instead of recomputing it. If the
    artifact fingerprint differs (e.g. an accidental retrain), the script
    refuses to silently produce a *new* site-B number and requires `--force`,
    which re-scores the cells and leaves an explicit `site_b_rescored` flag.
    """
    doc: dict = {}
    try:
        doc = json.loads(config.path("metrics").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        doc = {}
    cells = doc.get("window", {}).get("cells", {})
    if not cells.get("site_B_scored_once"):
        return "score"  # first scoring of this run's artifacts

    if cells.get("site_B_fingerprint") == fingerprints:
        return "score" if force else "reuse"

    if force:
        print(
            "--force: re-scoring site-B cells despite changed artifacts "
            "(site_b_rescored will be recorded).",
            file=__import__("sys").stderr,
        )
        return "score"

    keys = {
        "models": "model files",
        "b_eval_design": "site-B evaluation design",
        "threshold": "threshold file",
        "feature_columns": "feature columns file",
    }
    changed = [
        label
        for key, label in keys.items()
        if cells.get("site_B_fingerprint", {}).get(key) != fingerprints.get(key)
    ]
    raise SystemExit(
        "REFUSING to re-score site B: %s changed since the stored site-B score. "
        "The old site-B numbers are no longer meaningful; re-score explicitly "
        "with --force so the replacement is recorded."
        % (" or ".join(changed) if changed else "(unknown fingerprint difference)")
    )


def _matrix_from_stored(config) -> dict[str, dict]:
    """Rebuild the matrix from metrics.json (used when reusing a prior scoring)."""
    cells = json.loads(config.path("metrics").read_text(encoding="utf-8"))["window"]["cells"]
    return {cell: dict(cells[cell]) for cell in CELLS}


def _matrix_rows(matrix: dict[str, dict]) -> list[str]:
    rows = [
        "| Train | Test | AUROC | AUPRC | Utility | Prevalence |",
        "|---|---|---|---|---|---|",
    ]
    for cell in CELLS:
        m = matrix[cell]
        rows.append(
            f"| {cell.split('->')[0]} | {cell.split('->')[1]} | {m['auroc']:.4f} | "
            f"{m['auprc']:.4f} | {m['utility']:.4f} | {m['prevalence']:.4f} |"
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-score site-B cells even though they were already scored for this "
        "frozen design (records site_b_rescored in metrics.json)",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    designs = {
        name: design_files(config, name)
        for name in ("a_train", "a_val", "b_train", "b_eval", "ab_train")
    }
    columns = json.loads(
        (config.path("models") / "window_features_columns.json").read_text(encoding="utf-8")
    )
    fingerprint = _score_fingerprint(config, designs)

    # The site-B cells are scored only when (a) they have never been scored, or
    # (b) --force says replace them. Otherwise stored numbers are reused.
    decision = _site_b_scoring_decision(config, fingerprint, force=args.force)
    reuse = decision == "reuse"
    if reuse:
        print(
            "Site-B cells already scored for this frozen design; reusing stored "
            "metrics instead of re-scoring.",
            file=__import__("sys").stderr,
        )
    matrix = _matrix_from_stored(config) if reuse else _matrix(config, designs)

    # --- diagnostics ---------------------------------------------------------
    rng = np.random.default_rng(20190801)
    n_drift = 50_000
    x_a, _, _ = load_design(designs["a_train"])
    x_b, _, _ = load_design(designs["b_train"])
    a_samp = np.asarray(x_a[rng.integers(0, x_a.shape[0], size=n_drift)])
    b_samp = np.asarray(x_b[rng.integers(0, x_b.shape[0], size=n_drift)])
    dists = _feature_wasserstein(a_samp, b_samp)
    ranked = sorted(
        ((columns[j], dists[j]) for j in range(len(columns))),
        key=lambda kv: (-1 if np.isnan(kv[1]) else kv[1]),
        reverse=True,
    )

    a_model = lgb.Booster(model_file=str(config.path("models") / "window_a.txt"))
    b_model = lgb.Booster(model_file=str(config.path("models") / "window_b.txt"))
    a_imp = _shap_importance(a_model, designs["a_val"])
    b_imp = _shap_importance(b_model, designs["b_eval"])

    _write_json(
        config.path("feature_drift"),
        {"per_feature": {col: d for col, d in ranked}, "ranked": ranked,
         "provenance": provenance(config.seed, config.path("cohort"))},
    )
    _write_json(
        config.path("shap_importance"),
        {"site_A_model": a_imp, "site_B_model": b_imp,
         "provenance": provenance(config.seed, config.path("cohort"))},
    )

    # --- markdown report -----------------------------------------------------
    lines = [
        "# Cross-site matrix (AGENTS.md §9)",
        "",
        "All cells are scored with the official, vendored utility scorer. AUROC/AUPRC "
        "use raw probabilities; accuracy/utility/F-measure use the frozen D5 threshold "
        "belonging to the training set, chosen on site-A validation only.",
        "",
        "**Site-B cells (A→B, A+B→B) were scored exactly once, after every design choice "
        "was frozen (grid, threshold, features, inclusion rule).** Nothing in this file "
        "was retrained or re-tuned against site B.",
        "",
    ]
    lines += _matrix_rows(matrix)
    lines += [
        "",
        "## Evidence for the cross-site drop (D6)",
        "",
        "This section reports per-feature distribution distance and per-site SHAP "
        "importance. It presents evidence only; the interpretation is the human's.",
        "",
        "### Per-feature distribution distance (A-train vs B-train, Wasserstein)",
        "",
        "Ranked most-changed first. NaN means too few observed values at one site for "
        "a stable distance:",
        "",
    ]
    for col, d in ranked:
        lines.append(f"- `{col}` — `{d:.5g}`")
    lines += ["", "### SHAP global importance, per site", ""]
    lines.append("**Site A model (trained on A), top 15 by mean |SHAP|:**")
    lines.append("")
    for col, v in sorted(a_imp.items(), key=lambda kv: kv[1], reverse=True)[:15]:
        lines.append(f"- `{col}` — `{v:.5g}`")
    lines += ["", "**Site B model (trained on B, scored on B-eval), top 15:**", ""]
    for col, v in sorted(b_imp.items(), key=lambda kv: kv[1], reverse=True)[:15]:
        lines.append(f"- `{col}` — `{v:.5g}`")
    lines += ["", provenance_markdown(provenance(config.seed, config.path("cohort")))]

    matrix_md = config.result_path("cross_site_matrix.md")
    matrix_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # --- merge site-B cells into metrics.json --------------------------------
    metrics_path = config.path("metrics")
    current = json.loads(metrics_path.read_text(encoding="utf-8"))
    were_scored = not reuse
    for key in ("A->B", "A+B->B"):
        current["window"]["cells"][key] = matrix[key]
    if were_scored:
        current["window"]["cells"]["site_B_scored_once"] = True
        current["window"]["cells"]["site_B_fingerprint"] = fingerprint
        if args.force:
            current["window"]["cells"]["site_b_rescored"] = True
    _write_json(metrics_path, current)

    print(json.dumps(matrix, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

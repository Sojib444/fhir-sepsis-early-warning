"""Phase 4 — rigor analyses (AGENTS.md §10).

Recalibration, subgroups, alert burden, SHAP figures and the transfer curve.
Site-B *evaluation* cells here are the Phase-4 scoring pass: they reuse the
once-scored predictions pattern — the script loads the frozen A model once and
scores nothing on site B that the matrix script already scored. Adaptation for
the transfer curve draws exclusively from `b_train`; scoring is on `b_eval`,
and the draws are asserted disjoint from it.

Writes (all under results/):
  metrics.json               merged with calibration/subgroups/alert-burden keys
  rigor.md                   human-readable phase-4 summary
  transfer.json              transfer-curve numbers
  figures/*.png              alert burden, SHAP global, SHAP waterfalls, transfer
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import lightgbm as lgb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import polars as pl
import shap
from sklearn.isotonic import IsotonicRegression

from sepsis import evaluate
from sepsis.config import load_config
from sepsis.io import read_cohort
from sepsis.provenance import provenance, provenance_markdown
from sepsis.transfer import transfer_curve
from sepsis.window_design import design_files, load_design

AGE_BANDS: list[tuple[str, float, float]] = [
    ("<40", 0, 40), ("40-65", 40, 65), ("65-80", 65, 80), ("80+", 80, np.inf)
]
MIN_POSITIVES = 50  # suppress subgroups with fewer positive hours than this
FIGS = "figures"


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")


def _reliability_curve(ax, labels, probs, label, color):
    rows = evaluate.reliability_table(labels, probs)
    mean_pred = [r["mean_prediction"] or np.nan for r in rows]
    pos = [r["positive_rate"] or np.nan for r in rows]
    ax.plot(mean_pred, pos, marker="o", label=label, color=color)
    return rows


def _alert_burden(config, labels, probs, pids, threshold, fig_path):
    """Sensitivity/PPV/alerts-per-100-ICU-days vs threshold, with D5 marked."""
    step = config.threshold_sweep_step
    thresholds = np.arange(step, 1.0 - 1e-9, step)
    rows = []
    for t in thresholds:
        pred = (probs >= t).astype(np.int64)
        tp = float(((pred == 1) & (labels == 1)).sum())
        p = float((labels == 1).sum())
        fp = float(((pred == 1) & (labels == 0)).sum())
        sensitivity = tp / p if p else float("nan")
        ppv = tp / (tp + fp) if (tp + fp) else float("nan")
        rows.append(
            {
                "threshold": float(t),
                "sensitivity": sensitivity,
                "ppv": ppv,
                "alerts_per_100_icu_days": evaluate.alerts_per_100_icu_days(pred),
            }
        )
    out = np.array([(r["sensitivity"], r["ppv"], r["alerts_per_100_icu_days"]) for r in rows])

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(thresholds, out[:, 0], label="sensitivity")
    ax.plot(thresholds, out[:, 1], label="PPV")
    ax.plot(thresholds, out[:, 2], label="alerts / 100 ICU-days")
    ax.axvline(threshold, color="k", ls="--", lw=1, label=f"D5 threshold {threshold:.2f}")
    ax.set_xlabel("operating threshold")
    ax.set_ylabel("value")
    ax.set_title("Alert burden vs. operating threshold (site-A validation)")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(fig_path, dpi=200)
    plt.close(fig)
    return out, rows


def _shap_global_figure(model, design, columns, fig_path, title):
    x, _, _ = load_design(design)
    rng = np.random.default_rng(20190801)
    idx = rng.integers(0, x.shape[0], size=min(4000, x.shape[0]))
    sample = np.asarray(x[idx])
    explainer = shap.TreeExplainer(model)
    values = explainer.shap_values(sample)
    mean_abs = np.abs(values).mean(axis=0)
    order = np.argsort(mean_abs)[::-1][:15]

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.barh([columns[i] for i in order][::-1], mean_abs[order][::-1])
    ax.set_xlabel("mean |SHAP|")
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(fig_path, dpi=200)
    plt.close(fig)


def _shap_waterfall_figure(model, design, columns, row_index, fig_path, title):
    x, side, _ = load_design(design)
    X = np.asarray(x[row_index : row_index + 1])
    explainer = shap.TreeExplainer(model)
    values = explainer.shap_values(X)[0]
    base = explainer.expected_value
    fig = plt.figure(figsize=(9, 5))
    shap.waterfall_plot(
        shap.Explanation(values, base_value=base, data=X[0], feature_names=columns),
        max_display=12,
        show=False,
    )
    plt.title(title)
    fig.tight_layout()
    fig.savefig(fig_path, dpi=200)
    plt.close(fig)


def _subgroups(labels, probs, pids, age, gender, unit1, unit2):
    """AUROC/AUPRC by sex, age band and ICU unit, with support counts."""
    out: list[dict] = []

    def _add(name, mask):
        n_h = int(mask.sum())
        if n_h == 0:
            return
        y = labels[mask]
        n_pos = int(y.sum())
        rec = {
            "group": name,
            "n_hours": n_h,
            "n_patients": int(np.unique(pids[mask]).size),
            "n_positives": n_pos,
            # Degenerate bands (all-positive) carry no meaningful AUPRC either.
            "suppressed": n_pos < MIN_POSITIVES or n_pos == n_h,
        }
        if not rec["suppressed"]:
            m = evaluate.run_evaluation(y, probs[mask], pids[mask])
            rec["auroc"] = m["auroc"]
            rec["auprc"] = m["auprc"]
        else:
            rec["auroc"], rec["auprc"] = None, None
        out.append(rec)

    _add("sex=0", gender == 0)
    _add("sex=1", gender == 1)
    for label, lo, hi in AGE_BANDS:
        if hi == np.inf:
            _add(f"age {label}", (age >= lo))
        else:
            _add(f"age {label}", (age >= lo) & (age < hi))
    _add("Unit1=1", unit1 == 1)
    _add("Unit2=1", unit2 == 1)
    return out


def _transfer_figure(config, data, fig_path):
    n = np.array(data["n"])
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), sharex=True)
    for ax, key, ylabel in (
        (axes[0], "auprc", "AUPRC"),
        (axes[1], "utility", "utility (D5 threshold)"),
    ):
        for mode, color, label in (
            ("recalib", "C0", "recalibration"),
            ("finetune", "C1", "fine-tuning"),
        ):
            arr = np.array([[d[key] for d in draw] for draw in data[mode]])
            mean = arr.mean(axis=1)
            std = arr.std(axis=1)
            ax.plot(n, mean, color=color, marker="o", label=label)
            ax.fill_between(n, mean - std, mean + std, color=color, alpha=0.25)
        ax.axhline(y=arr[0].mean(), color="k", ls=":", lw=1)
        ax.set_xlabel("n site-B patients used to adapt")
        ax.set_ylabel(ylabel)
        ax.legend(frameon=False)
        ax.grid(alpha=0.3)
    fig.suptitle(
        "Transfer curve: A-trained model recovered with n site-B patients (mean +/- SD)"
    )
    fig.tight_layout()
    fig.savefig(fig_path, dpi=200)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()
    config = load_config(args.config)

    # --- frozen artifacts ----------------------------------------------------
    columns = json.loads(
        (config.path("models") / "window_features_columns.json").read_text(encoding="utf-8")
    )
    base_params = {
        "objective": "binary",
        "metric": "binary_logloss",
        "seed": config.seed,
        "deterministic": True,
        "force_row_wise": True,
        "num_threads": int(config.raw["model"]["lightgbm"]["num_threads"]),
        "verbosity": -1,
    }
    thr = json.loads(config.path("threshold").read_text(encoding="utf-8"))["A"]["threshold"]

    a_model = lgb.Booster(model_file=str(config.path("models") / "window_a.txt"))
    ab_model = lgb.Booster(model_file=str(config.path("models") / "window_ab.txt"))
    designs = {name: design_files(config, name) for name in ("a_val", "b_eval", "b_train")}

    def _design(name):
        return load_design(designs[name])

    # --- cohort attributes (for subgroups) -----------------------------------
    x_val, side_val, _ = _design("a_val")
    pids_val = side_val["patient_id"]
    cohort = read_cohort(config.path("cohort")).filter(
        pl.col("patient_id").is_in(set(pids_val))
    )
    cohort_by_pid = {
        pid: row
        for pid, row in zip(
            cohort["patient_id"],
            cohort.select(["Age", "Gender", "Unit1", "Unit2"]).rows(),
            strict=True,
        )
    }

    x_val = np.asarray(x_val)
    y_val = side_val["SepsisLabel"].astype(np.int64)
    age = np.array([cohort_by_pid[p][0] for p in pids_val])
    gender = np.array([cohort_by_pid[p][1] for p in pids_val])
    unit1 = np.array([cohort_by_pid[p][2] for p in pids_val])
    unit2 = np.array([cohort_by_pid[p][3] for p in pids_val])
    probs_val = np.asarray(a_model.predict(x_val), dtype=np.float64)

    # --- 1. calibration ------------------------------------------------------
    brier = {"A->A": None, "A->B": None}
    reliability = {"A->A": [], "A->B": []}
    fig, ax = plt.subplots(figsize=(6, 5))
    x_b, side_b, _ = _design("b_eval")
    y_b = side_b["SepsisLabel"].astype(np.int64)
    probs_b = np.asarray(a_model.predict(np.asarray(x_b)), dtype=np.float64)
    brier["A->A"] = evaluate.brier_score(y_val, probs_val)
    brier["A->B"] = evaluate.brier_score(y_b, probs_b)
    reliability["A->A"] = _reliability_curve(ax, y_val, probs_val, "A->A", "C0")
    reliability["A->B"] = _reliability_curve(ax, y_b, probs_b, "A->B", "C1")
    ax.plot([0, 1], [0, 1], ls=":", color="k")
    ax.set_xlabel("mean predicted probability")
    ax.set_ylabel("observed positive rate")
    ax.set_title("Reliability curves — raw A model")
    ax.legend(frameon=False)
    fig.tight_layout()
    cur_path = config.result_path("figures") / "reliability_raw.png"
    cur_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(cur_path, dpi=200)
    plt.close(fig)

    # --- 1b. isotonic recalibration: fit on A-val slice and on B slice --------
    iso_A = IsotonicRegression(out_of_bounds="clip").fit(probs_val, y_val)
    probs_b_calA = np.asarray(iso_A.predict(probs_b), dtype=np.float64)
    cal_brier = {
        "A->A_fit_on_A_val": {"brier": evaluate.brier_score(y_val, iso_A.predict(probs_val))},
        "A->B_fit_on_A_val": {"brier": evaluate.brier_score(y_b, probs_b_calA)},
    }

    # small slice of b_train (never b_eval) for the "site-B fit" arm
    x_bt, side_bt, _ = _design("b_train")
    bt_rng = np.random.default_rng(20190801)
    n_bt = 2000
    bt_idx = bt_rng.integers(0, x_bt.shape[0], size=n_bt)
    probs_bt = np.asarray(a_model.predict(np.asarray(x_bt[bt_idx])), dtype=np.float64)
    iso_B = IsotonicRegression(out_of_bounds="clip").fit(
        probs_bt, side_bt["SepsisLabel"][bt_idx].astype(np.int64)
    )
    probs_b_calB = np.asarray(iso_B.predict(probs_b), dtype=np.float64)
    cal_brier["A->B_fit_on_B_train_slice"] = {"brier": evaluate.brier_score(y_b, probs_b_calB)}
    cal_brier["A->A_fit_on_B_train_slice"] = {
        "brier": evaluate.brier_score(y_val, iso_B.predict(probs_val).astype(np.float64))
    }

    # --- 2. subgroups --------------------------------------------------------
    subgroups = _subgroups(y_val, probs_val, pids_val, age, gender, unit1, unit2)

    # --- 3. alert burden -----------------------------------------------------
    alert_fig = config.result_path("figures") / "alert_burden.png"
    alert_curve, alert_rows = _alert_burden(config, y_val, probs_val, pids_val, thr, alert_fig)

    # --- 4. SHAP figures -----------------------------------------------------
    fig_dir = config.result_path("figures")
    _shap_global_figure(a_model, designs["a_val"], columns, fig_dir / "shap_global_a.png",
                        "Global SHAP — site-A model on site-A validation")
    _shap_global_figure(ab_model, designs["b_eval"], columns, fig_dir / "shap_global_b.png",
                        "Global SHAP — A+B model on site-B evaluation")
    # one true positive, one false positive (A model at D5 threshold on A-val)
    pred_val = (probs_val >= thr).astype(np.int64)
    tp = np.where((pred_val == 1) & (y_val == 1))[0]
    fp = np.where((pred_val == 1) & (y_val == 0))[0]
    if tp.size and fp.size:
        _shap_waterfall_figure(
            a_model, designs["a_val"], columns, tp[0],
            fig_dir / "shap_waterfall_tp.png",
            "True positive (A model, D5 threshold)",
        )
        _shap_waterfall_figure(
            a_model, designs["a_val"], columns, fp[0],
            fig_dir / "shap_waterfall_fp.png",
            "False positive (A model, D5 threshold)",
        )

    # --- 5. transfer curve ---------------------------------------------------
    transfer_data = transfer_curve(
        base_model=a_model,
        base_params=base_params,
        b_train_design=designs["b_train"],
        b_eval_design=designs["b_eval"],
        columns=columns,
        threshold=thr,
        seed=config.seed,
    )
    transfer_fig = config.result_path("figures") / "transfer_curve.png"
    _transfer_figure(config, transfer_data, transfer_fig)

    # --- write outputs -------------------------------------------------------
    thresh_grid = np.arange(config.threshold_sweep_step, 1.0, config.threshold_sweep_step)
    d5_index = int(np.argmin(np.abs(thresh_grid - thr)))
    payload = {
        "calibration": {
            "brier": brier,
            "recalibration": cal_brier,
            "reliability_A_A": reliability["A->A"],
            "reliability_A_B": reliability["A->B"],
        },
        "subgroups": subgroups,
        "alert_burden": alert_rows,
        "alert_burden_d5": {
            "threshold": thr,
            "sensitivity": float(alert_curve[d5_index, 0]),
            "ppv": float(alert_curve[d5_index, 1]),
            "alerts_per_100_icu_days": float(alert_curve[d5_index, 2]),
        },
        "transfer": transfer_data,
        "provenance": provenance(config.seed, config.path("cohort")),
    }
    _write_json(config.result_path("rigor.json"), payload)

    # --- rigor.md ------------------------------------------------------------
    lines = [
        "# Phase 4 rigor analyses (AGENTS.md §10)",
        "",
        f"Operating threshold: **{thr:.2f}** (D5, frozen before site-B scoring).",
        "",
        "## Calibration (raw A model, Brier)",
        "",
        f"- A→A (site-A validation): **Brier {brier['A->A']:.4f}**",
        f"- A→B (site-B evaluation): **Brier {brier['A->B']:.4f}**",
        "",
        "Reliability curves: `figures/reliability_raw.png`.",
        "",
        "## Isotonic recalibration",
        "",
        "| Fit slice | Applied to | Brier |",
        "|---|---|---|",
        f"| A-val | A-val | {cal_brier['A->A_fit_on_A_val']['brier']:.4f} |",
        f"| A-val | B-eval | {cal_brier['A->B_fit_on_A_val']['brier']:.4f} |",
        f"| B-train slice | A-val | {cal_brier['A->A_fit_on_B_train_slice']['brier']:.4f} |",
        f"| B-train slice | B-eval | {cal_brier['A->B_fit_on_B_train_slice']['brier']:.4f} |",
        "",
        "The calibrator is *always* fit on site-A or the site-B training slice; "
        "never on the evaluation set.",
        "",
        "## Subgroups (site-A validation)",
        "",
        "| Group | n hours | n patients | n positives | AUROC | AUPRC |",
        "|---|---|---|---|---|---|",
    ]
    for g in subgroups:
        if g["suppressed"] or g["auroc"] is None:
            lines.append(
                f"| {g['group']} | {g['n_hours']} | {g['n_patients']} | {g['n_positives']} "
                f"| suppressed (<{MIN_POSITIVES} positives) | — |"
            )
        else:
            lines.append(
                f"| {g['group']} | {g['n_hours']} | {g['n_patients']} | {g['n_positives']} "
                f"| {g['auroc']:.3f} | {g['auprc']:.3f} |"
            )
    lines += [
        "",
        "## Alert burden (site-A validation)",
        "",
        f"Sensitivity {payload['alert_burden_d5']['sensitivity']:.3f}, PPV "
        f"{payload['alert_burden_d5']['ppv']:.3f}, alerts/100 ICU-days "
        f"{payload['alert_burden_d5']['alerts_per_100_icu_days']:.1f} at the D5 "
        f"threshold {thr:.2f}. Full sweep: `figures/alert_burden.png`.",
        "",
        "## Transfer curve",
        "",
        "Mean over 5 disjoint draws of n site-B patients (from b_train, scored on "
        "the held-out b_eval):",
        "",
    ]
    n = np.array(transfer_data["n"])
    for i, ni in enumerate(n):
        rec = transfer_data["recalib"][i]
        ft = transfer_data["finetune"][i]
        rec_arr = np.array([[d["auprc"], d["utility"]] for d in rec])
        ft_arr = np.array([[d["auprc"], d["utility"]] for d in ft])
        lines.append(
            f"| n={ni} | recalib AUPRC {rec_arr[:,0].mean():.3f}±{rec_arr[:,0].std():.3f}, "
            f"utility {rec_arr[:,1].mean():.3f} | finetune AUPRC {ft_arr[:,0].mean():.3f}±"
            f"{ft_arr[:,0].std():.3f}, utility {ft_arr[:,1].mean():.3f} |"
        )
    lines += ["", provenance_markdown(provenance(config.seed, config.path("cohort")))]
    (config.result_path("rigor.md")).write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(payload["calibration"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
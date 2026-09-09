"""Transfer curve (AGENTS.md §10.5).

Recovering a transferred model's performance with a little target-site data.
For each `n` of site-B patients we either re-calibrate (isotonic regression on
the base model's probabilities) or fine-tune (continue LightGBM boosting on the
n patients' rows), then score on the held-out site-B evaluation set.

Adaptation data is drawn from `b_train` — a deterministic, by-patient split of
site B into a training slice and a held-out evaluation slice, recorded in
docs/decisions.md. `b_eval` is never adapted to and never appears on the
training side of anything here; scoring happens exactly there, and a drawn
patient can never appear in the scoring set by construction.

Everything is deterministic for a given seed, and the D5 operating threshold
stays frozen at its site-A value: the study measures what an operator who only
has n site-B patients can do without re-tuning the operating point.
"""

from __future__ import annotations

import gc

import lightgbm as lgb
import numpy as np
from sklearn.isotonic import IsotonicRegression

from sepsis.window_design import DesignFiles, load_design

#: Fine-tuning tree budget. Small on purpose: 2000 patients is the largest n,
#: and the study should isolate *adaptation* rather than a second grid search.
FINE_TUNE_ROUNDS = 200

#: The n values of the transfer curve (AGENTS.md §10.5).
TRANSFER_N = (0, 50, 100, 250, 500, 1000, 2000)

#: Random draws per n.
DRAWS = 5


def predict_design(model, design: DesignFiles) -> dict[str, np.ndarray]:
    """Hourly probabilities plus labels and patient ids for one design."""
    x, side, _ = load_design(design)
    return {
        "probs": np.asarray(model.predict(x), dtype=np.float64),
        "labels": side["SepsisLabel"].astype(np.int64),
        "pids": side["patient_id"],
    }


def draw_patient_mask(
    pids: np.ndarray, n_patients: int, rng
) -> np.ndarray:
    """Boolean mask marking one deterministic by-patient draw of `n_patients`."""
    unique_pids = np.unique(pids)
    if n_patients > len(unique_pids):
        raise ValueError(f"cannot draw {n_patients} patients from {len(unique_pids)} available")
    chosen = rng.choice(unique_pids, size=n_patients, replace=False)
    return np.isin(pids, chosen)


def assert_adaptation_disjoint(draw_pids: np.ndarray, eval_pids: np.ndarray) -> None:
    """A patient in the adaptation draw must never be in the scoring set (§10.5)."""
    overlap = np.intersect1d(draw_pids, eval_pids)
    if overlap.size:
        raise AssertionError(
            f"{overlap.size} patients appear in both the adaptation draw and the "
            "site-B evaluation set — transfer adaptation leaked into scoring (§10.5)"
        )


def recalibrate_isotonic(fit_probs: np.ndarray, fit_labels: np.ndarray):
    """Isotonic calibrator fit on (probabilities, labels); returns a predictor."""
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(fit_probs, fit_labels)
    return iso


def fine_tune(
    base_model,
    x_draw: np.ndarray,
    y_draw: np.ndarray,
    columns: list[str],
    params: dict,
    rounds: int = FINE_TUNE_ROUNDS,
):
    """Continue LightGBM boosting from `base_model` on the drawn rows."""
    if x_draw.shape[0] == 0:
        return base_model
    dataset = lgb.Dataset(x_draw, label=y_draw, feature_name=columns, free_raw_data=True)
    model = lgb.train(
        params,
        dataset,
        num_boost_round=rounds,
        init_model=base_model,
        callbacks=[lgb.log_evaluation(0)],
    )
    del dataset
    gc.collect()
    return model


def transfer_curve(
    base_model,
    base_params: dict,
    b_train_design: DesignFiles,
    b_eval_design: DesignFiles,
    columns: list[str],
    threshold: float,
    n_values: tuple[int, ...] = TRANSFER_N,
    draws: int = DRAWS,
    seed: int = 20190801,
) -> dict:
    """AUPRC and utility vs n for recalibration and fine-tuning.

    Returns::

        {
          "n": [0, 50, ...],
          "recalib":  [[{auprc, utility}, ... per draw], ... per n],
          "finetune": [...same...],
        }
    """
    from sepsis import evaluate

    # The base model's probabilities are computed once and reused: the isotonic
    # calibrator fits on them, and n=0 is the un-adapted base.
    train_preds = predict_design(base_model, b_train_design)
    eval_preds = predict_design(base_model, b_eval_design)
    x_train = np.asarray(load_design(b_train_design)[0])
    x_eval = np.asarray(load_design(b_eval_design)[0])
    y_eval = eval_preds["labels"]
    pids_eval = eval_preds["pids"]

    results: dict = {"n": [], "recalib": [], "finetune": []}
    for n in n_values:
        results["n"].append(int(n))
        rec_draws: list[dict] = []
        ft_draws: list[dict] = []
        for draw in range(draws):
            rng = np.random.default_rng(seed + n * 17 + draw * 101)

            rec_probs = eval_preds["probs"]  # n == 0: un-adapted base
            ft_probs = eval_preds["probs"]
            if n > 0:
                mask = draw_patient_mask(train_preds["pids"], n, rng)
                draw_pids = np.unique(train_preds["pids"][mask])
                assert_adaptation_disjoint(draw_pids, np.unique(pids_eval))

                # --- recalibration (fit on base probs of the n patients) ----
                iso = recalibrate_isotonic(
                    train_preds["probs"][mask], train_preds["labels"][mask]
                )
                rec_probs = np.asarray(iso.predict(eval_preds["probs"]), dtype=np.float64)

                # --- fine-tuning (continue boosting on the n patients) -------
                adapted = fine_tune(
                    base_model, x_train[mask], train_preds["labels"][mask], columns, base_params
                )
                ft_probs = np.asarray(adapted.predict(x_eval), dtype=np.float64)
                del adapted
                gc.collect()

            for mode, probs in (("recalib", rec_probs), ("finetune", ft_probs)):
                metrics = evaluate.run_evaluation(y_eval, probs, pids_eval, threshold=threshold)
                row = {"auprc": metrics["auprc"], "utility": metrics["utility"]}
                (rec_draws if mode == "recalib" else ft_draws).append(row)

        results["recalib"].append(rec_draws)
        results["finetune"].append(ft_draws)

    return results
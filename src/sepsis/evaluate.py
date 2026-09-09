"""The single evaluation entry point (AGENTS.md §8.2).

Every metric a reported number depends on is computed here, so a number can
never come out of two different pieces of code. AUROC and AUPRC use
scikit-learn; the utility uses the official, vendored Challenge scorer
(AGENTS.md §5 — imported, never reimplemented); prevalence and calibration are
computed here for the rule §2.7 that they travel together.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

from sepsis.vendor.evaluate_sepsis_score import (
    compute_accuracy_f_measure,
    compute_prediction_utility,
)

#: The official scoring horizon and utility weights. Copied from the vendored
#: scorer's `evaluate_sepsis_score` so the normalized utility here is the
#: normalized utility of the Challenge, assembled without the file round-trip.
DT_EARLY = -12
DT_OPTIMAL = -6
DT_LATE = 3
MAX_U_TP = 1
MIN_U_FN = -2
U_FP = -0.05
U_TN = 0


def reliability_table(
    labels: np.ndarray, probs: np.ndarray, bins: int = 10
) -> list[dict[str, Any]]:
    """Reliability bins over probability: mean prediction vs observed rate.

    Rows with extreme probs need support before they mean anything, so each row
    carries its bin population.
    """
    labels = np.asarray(labels, dtype=np.int64)
    probs = np.asarray(probs, dtype=np.float64)
    edges = np.linspace(0.0, 1.0, bins + 1)
    rows: list[dict[str, Any]] = []
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        in_bin = (probs >= lo) & (probs < hi)
        if i == bins - 1:  # the last bin closes on 1.0
            in_bin |= probs == 1.0
        n = int(in_bin.sum())
        rows.append(
            {
                "bin": f"{lo:.2f}-{hi:.2f}",
                "n": n,
                "mean_prediction": float(probs[in_bin].mean()) if n else None,
                "positive_rate": float(labels[in_bin].mean()) if n else None,
            }
        )
    return rows


def normalized_utility(
    labels: np.ndarray,
    predictions: np.ndarray,
    patient_ids: np.ndarray,
) -> float:
    """The Challenge's normalized utility, computed per patient.

    Each per-patient utility comes from the vendored `compute_prediction_utility`
    with the scorer's own parameters; the normalization (observed vs best vs
    inaction) mirrors `evaluate_sepsis_score` exactly, so the number equals
    what the official CLI would report for the same binarized predictions.
    """
    labels = np.asarray(labels, dtype=np.int64)
    predictions = np.asarray(predictions, dtype=np.int64)
    patient_ids = np.asarray(patient_ids)

    if labels.shape != predictions.shape or labels.shape != patient_ids.shape:
        raise ValueError("labels, predictions and patient_ids must be the same length")

    unique, inverse = np.unique(patient_ids, return_inverse=True)

    observed_sum = 0.0
    best_sum = 0.0
    inaction_sum = 0.0

    for k in range(len(unique)):
        mask = inverse == k
        y = labels[mask]
        n = len(y)

        inaction_pred = np.zeros(n, dtype=np.int64)
        inaction_sum += float(compute_prediction_utility(y, inaction_pred, check_errors=False))

        best_pred = np.zeros(n, dtype=np.int64)
        if y.any():
            t_sepsis = int(np.argmax(y)) - DT_OPTIMAL  # int, as in the official scorer
            best_pred[max(0, t_sepsis + DT_EARLY) : min(t_sepsis + DT_LATE + 1, n)] = 1
        best_sum += float(compute_prediction_utility(y, best_pred, check_errors=False))

        observed_sum += float(compute_prediction_utility(y, predictions[mask], check_errors=False))

    denominator = best_sum - inaction_sum
    if denominator == 0.0:
        return float("nan")
    return float((observed_sum - inaction_sum) / denominator)


def _binary_metrics(labels: np.ndarray, predictions: np.ndarray) -> tuple[float, float]:
    accuracy, f_measure = compute_accuracy_f_measure(labels, predictions, check_errors=False)
    return float(accuracy), float(f_measure)


def run_evaluation(
    labels: np.ndarray,
    probs: np.ndarray,
    patient_ids: np.ndarray,
    threshold: float = 0.5,
    calibration_bins: int = 10,
) -> dict[str, Any]:
    """AUROC, AUPRC, utility, prevalence and calibration, together (§2.7).

    `threshold` binarizes probabilities for the utility score; AUROC/AUPRC use
    the probabilities themselves. Only one function computes evaluation numbers
    anywhere in this repository.
    """
    labels = np.asarray(labels, dtype=np.int64)
    probs = np.asarray(probs, dtype=np.float64)
    patient_ids = np.asarray(patient_ids)
    if labels.shape != probs.shape or labels.shape != patient_ids.shape:
        raise ValueError("labels, probs and patient_ids must be the same length")

    n_hours = len(labels)
    n_patients = int(np.unique(patient_ids).size)

    if n_hours == 0:
        raise ValueError("cannot evaluate an empty cohort")

    # roc_auc_score raises on a single class; a validation split with no
    # positives is reported rather than crashed on.
    if labels.min() == labels.max():
        auroc = float("nan")
        auprc = float("nan")
    else:
        auroc = float(roc_auc_score(labels, probs))
        auprc = float(average_precision_score(labels, probs))

    predictions = (probs >= threshold).astype(np.int64)
    accuracy, f_measure = _binary_metrics(labels, predictions)

    return {
        "auroc": auroc,
        "auprc": auprc,
        "prevalence": float(labels.mean()),
        "accuracy": accuracy,
        "f_measure": f_measure,
        "utility": normalized_utility(labels, predictions, patient_ids),
        "threshold": threshold,
        "n_patients": n_patients,
        "n_hours": n_hours,
        "calibration": reliability_table(labels, probs, calibration_bins),
    }

"""Scorer and evaluation harness (AGENTS.md §8).

- Tier 1: the vendored scorer reproduces a known value.
- The single evaluation entry point agrees with the official CLI on a
  hand-built cohort.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from sepsis import evaluate
from sepsis.vendor.evaluate_sepsis_score import (
    compute_auc,
    compute_prediction_utility,
)
from sepsis.vendor.evaluate_sepsis_score import (
    evaluate_sepsis_score as official_evaluate,
)


def _write_psv(directory, name, rows, header):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.psv"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header + "\n")
        for row in rows:
            fh.write("|".join(str(v) for v in row) + "\n")
    return path


def test_vendored_utility_reproduces_the_documented_example():
    """The value printed in the scorer's own docstring is reproduced."""
    labels = np.array([0, 0, 0, 0, 1, 1])
    predictions = np.array([0, 0, 1, 1, 1, 1])
    utility = compute_prediction_utility(labels, predictions, check_errors=False)
    assert utility == 3.388888888888889


def test_vendored_auroc_reproduces_the_documented_example():
    labels = np.array([0, 0, 0, 0, 1, 1])
    predictions = np.array([0.3, 0.4, 0.6, 0.7, 0.8, 0.8])
    auroc, auprc = compute_auc(labels, predictions)
    assert auroc == 1.0
    assert auprc == 1.0


def test_evaluate_matches_the_official_cli(tmp_path):
    """run_evaluation agrees with the vendored CLI on a hand-built cohort."""
    cases = [
        # pid, labels, probabilities
        ("case0", [0, 0, 0, 0, 1, 1, 1], [0.1, 0.2, 0.3, 0.4, 0.8, 0.9, 0.95]),
        (
            "case1",
            [0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1],
            [0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.7, 0.8, 0.9],
        ),
        ("case2", [0, 0, 0, 0, 0, 0], [0.01, 0.02, 0.03, 0.04, 0.05, 0.06]),
    ]

    label_dir = tmp_path / "labels"
    pred_dir = tmp_path / "predictions"

    patient_ids = []
    flat_labels = []
    flat_probs = []
    for pid, lbl, prb in cases:
        patient_ids.extend([pid] * len(lbl))
        flat_labels.extend(lbl)
        flat_probs.extend(prb)
        _write_psv(label_dir, pid, [[v] for v in lbl], "SepsisLabel")
        _write_psv(
            pred_dir,
            pid,
            [[p, "1" if p >= 0.5 else "0"] for p in prb],
            "PredictedProbability|PredictedLabel",
        )

    official = official_evaluate(str(label_dir), str(pred_dir))
    ours = evaluate.run_evaluation(
        np.array(flat_labels, dtype=np.int64),
        np.array(flat_probs, dtype=np.float64),
        np.array(patient_ids),
        threshold=0.5,
    )

    assert math.isclose(ours["auroc"], official[0], rel_tol=1e-12)
    assert math.isclose(ours["auprc"], official[1], rel_tol=1e-12)
    assert math.isclose(ours["utility"], official[4], rel_tol=1e-12)


def test_utility_is_zero_for_never_positive_predictions():
    """Inaction (predict nothing) is the zero point of the normalized utility."""
    labels = np.array([0, 0, 0, 0, 1, 1])
    predictions = np.zeros(6, dtype=np.int64)
    patient_ids = np.array(["p1"] * 6)
    utility = evaluate.normalized_utility(labels, predictions, patient_ids)
    assert utility == 0.0


def test_reliability_table_carries_support_counts():
    labels = np.array([0, 0, 1, 1, 1] * 20, dtype=np.int64)
    probs = np.array([0.05, 0.2, 0.4, 0.6, 0.9] * 20, dtype=np.float64)
    table = evaluate.reliability_table(labels, probs, bins=10)
    assert len(table) == 10
    assert sum(row["n"] for row in table) == len(labels)
    # Empty bins are legitimate (no predictions land in them); populated bins
    # must carry both a mean prediction and an observed rate.
    populated = [row for row in table if row["n"] > 0]
    assert len(populated) == len(set(row["bin"] for row in populated if row["n"])) > 0
    assert all(
        row["mean_prediction"] is not None and row["positive_rate"] is not None for row in populated
    )


def test_evaluation_refuses_mismatched_inputs():
    with pytest.raises(ValueError):
        evaluate.run_evaluation(np.array([0, 1]), np.array([0.1, 0.2, 0.3]), np.array(["a", "b"]))

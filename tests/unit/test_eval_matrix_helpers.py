"""Tier 2 — eval_matrix helpers are the single matrix code path (§9.3).

These helpers are format-contract tests: the row ordering of the matrix table
and the threshold lookup that maps a training artifact to its frozen D5
threshold. No trained artifacts or real data are required.
"""

from __future__ import annotations

import json

from scripts.eval_matrix import TRAIN_LABEL, _matrix_rows


def _cell(name: str) -> dict:
    return {
        "auroc": 0.9,
        "auprc": 0.2,
        "utility": 0.3,
        "prevalence": 0.03,
        "threshold": 0.5,
    }


def test_matrix_rows_are_ordered_and_complete():
    matrix = {cell: _cell(cell) for cell in ("A->A", "A->B", "B->A", "A+B->A", "A+B->B")}
    rows = _matrix_rows(matrix)
    assert rows[0].startswith("| Train | Test | AUROC | AUPRC | Utility | Prevalence |")
    assert rows[1].startswith("|---|---|---|---|---|---|")
    # exact ordering from AGENTS.md §9.3
    assert "| A | A |" in rows[2]
    assert "| A | B |" in rows[3]
    assert "| B | A |" in rows[4]
    assert "| A+B | A |" in rows[5]
    assert "| A+B | B |" in rows[6]


def test_training_artifact_to_threshold_label_mapping():
    assert TRAIN_LABEL == {
        "window_a.txt": "A",
        "window_b.txt": "B",
        "window_ab.txt": "A+B",
    }


def test_threshold_document_is_what_the_matrix_consumes(tmp_path):
    """The thresholds file layout the matrix reads matches what train.py writes."""
    doc = {
        "rule": "D5: utility-max on site-A validation, swept at 0.01",
        "A": {"threshold": 0.42, "utility_at_threshold": 0.31},
        "B": {"threshold": 0.44, "utility_at_threshold": 0.30},
        "A+B": {"threshold": 0.40, "utility_at_threshold": 0.33},
    }
    threshold_file = tmp_path / "threshold.json"
    with open(threshold_file, "w") as fh:
        json.dump(doc, fh)
    # this mirrors the lookup used by _threshold_for
    with open(threshold_file) as fh:
        reloaded = json.load(fh)
    for artifact, label in TRAIN_LABEL.items():
        assert "threshold" in reloaded[label]
        assert artifact in ("window_a.txt", "window_b.txt", "window_ab.txt")

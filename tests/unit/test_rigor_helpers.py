"""Phase 4 rigor-analysis helpers (AGENTS.md §10) — plotting and suppression logic."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import numpy as np
import pytest

from scripts.eval_rigor import (  # noqa: E402
    _alert_burden,
    _reliability_curve,
    _subgroups,
    _transfer_figure,
)
from sepsis.config import load_config  # noqa: E402

SAMPLE = 4000


def _toy():
    rng = np.random.default_rng(0)
    probs = rng.random(SAMPLE)
    labels = (probs > 0.9).astype(np.int64)  # 10% positives
    pids = np.arange(SAMPLE // 10).repeat(10)  # 400 patients, 10 hours each
    return labels, probs, pids


def test_alert_burden_happy_path(tmp_path):
    config = load_config()
    labels, probs, pids = _toy()
    curve, rows = _alert_burden(config, labels, probs, pids, 0.5, tmp_path / "a.png")
    assert (tmp_path / "a.png").stat().st_size > 0
    assert {"threshold", "sensitivity", "ppv", "alerts_per_100_icu_days"} == set(rows[0])
    assert all(isinstance(r["alerts_per_100_icu_days"], float) for r in rows)
    # monotone threshold grid
    ts = np.array([r["threshold"] for r in rows])
    assert (np.diff(ts) > 0).all()


def test_subgroups_metrics_and_suppression():
    labels, probs, pids = _toy()
    age = np.where(labels, 50.0, 70.0)
    gender = np.zeros(SAMPLE)
    unit1, unit2 = np.ones(SAMPLE), np.zeros(SAMPLE)

    # give the "<40" age band a handful of positives so it must be suppressed
    age[:10] = 20.0
    age[np.where(labels == 1)[0][:10]] = 20.0

    rows = _subgroups(labels, probs, pids, age, gender, unit1, unit2)
    # sexes(sex=0 only) + age bands <40,40-65,65-80 + Unit1; 80+ and Unit2=1 empty
    assert len(rows) == 5
    by_group = {r["group"]: r for r in rows}
    assert by_group["age <40"]["n_positives"] < 50
    assert by_group["age <40"]["suppressed"]
    for r in rows:
        assert set(r) >= {"group", "n_hours", "n_patients", "n_positives", "suppressed"}
        if r["suppressed"]:
            assert r["auroc"] is None and r["auprc"] is None
    # every patient-hour is accounted for exactly once across the age bands
    assert sum(r["n_hours"] for r in rows if r["group"].startswith("age ")) == labels.size
    # an unsuppressed subgroup carries both metrics
    for r in rows:
        if not r["suppressed"]:
            assert 0 <= r["auroc"] <= 1 and 0 <= r["auprc"] <= 1


def test_reliability_curve_writes_plot(tmp_path):
    labels, probs, _ = _toy()
    fig = pytest.importorskip("matplotlib").pyplot.figure()
    ax = fig.add_subplot(1, 1, 1)
    rows = _reliability_curve(ax, labels, probs, "toy", "C0")
    assert len(rows) == 10
    for r in rows:
        assert 0.0 <= r["mean_prediction"] <= 1.0
        assert 0.0 <= r["positive_rate"] <= 1.0


def test_transfer_figure_writes_std_error_bands(tmp_path):
    data = {
        "n": [0, 50, 100],
        "recalib": [
            [{"auprc": 0.5 + i / 100, "utility": 0.1} for i in range(5)] for _ in range(3)
        ],
        "finetune": [
            [{"auprc": 0.5 + i / 100, "utility": 0.2} for i in range(5)] for _ in range(3)
        ],
    }
    fig = tmp_path / "transfer.png"
    _transfer_figure(load_config(), data, fig)
    assert fig.stat().st_size > 0
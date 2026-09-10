"""Tests for the Phase 6 model service (src/model_api/main.py).

The real pipeline artifact (results/models/window_a.txt, results/threshold.json)
only exists on the machine that ran `make train`, so these tests point the
service at a tiny throwaway LightGBM model and threshold via MODEL_PATH and
THRESHOLD_PATH.
"""

from __future__ import annotations

import json

import lightgbm as lgb
import numpy as np
import polars as pl
import pytest
from fastapi.testclient import TestClient

from sepsis.config import CLINICAL_VARIABLES
from sepsis.features import window_feature_columns, window_features_frame

COLUMNS = window_feature_columns((6, 12, 24))


@pytest.fixture()
def model_service(tmp_path, monkeypatch):
    rng = np.random.default_rng(7)
    x = rng.normal(size=(200, len(COLUMNS)))
    y = rng.integers(0, 2, size=200)
    booster = lgb.train(
        {"objective": "binary", "verbose": -1, "seed": 0},
        lgb.Dataset(x, label=y, feature_name=COLUMNS),
        num_boost_round=3,
    )
    model_path = tmp_path / "window_a.txt"
    booster.save_model(str(model_path))

    threshold_path = tmp_path / "threshold.json"
    threshold_path.write_text(json.dumps({"A": {"threshold": 0.42}}), encoding="utf-8")

    monkeypatch.setenv("MODEL_PATH", str(model_path))
    monkeypatch.setenv("THRESHOLD_PATH", str(threshold_path))
    monkeypatch.setenv("THRESHOLD_DECISION", "A")
    from model_api import main as api

    with TestClient(api.app) as client:
        yield client, api


def _observations():
    """A tiny but contiguous observation history covering all 34 variables."""
    rows = []
    for hour in range(0, 24):
        for i, var in enumerate(CLINICAL_VARIABLES):
            rows.append({"hour": hour, "var": var, "value": 90.0 + i if hour > 3 else None})
    return rows


_DEMOGRAPHICS = {"age": 68.0, "gender": 1, "unit1": 1.0, "unit2": 0.0, "iculos": 24}


def test_features_endpoint_returns_full_column_row(model_service):
    client, api = model_service
    resp = client.post(
        "/features", json={"patient_id": "p1", "observations": _observations(), **_DEMOGRAPHICS}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["hour"] == 23
    assert set(body["features"]) == set(api._COLUMNS)
    assert body["features"]["HR_min_6"] is not None


def test_features_rejects_non_contiguous_hours(model_service):
    """A hole in the middle of the stay is a data problem, not a blank hour."""
    client, _ = model_service
    obs = [o for o in _observations() if o["hour"] != 5]
    resp = client.post("/features", json={"patient_id": "p1", "observations": obs})
    assert resp.status_code == 422
    assert "contiguously" in resp.json()["detail"]


def test_features_rejects_starting_above_hour_zero(model_service):
    client, _ = model_service
    obs = [o for o in _observations() if o["hour"] > 5]
    resp = client.post("/features", json={"patient_id": "p1", "observations": obs})
    assert resp.status_code == 422


def test_features_rejects_unknown_variable(model_service):
    client, _ = model_service
    obs = [{"hour": 0, "var": "NotAVariable", "value": 1.0}]
    resp = client.post("/features", json={"patient_id": "p1", "observations": obs})
    assert resp.status_code == 422


def test_predict_output_contract(model_service):
    client, api = model_service
    built = client.post(
        "/features", json={"patient_id": "p1", "observations": _observations(), **_DEMOGRAPHICS}
    ).json()
    resp = client.post("/predict", json={"features": built["features"]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert 0.0 <= body["risk"] <= 1.0
    assert body["threshold"] == 0.42
    assert body["indicator"] in ("info", "warning", "critical")
    assert len(body["top_shap"]) == 10
    assert all({"feature", "value"} <= set(t) for t in body["top_shap"])


def test_predict_rejects_incomplete_feature_row(model_service):
    client, _ = model_service
    features = dict.fromkeys(COLUMNS, 0.0)
    features[COLUMNS[0]] = None
    resp = client.post("/predict", json={"features": features})
    assert resp.status_code == 422
    assert "missing" in resp.json()["detail"]


def test_lifespan_requires_model_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PATH", str(tmp_path / "does-not-exist.txt"))
    monkeypatch.setenv("THRESHOLD_PATH", str(tmp_path / "threshold.json"))
    threshold = json.dumps({"A": {"threshold": 0.5}})
    (tmp_path / "threshold.json").write_text(threshold, encoding="utf-8")
    import importlib

    import model_api.main as api

    importlib.reload(api)
    with pytest.raises(RuntimeError, match="missing model artifact"), TestClient(api.app):
        pass


def test_features_match_training_code_path(model_service):
    """§12.3: the serving feature builder is provably the training feature builder.

    Feed /features the same hourly table `window_features_frame` consumes and
    assert the last-hour row equals the training frame's last row elementwise.
    """
    import model_api.main as api

    rng = np.random.default_rng(11)
    n_hours = 5
    observations = []
    for hour in range(n_hours):
        for i, var in enumerate(CLINICAL_VARIABLES):
            value = float(rng.normal(70 + i, 10)) if hour >= 2 else None
            observations.append({"hour": hour, "var": var, "value": value})
    demographics = {"age": 71.0, "gender": 1, "unit1": 1.0, "unit2": 0.0, "iculos": 9}

    client, _ = model_service
    payload = {"patient_id": "p-parity", "observations": observations, **demographics}
    resp = client.post("/features", json=payload)
    assert resp.status_code == 200, resp.text
    served = resp.json()["features"]

    frame = pl.DataFrame(
        {
            "patient_id": ["p-parity"] * n_hours,
            "site": ["S"] * n_hours,
            "hour": list(range(n_hours)),
            **{
                var: [obs["value"] for obs in observations if obs["var"] == var]
                for var in CLINICAL_VARIABLES
            },
            "Age": [71.0] * n_hours,
            "Gender": [1.0] * n_hours,
            "Unit1": [1.0] * n_hours,
            "Unit2": [0.0] * n_hours,
            "HospAdmTime": [0.0] * n_hours,
            "ICULOS": [9.0] * n_hours,
            "SepsisLabel": [0] * n_hours,
        }
    )
    expected = window_features_frame(frame, (6, 12, 24)).select(api._COLUMNS).row(-1)

    for name, expected_value in zip(api._COLUMNS, expected, strict=True):
        served_value = served[name]
        if expected_value != expected_value:  # NaN
            assert served_value is None, name
        else:
            assert served_value is not None
            assert served_value == pytest.approx(expected_value, abs=1e-5), name

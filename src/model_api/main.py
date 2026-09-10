"""Step 6 model service (AGENTS.md §12.1).

Two endpoints:

  POST /features  — build one hourly feature row from the patient's observation
                    history so far. Runs the exact Phase-3 code path
                    (`features.window_features_frame`, `_feature_block`), so
                    serving-time features are provably the same as training-time
                    features (§12.3). Input must contain only hours <= t.

  POST /predict   — score one feature row with the frozen LightGBM model. The
                    row must come from /features (or be built identically); the
                    service returns risk, the D5 threshold, and the top SHAP
                    contributors.

Model, threshold and column order are loaded once at startup from the same
artifacts the training pipeline wrote (results/models/, results/threshold.json),
so the service cannot drift from the matrix that was scored.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import lightgbm as lgb
import numpy as np
import polars as pl
import shap
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from sepsis.config import CLINICAL_VARIABLES, load_config
from sepsis.features import window_feature_columns, window_features_frame


class ObservationRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hour: int = Field(ge=0, description="0-based row index of this observation")
    var: str = Field(description="clinical variable, e.g. HR, Lactate, O2Sat")
    value: float | None = Field(description="the measurement, or None if unmeasured that hour")


class FeaturesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient_id: str
    observations: list[ObservationRow]
    age: float | None = None
    gender: int | None = None  # Challenge convention: 0 male, 1 female
    unit1: float | None = None
    unit2: float | None = None
    hosp_adm_time: float = 0.0
    iculos: int | None = Field(
        default=None, description="ICU hour of the row; defaults to last hour + 1"
    )


class FeaturesResponse(BaseModel):
    features: dict[str, float | None]
    hour: int


class PredictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    features: dict[str, float | None]


class ShapContribution(BaseModel):
    feature: str
    value: float


class PredictResponse(BaseModel):
    risk: float
    threshold: float
    indicator: str  # info | warning | critical
    top_shap: list[ShapContribution]


_MODEL: lgb.Booster | None = None
_THRESHOLD: float = 0.5
_WINDOWS: tuple[int, ...] = (6, 12, 24)
_COLUMNS: list[str] = []


def _threshold_path(config) -> Path:
    """results/threshold.json, or THRESHOLD_PATH when testing."""
    override = os.environ.get("THRESHOLD_PATH")
    return Path(override) if override else config.path("threshold")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _MODEL, _THRESHOLD, _WINDOWS, _COLUMNS
    config = load_config(os.environ.get("CONFIG_PATH"))
    model_file = os.environ.get("MODEL_FILE", "window_a.txt")
    model_path = Path(os.environ.get("MODEL_PATH") or (config.path("models") / model_file))
    if not model_path.is_file():
        raise RuntimeError(f"missing model artifact {model_path}; run `make train` first")

    threshold_json = json.loads(_threshold_path(config).read_text(encoding="utf-8"))
    decision = os.environ.get("THRESHOLD_DECISION", "A")

    _MODEL = lgb.Booster(model_file=str(model_path))
    _THRESHOLD = float(threshold_json[decision]["threshold"])
    _WINDOWS = tuple(config.windows)
    _COLUMNS = window_feature_columns(_WINDOWS)
    yield


app = FastAPI(
    title="sepsis-risk model service",
    description="Scores hourly sepsis risk on the Phase-3 feature set (research prototype).",
    version="0.1.0",
    lifespan=_lifespan,
)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "sepsis-risk model", "ok": True}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    # Liveness probe for the deployed edge (compose healthcheck, §22).
    return {"status": "ok"}


@app.post("/features", response_model=FeaturesResponse)
def build_features(request: FeaturesRequest) -> FeaturesResponse:
    if not request.observations:
        raise HTTPException(status_code=422, detail="observations must not be empty")
    if request.iculos is not None and request.iculos < 1:
        raise HTTPException(status_code=422, detail="iculos must be >= 1")

    hours = sorted({o.hour for o in request.observations})
    if hours != list(range(hours[0], hours[0] + len(hours))) or hours[0] != 0:
        message = "observations must cover every hour contiguously from hour 0"
        raise HTTPException(status_code=422, detail=message)

    by_var = {var: [None] * len(hours) for var in CLINICAL_VARIABLES}
    for obs in request.observations:
        if obs.var not in by_var:
            raise HTTPException(status_code=422, detail=f"unknown variable {obs.var!r}")
        by_var[obs.var][obs.hour - hours[0]] = obs.value

    n = len(hours)
    last_hour = hours[-1]
    frame = pl.DataFrame(
        {
            "patient_id": [request.patient_id] * n,
            "site": ["S"] * n,  # serving rows are not part of any training site split
            "hour": hours,
            **by_var,
            "Age": [request.age if request.age is not None else float("nan")] * n,
            "Gender": [request.gender if request.gender is not None else float("nan")] * n,
            "Unit1": [request.unit1 if request.unit1 is not None else float("nan")] * n,
            "Unit2": [request.unit2 if request.unit2 is not None else float("nan")] * n,
            "HospAdmTime": [request.hosp_adm_time] * n,
            "ICULOS": [float(request.iculos if request.iculos is not None else last_hour + 1)] * n,
            "SepsisLabel": [0] * n,
        }
    )

    features_frame = window_features_frame(frame, _WINDOWS)
    last_row = features_frame.select(_COLUMNS).row(-1)
    features = {
        name: (None if value != value else float(value))
        for name, value in zip(_COLUMNS, last_row, strict=True)
    }
    return FeaturesResponse(features=features, hour=last_hour)


@app.post("/predict", response_model=PredictResponse)
def predict(request: PredictRequest) -> PredictResponse:
    missing = [c for c in _COLUMNS if request.features.get(c) is None]
    if not _COLUMNS or _MODEL is None:
        raise HTTPException(status_code=503, detail="model not loaded")
    if missing:
        message = (
            f"missing {len(missing)} feature values (e.g. {missing[0]!r}) — feed /features output"
        )
        raise HTTPException(status_code=422, detail=message)

    x = np.array(
        [[request.features[c] if request.features[c] is not None else np.nan for c in _COLUMNS]],
        dtype=np.float32,
    )
    proba = float(np.asarray(_MODEL.predict(x))[0])
    explainer = shap.TreeExplainer(_MODEL)
    arr = np.asarray(explainer.shap_values(x))
    # Binary: shap may return one (1, n) array or a (2, 1, n) class list.
    risk_shift = arr[1][0] if arr.ndim == 3 else arr[0] if arr.shape[0] == 1 else arr
    risk_shift = np.asarray(risk_shift).reshape(-1)
    order = np.argsort(-np.abs(risk_shift))[:10]
    top_shap = [{"feature": _COLUMNS[i], "value": round(float(risk_shift[i]), 6)} for i in order]

    indicator = _indicator(proba, _THRESHOLD)
    return PredictResponse(risk=proba, threshold=_THRESHOLD, indicator=indicator, top_shap=top_shap)


def _indicator(risk: float, threshold: float) -> str:
    if risk >= threshold * 4 / 3:
        return "critical"
    if risk >= threshold:
        return "warning"
    return "info"

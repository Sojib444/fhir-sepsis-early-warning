"""Loading of config.yaml and the column vocabulary of the Challenge 2019 files.

Every knob that can change a reported number lives in config.yaml, not here.
This module only reads it and pins the column names, which are a property of
the dataset rather than a choice.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# --- Column vocabulary (AGENTS.md §5) ---------------------------------------
# Order matters: it is the on-disk column order of every .psv file, and we
# verify each file against it rather than trusting it.

VITALS: tuple[str, ...] = (
    "HR",
    "O2Sat",
    "Temp",
    "SBP",
    "MAP",
    "DBP",
    "Resp",
    "EtCO2",
)

LABS: tuple[str, ...] = (
    "BaseExcess",
    "HCO3",
    "FiO2",
    "pH",
    "PaCO2",
    "SaO2",
    "AST",
    "BUN",
    "Alkalinephos",
    "Calcium",
    "Chloride",
    "Creatinine",
    "Bilirubin_direct",
    "Glucose",
    "Lactate",
    "Magnesium",
    "Phosphate",
    "Potassium",
    "Bilirubin_total",
    "TroponinI",
    "Hct",
    "Hgb",
    "PTT",
    "WBC",
    "Fibrinogen",
    "Platelets",
)

DEMOGRAPHICS: tuple[str, ...] = (
    "Age",
    "Gender",
    "Unit1",
    "Unit2",
    "HospAdmTime",
    "ICULOS",
)

LABEL = "SepsisLabel"

#: The 40 feature columns, in file order.
FEATURE_COLUMNS: tuple[str, ...] = VITALS + LABS + DEMOGRAPHICS

#: The 41 columns of a .psv file, in file order.
PSV_COLUMNS: tuple[str, ...] = FEATURE_COLUMNS + (LABEL,)

#: Clinical variables that are measured over time. Missingness indicators and
#: window statistics are built for these; demographics are constant per stay.
CLINICAL_VARIABLES: tuple[str, ...] = VITALS + LABS

#: Columns added by our parser, not present in the raw files.
META_COLUMNS: tuple[str, ...] = ("patient_id", "site", "hour")


# --- config.yaml ------------------------------------------------------------


def repo_root() -> Path:
    """Repository root, resolved from this file's location."""
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Config:
    """Parsed config.yaml, with paths resolved against the repository root."""

    raw: dict[str, Any]
    root: Path

    @property
    def seed(self) -> int:
        return int(self.raw["seed"])

    @property
    def sites(self) -> dict[str, str]:
        """Site letter -> raw directory name, e.g. {'A': 'training_setA'}."""
        return dict(self.raw["data"]["sites"])

    @property
    def min_icu_hours(self) -> int:
        """D3: patients with fewer hourly records than this are excluded."""
        return int(self.raw["data"]["min_icu_hours"])

    @property
    def train_fraction(self) -> float:
        return float(self.raw["splits"]["train_fraction"])

    @property
    def site_b_train_fraction(self) -> float:
        """The patient-fraction of site B carved off for the §9 training rows."""
        return float(self.raw["splits"]["site_b_train_fraction"])

    @property
    def threshold_sweep_step(self) -> float:
        """D5: granularity of the utility-max threshold sweep on site A."""
        return float(self.raw["threshold"]["sweep_step"])

    @property
    def windows(self) -> list[int]:
        """D4: feature window sizes in hours."""
        return list(self.raw["features"]["windows"])

    def path(self, key: str) -> Path:
        """Resolve a `paths:` entry against the repository root."""
        return self.root / self.raw["paths"][key]


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Load config.yaml. Defaults to the copy at the repository root."""
    root = repo_root()
    cfg_path = Path(path) if path is not None else root / "config.yaml"
    with open(cfg_path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return Config(raw=raw, root=root)

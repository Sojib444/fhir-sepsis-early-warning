"""Generate tests/fixtures/mini_cohort/ — a small, hand-constructed cohort.

Why this exists (AGENTS-ENGINEERING.md §18.3): CI must run the whole pipeline
without downloading 40,336 real patient files, and no real patient record may
enter the repository. These patients are constructed from an explicit spec
below, not sampled from the real data, so they carry no licensing question.

The spec covers every case in the §19 Tier 3 table. Each `PatientSpec` names
the case it exists to exercise; if you add a case to §19, add it here.

Run:  uv run python scripts/make_fixtures.py
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from sepsis.config import CLINICAL_VARIABLES, PSV_COLUMNS

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "mini_cohort"

#: Deterministic — the fixtures are committed, so regenerating them must not
#: produce a diff unless the spec changed.
SEED = 20190801

#: Plausible resting values, used as the centre of each variable's wander.
#: These are round numbers chosen by hand, not statistics of the real data.
BASELINES: dict[str, float] = {
    "HR": 82.0,
    "O2Sat": 97.0,
    "Temp": 37.0,
    "SBP": 120.0,
    "MAP": 82.0,
    "DBP": 65.0,
    "Resp": 17.0,
    "EtCO2": 33.0,
    "BaseExcess": 0.0,
    "HCO3": 24.0,
    "FiO2": 0.5,
    "pH": 7.38,
    "PaCO2": 40.0,
    "SaO2": 97.0,
    "AST": 30.0,
    "BUN": 16.0,
    "Alkalinephos": 75.0,
    "Calcium": 9.2,
    "Chloride": 104.0,
    "Creatinine": 1.0,
    "Bilirubin_direct": 0.3,
    "Glucose": 120.0,
    "Lactate": 1.4,
    "Magnesium": 2.0,
    "Phosphate": 3.5,
    "Potassium": 4.1,
    "Bilirubin_total": 0.8,
    "TroponinI": 0.05,
    "Hct": 34.0,
    "Hgb": 11.5,
    "PTT": 32.0,
    "WBC": 9.0,
    "Fibrinogen": 300.0,
    "Platelets": 220.0,
}

#: How often each variable is recorded. Vitals are near-continuous, labs are
#: sparse — the same qualitative pattern as the real data, which is what the
#: missingness machinery has to cope with.
MEASURE_EVERY: dict[str, int] = {var: 1 for var in BASELINES}
MEASURE_EVERY.update(
    {
        "EtCO2": 6,
        "BaseExcess": 8,
        "HCO3": 8,
        "FiO2": 6,
        "pH": 8,
        "PaCO2": 8,
        "SaO2": 4,
        "AST": 24,
        "BUN": 12,
        "Alkalinephos": 24,
        "Calcium": 12,
        "Chloride": 12,
        "Creatinine": 12,
        "Bilirubin_direct": 24,
        "Glucose": 4,
        "Lactate": 8,
        "Magnesium": 12,
        "Phosphate": 12,
        "Potassium": 8,
        "Bilirubin_total": 24,
        "TroponinI": 24,
        "Hct": 8,
        "Hgb": 8,
        "PTT": 12,
        "WBC": 12,
        "Fibrinogen": 24,
        "Platelets": 12,
    }
)


@dataclass(frozen=True)
class PatientSpec:
    """One synthetic patient, and the §19 case it exists to cover."""

    patient_id: str
    site: str
    n_hours: int
    case: str
    #: Hour at which SepsisLabel first becomes 1. None = never septic.
    onset_hour: int | None = None
    age: float = 62.0
    gender: int = 1
    unit1: float | None = 1.0
    unit2: float | None = 0.0
    hosp_adm_time: float = -12.0
    #: Variables that are never recorded for this patient.
    never_measured: tuple[str, ...] = ()
    #: Variables recorded only in the final hour.
    last_hour_only: tuple[str, ...] = ()
    #: All 26 labs null for the whole stay.
    no_labs: bool = False
    #: Write the rows to file in reverse ICULOS order.
    shuffle_rows: bool = False
    #: First ICULOS value. 37% of real records begin part-way into the stay,
    #: so `hour` (a row index) is not the ICU clock. See docs/data_notes.md.
    iculos_start: int = 1
    #: {variable: (hour, value)} — deliberate data-entry errors, for D8.
    implausible: dict[str, tuple[int, float]] = field(default_factory=dict)


SPECS: tuple[PatientSpec, ...] = (
    # --- site A -------------------------------------------------------------
    PatientSpec("p000001", "A", 48, "ordinary non-septic stay"),
    PatientSpec("p000002", "A", 36, "septic, onset mid-stay", onset_hour=20),
    PatientSpec("p000003", "A", 8, "exactly 8 hours — D3 boundary, included"),
    PatientSpec("p000004", "A", 7, "7 hours — excluded by D3"),
    PatientSpec("p000005", "A", 1, "1 hour — excluded by D3"),
    PatientSpec("p000006", "A", 24, "septic from hour 0", onset_hour=0),
    PatientSpec(
        "p000007",
        "A",
        30,
        "Lactate never measured",
        never_measured=("Lactate", "Fibrinogen", "TroponinI"),
    ),
    PatientSpec(
        "p000008",
        "A",
        30,
        "Bilirubin_total measured only in the final hour",
        last_hour_only=("Bilirubin_total",),
    ),
    PatientSpec("p000009", "A", 20, "all labs null, vitals present", no_labs=True),
    PatientSpec("p000010", "A", 18, "rows written out of order", shuffle_rows=True),
    PatientSpec(
        "p000011",
        "A",
        26,
        "negative HospAdmTime — long ward stay before ICU",
        hosp_adm_time=-240.5,
    ),
    PatientSpec(
        "p000012",
        "A",
        28,
        "physiologically impossible values — D8",
        implausible={"HR": (5, 300.0), "Temp": (9, 0.0), "SBP": (12, -5.0)},
    ),
    PatientSpec(
        "p000013",
        "A",
        72,
        "long stay, septic late, record starts at ICULOS 5",
        onset_hour=60,
        iculos_start=5,
    ),
    PatientSpec("p000014", "A", 12, "short stay, no Unit recorded", unit1=None, unit2=None),
    PatientSpec("p000015", "A", 40, "young patient", age=24.0, gender=0),
    PatientSpec("p000016", "A", 44, "elderly patient, septic", age=91.0, onset_hour=30),
    # --- site B (held out) --------------------------------------------------
    PatientSpec("p100001", "B", 40, "ordinary non-septic stay"),
    PatientSpec("p100002", "B", 34, "septic, onset mid-stay", onset_hour=18),
    PatientSpec("p100003", "B", 8, "exactly 8 hours — D3 boundary, included"),
    PatientSpec("p100004", "B", 6, "6 hours — excluded by D3"),
    PatientSpec(
        "p100005",
        "B",
        52,
        "sparser labs than site A — the shift this project measures",
        never_measured=("Lactate", "PTT", "Fibrinogen", "TroponinI", "Bilirubin_direct"),
    ),
    PatientSpec("p100006", "B", 22, "septic from hour 0", onset_hour=0),
)


def _series_for(spec: PatientSpec, variable: str, rng: np.random.Generator) -> list[float | None]:
    """Build one variable's column: a slow wander, sampled on its own cadence."""
    if variable in spec.never_measured:
        return [None] * spec.n_hours
    if spec.no_labs and variable not in ("HR", "O2Sat", "Temp", "SBP", "MAP", "DBP", "Resp"):
        return [None] * spec.n_hours

    baseline = BASELINES[variable]
    cadence = MEASURE_EVERY[variable]
    scale = max(abs(baseline) * 0.04, 0.02)
    offset = rng.integers(0, cadence) if cadence > 1 else 0

    # Physiological caps, so that a long stay's random walk cannot wander into
    # values no patient could have. p000012 overrides these deliberately: it is
    # the fixture for D8, and its impossible values are applied after the walk.
    caps: dict[str, tuple[float, float]] = {
        "O2Sat": (70.0, 100.0),
        "SaO2": (70.0, 100.0),
        "Temp": (34.0, 41.0),
        "pH": (7.0, 7.6),
        "FiO2": (0.21, 1.0),
    }

    values: list[float | None] = []
    drift = 0.0
    for hour in range(spec.n_hours):
        # Mean-reverting rather than a free random walk: without the decay, a
        # 72-hour stay drifts arbitrarily far from the baseline.
        drift = 0.85 * drift + float(rng.normal(0.0, scale * 0.3))
        # Septic patients deteriorate: the fixtures need *some* signal so that a
        # pipeline smoke test is not fitting pure noise.
        sick = 0.0
        if spec.onset_hour is not None and hour >= max(spec.onset_hour - 6, 0):
            direction = {"HR": 1.0, "Resp": 1.0, "Temp": 1.0, "Lactate": 1.0, "WBC": 1.0}.get(
                variable, -0.3 if variable in ("SBP", "MAP", "DBP", "Platelets") else 0.0
            )
            sick = direction * scale * 3.0 * (hour - max(spec.onset_hour - 6, 0) + 1) / 6.0

        measured = (hour % cadence == offset) if cadence > 1 else True
        if variable in spec.last_hour_only:
            measured = hour == spec.n_hours - 1

        value = baseline + drift + sick + float(rng.normal(0.0, scale))
        if variable in caps:
            low, high = caps[variable]
            value = min(max(value, low), high)
        values.append(round(value, 2) if measured else None)

    for variable_name, (hour, bad_value) in spec.implausible.items():
        if variable_name == variable and 0 <= hour < spec.n_hours:
            values[hour] = bad_value

    return values


def _format(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "NaN"
    return f"{value:g}"


def build_rows(spec: PatientSpec, rng: np.random.Generator) -> list[list[str]]:
    """Render one patient as .psv rows, in ICULOS order."""
    columns = {var: _series_for(spec, var, rng) for var in CLINICAL_VARIABLES}

    rows: list[list[str]] = []
    for hour in range(spec.n_hours):
        label = 0
        if spec.onset_hour is not None and hour >= spec.onset_hour:
            label = 1

        record: dict[str, float | None] = {var: columns[var][hour] for var in CLINICAL_VARIABLES}
        record["Age"] = spec.age
        record["Gender"] = float(spec.gender)
        record["Unit1"] = spec.unit1
        record["Unit2"] = spec.unit2
        record["HospAdmTime"] = spec.hosp_adm_time
        record["ICULOS"] = float(spec.iculos_start + hour)
        record["SepsisLabel"] = float(label)

        rows.append([_format(record[col]) for col in PSV_COLUMNS])
    return rows


def write_fixtures(root: Path = FIXTURE_ROOT) -> list[Path]:
    """Write every spec to root/training_set{A,B}/p*.psv. Returns the paths."""
    written: list[Path] = []
    header = "|".join(PSV_COLUMNS)

    for index, spec in enumerate(SPECS):
        rng = np.random.default_rng(SEED + index)
        rows = build_rows(spec, rng)
        if spec.shuffle_rows:
            rows = list(reversed(rows))

        site_dir = root / f"training_set{spec.site}"
        site_dir.mkdir(parents=True, exist_ok=True)
        path = site_dir / f"{spec.patient_id}.psv"
        path.write_text(
            header + "\n" + "\n".join("|".join(row) for row in rows) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        written.append(path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=FIXTURE_ROOT)
    args = parser.parse_args()

    paths = write_fixtures(args.out)
    print(f"wrote {len(paths)} fixture patients to {args.out}")
    for spec in SPECS:
        print(f"  {spec.site} {spec.patient_id}  {spec.n_hours:3d}h  {spec.case}")


if __name__ == "__main__":
    main()

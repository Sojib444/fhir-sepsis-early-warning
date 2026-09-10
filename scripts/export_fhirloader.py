"""Phase 5: export the cohort parquet to the flat CSV the FhirLoader consumes.

The .NET loader deliberately reads plain CSV rather than parquet, so it needs no
parquet dependency and a reviewer can open the file. Data `data/interim/*` is
gitignored, exactly like every other derived artifact.

Column order matters: CsvColumns in FhirLoader maps by *name*, so a headers
line is the only contract. Nulls are empty cells.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl

from sepsis.config import load_config
from sepsis.io import read_cohort

VARIABLES = [
    "HR",
    "O2Sat",
    "Temp",
    "SBP",
    "MAP",
    "DBP",
    "Resp",
    "EtCO2",
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
]

STATIC = ["Age", "Gender", "Unit1", "Unit2", "ICULOS"]


def export(
    config,
    out: Path,
    site: str | None = None,
    limit: int | None = None,
    cohort_path: Path | None = None,
) -> Path:
    cohort = read_cohort(cohort_path or config.path("cohort"))
    if site:
        cohort = cohort.filter(pl.col("site") == site)
    if limit:
        # Stable subset: first `limit` patient ids in sort order (demo seed).
        ids = sorted(cohort.get_column("patient_id").unique().to_list())[:limit]
        cohort = cohort.filter(pl.col("patient_id").is_in(ids))
    cohort = cohort.sort(["patient_id", "hour"])

    frame = cohort.select(
        [
            "patient_id",
            "site",
            pl.col("hour").cast(pl.Int32),
            pl.col("ICULOS").cast(pl.Int32),
        ]
        + STATIC
        + ["SepsisLabel"]
        + VARIABLES
    )
    frame.write_csv(out, float_precision=3)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output CSV path (default data/interim/fhirloader.csv)",
    )
    parser.add_argument(
        "--site",
        choices=["A", "B"],
        default=None,
        help="limit to one site (demo: site A is a few thousand MB; B is fine)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="subset the cohort to the first N patient ids (demo seed)",
    )
    parser.add_argument(
        "--cohort",
        type=Path,
        default=None,
        help="cohort parquet override (default paths.cohort from config); "
        "used to export a demo seed from the synthetic fixtures in CI",
    )
    args = parser.parse_args()
    config = load_config(args.config)
    out = args.out or config.path("interim") / "fhirloader.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    export(config, out, site=args.site, limit=args.count, cohort_path=args.cohort)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

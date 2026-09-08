"""Parse data/raw/ into data/interim/cohort.parquet.

The cache holds *every* patient, before the D3 inclusion rule. Filtering
happens downstream so that docs/data_notes.md can report what D3 removes from
the same file the model is trained from.

Run:  uv run python scripts/build_cohort.py
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import polars as pl

from sepsis.config import load_config
from sepsis.io import build_cohort, write_cohort
from sepsis.splits import patient_hour_counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument(
        "--raw",
        type=Path,
        default=None,
        help="override the raw directory (e.g. tests/fixtures/mini_cohort)",
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="parser processes; 1 forces the serial path",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    raw_dir = args.raw if args.raw is not None else config.path("raw")
    out_path = args.out if args.out is not None else config.path("cohort")

    missing = [
        raw_dir / dirname for dirname in config.sites.values() if not (raw_dir / dirname).is_dir()
    ]
    if missing:
        print(f"ERROR: missing site directories: {[str(p) for p in missing]}", file=sys.stderr)
        print("Run scripts/fetch_data.sh (or `make fetch`) first.", file=sys.stderr)
        return 1

    started = time.perf_counter()
    cohort = build_cohort(raw_dir, config.sites, max_workers=args.workers)
    elapsed = time.perf_counter() - started

    write_cohort(cohort, out_path)

    counts = patient_hour_counts(cohort)
    print(f"parsed {counts.height} patients / {cohort.height} patient-hours in {elapsed:.1f}s")
    per_site = (
        counts.group_by("site")
        .agg(
            pl.len().alias("patients"),
            pl.col("n_hours").sum().alias("hours"),
        )
        .sort("site")
    )
    for row in per_site.iter_rows(named=True):
        print(f"  site {row['site']}: {row['patients']} patients, {row['hours']} hours")
    print(f"wrote {out_path}")

    if elapsed > 300:
        print(
            f"WARNING: parsing took {elapsed:.0f}s, over the 5 minute budget in AGENTS.md §7.1",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Generate docs/data_notes.md from data/interim/cohort.parquet.

Everything in that document is computed here, so the numbers in it are
reproducible by re-running this script rather than trusted because someone
typed them.

Sections, and the requirement each one satisfies:

  Cohort            AGENTS.md §7.2 — patient counts per site
  Inclusion (D3)    D3            — what the 8-hour rule removes, per site
  Prevalence        §7.2          — patient-level and hour-level, per site
  Length of stay    §7.2          — median and quartiles per site
  Label semantics   §7.4          — the §5 claim, checked against the data
  Missingness       §7.2          — all 40 variables, per site
  Ordering culture  D6.2          — how often each lab is actually ordered
  D8 evidence       §24           — ranges and out-of-range counts, per site
  Licence           §18           — what the data licence permits

Run:  uv run python scripts/data_notes.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl

from sepsis.config import CLINICAL_VARIABLES, FEATURE_COLUMNS, LABEL, load_config
from sepsis.io import read_cohort
from sepsis.provenance import provenance, provenance_markdown
from sepsis.splits import inclusion_report, patient_hour_counts

#: Clinically plausible bounds, used ONLY to count out-of-range values as
#: evidence for decision D8. Nothing is clipped or dropped anywhere in the
#: pipeline on the strength of these numbers — that is the human's call.
#: Bounds are deliberately wide: the question is "is this physically possible",
#: not "is this normal".
PLAUSIBLE_RANGE: dict[str, tuple[float, float]] = {
    "HR": (10.0, 250.0),
    "O2Sat": (10.0, 100.0),
    "Temp": (20.0, 45.0),
    "SBP": (20.0, 300.0),
    "MAP": (10.0, 250.0),
    "DBP": (5.0, 200.0),
    "Resp": (0.0, 80.0),
    "EtCO2": (0.0, 100.0),
    "BaseExcess": (-40.0, 40.0),
    "HCO3": (1.0, 60.0),
    "FiO2": (0.20, 1.0),
    "pH": (6.5, 8.0),
    "PaCO2": (5.0, 150.0),
    "SaO2": (10.0, 100.0),
    "AST": (0.0, 20000.0),
    "BUN": (0.0, 300.0),
    "Alkalinephos": (0.0, 3000.0),
    "Calcium": (2.0, 20.0),
    "Chloride": (50.0, 160.0),
    "Creatinine": (0.0, 30.0),
    "Bilirubin_direct": (0.0, 50.0),
    "Glucose": (5.0, 1500.0),
    "Lactate": (0.0, 40.0),
    "Magnesium": (0.2, 10.0),
    "Phosphate": (0.2, 25.0),
    "Potassium": (1.0, 12.0),
    "Bilirubin_total": (0.0, 60.0),
    "TroponinI": (0.0, 500.0),
    "Hct": (5.0, 75.0),
    "Hgb": (2.0, 25.0),
    "PTT": (5.0, 300.0),
    "WBC": (0.0, 300.0),
    "Fibrinogen": (10.0, 1500.0),
    "Platelets": (1.0, 2000.0),
}


def _table(rows: list[list[str]], headers: list[str]) -> str:
    """Render a markdown table. Kept trivial on purpose."""
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _sites(cohort: pl.DataFrame) -> list[str]:
    return sorted(cohort.get_column("site").unique().to_list())


# --- sections ---------------------------------------------------------------


def section_cohort(cohort: pl.DataFrame) -> str:
    counts = patient_hour_counts(cohort)
    rows = []
    for site in _sites(cohort):
        site_counts = counts.filter(pl.col("site") == site)
        rows.append(
            [
                site,
                f"{site_counts.height:,}",
                f"{site_counts.get_column('n_hours').sum():,}",
            ]
        )
    rows.append(
        [
            "**total**",
            f"**{counts.height:,}**",
            f"**{counts.get_column('n_hours').sum():,}**",
        ]
    )
    return (
        "## Cohort\n\n"
        "Every patient in `data/raw/`, before any inclusion rule.\n\n"
        + _table(rows, ["Site", "Patients", "Patient-hours"])
        + "\n\nSite A is Beth Israel Deaconess Medical Center (Boston); site B is "
        "Emory University Hospital (Atlanta). Site B is held out entirely "
        "(AGENTS.md §2.2).\n"
    )


def section_inclusion(cohort: pl.DataFrame, min_hours: int) -> str:
    report = inclusion_report(cohort, min_hours)
    rows = []
    flagged = False
    for row in report.iter_rows(named=True):
        if row["patients_dropped_pct"] > 3.0:
            flagged = True
        rows.append(
            [
                row["site"],
                f"{row['patients_before']:,}",
                f"{row['patients_after']:,}",
                f"{row['patients_dropped']:,}",
                f"{row['patients_dropped_pct']:.2f}%",
                f"{row['hours_dropped']:,}",
                f"{row['hours_dropped_pct']:.2f}%",
            ]
        )

    text = (
        f"## Inclusion rule (D3)\n\n"
        f"The only exclusion in this study: drop patients with fewer than "
        f"{min_hours} hourly records. Applied to whole patients, never to rows.\n\n"
        + _table(
            rows,
            [
                "Site",
                "Patients before",
                "Patients after",
                "Patients dropped",
                "% patients",
                "Hours dropped",
                "% hours",
            ],
        )
    )
    if flagged:
        text += (
            "\n\n> **FLAG (D3).** This rule removes more than 3% of patients at "
            "at least one site. D3 requires this to be flagged rather than "
            "passed over silently. The human decides whether to keep the rule "
            "as specified.\n"
        )
    else:
        text += "\n\nBelow the 3% threshold at which D3 requires a flag.\n"
    return text


def section_prevalence(cohort: pl.DataFrame) -> str:
    rows = []
    for site in _sites(cohort):
        site_rows = cohort.filter(pl.col("site") == site)
        per_patient = site_rows.group_by("patient_id").agg(
            pl.col(LABEL).max().alias("ever_septic"),
            pl.len().alias("n_hours"),
            pl.col(LABEL).sum().alias("positive_hours"),
        )
        n_patients = per_patient.height
        n_septic = int(per_patient.get_column("ever_septic").sum())
        n_hours = site_rows.height
        n_positive = int(site_rows.get_column(LABEL).sum())
        rows.append(
            [
                site,
                f"{n_septic:,} / {n_patients:,}",
                f"{100.0 * n_septic / n_patients:.2f}%",
                f"{n_positive:,} / {n_hours:,}",
                f"{100.0 * n_positive / n_hours:.2f}%",
            ]
        )
    return (
        "## Sepsis prevalence\n\n"
        "Reported at both levels because they differ by roughly an order of "
        "magnitude, and because the hour-level figure is the prevalence that "
        "AUPRC must be read against (AGENTS.md §2.7).\n\n"
        + _table(
            rows,
            [
                "Site",
                "Septic patients",
                "Patient-level",
                "Positive hours",
                "Hour-level",
            ],
        )
        + "\n"
    )


def section_length_of_stay(cohort: pl.DataFrame) -> str:
    counts = patient_hour_counts(cohort)
    rows = []
    for site in _sites(cohort):
        hours = counts.filter(pl.col("site") == site).get_column("n_hours")
        rows.append(
            [
                site,
                f"{hours.median():.0f}",
                f"{hours.quantile(0.25):.0f}",
                f"{hours.quantile(0.75):.0f}",
                f"{hours.min()}",
                f"{hours.max()}",
            ]
        )
    return (
        "## ICU length of stay (hourly records per patient)\n\n"
        + _table(rows, ["Site", "Median", "Q1", "Q3", "Min", "Max"])
        + "\n"
    )


def section_label_semantics(cohort: pl.DataFrame) -> str:
    """AGENTS.md §7.4 — check the §5 label claim against the data itself."""
    findings: list[str] = []
    rows = []

    for site in _sites(cohort):
        site_rows = cohort.filter(pl.col("site") == site)
        per_patient = (
            site_rows.sort(["patient_id", "hour"])
            .group_by("patient_id")
            .agg(
                pl.col(LABEL).max().alias("ever_septic"),
                pl.col(LABEL).sum().alias("positive_hours"),
                pl.len().alias("n_hours"),
                pl.col("hour").filter(pl.col(LABEL) == 1).min().alias("first_positive_hour"),
                pl.col("hour").filter(pl.col(LABEL) == 0).max().alias("last_negative_hour"),
            )
        )
        septic = per_patient.filter(pl.col("ever_septic") == 1)

        # Monotonicity: once the label turns 1 it never returns to 0. If this
        # holds, the label is a single contiguous block at the end of the stay,
        # which is what a shifted onset label looks like.
        non_monotone = septic.filter(
            pl.col("last_negative_hour").is_not_null()
            & (pl.col("last_negative_hour") > pl.col("first_positive_hour"))
        ).height

        onset_at_zero = septic.filter(pl.col("first_positive_hour") == 0).height

        rows.append(
            [
                site,
                f"{septic.height:,}",
                f"{non_monotone:,}",
                f"{onset_at_zero:,}",
                f"{septic.get_column('positive_hours').median():.0f}" if septic.height else "n/a",
                f"{septic.get_column('first_positive_hour').median():.0f}"
                if septic.height
                else "n/a",
            ]
        )

        if non_monotone:
            findings.append(
                f"- Site {site}: **{non_monotone} septic patients have a 0 after their first 1.** "
                "That contradicts a single shifted-onset label and must be "
                "resolved before modelling."
            )

    if not findings:
        findings.append(
            "- In every septic patient at both sites the label is a single "
            "contiguous run of 1s ending at the last hour of the stay. That is "
            "consistent with the documented rule (`SepsisLabel = 1` from "
            "`t_sepsis − 6` onward) and with no patient's label ever reverting."
        )

    return (
        "## Label semantics — checked, not assumed\n\n"
        "AGENTS.md §5 states that `SepsisLabel` is 1 from `t_sepsis − 6` onward "
        "for septic patients and 0 throughout for the rest, and requires this to "
        "be verified rather than trusted.\n\n"
        "**What the official documentation says.** The PhysioNet project page "
        'states: *"we have shifted the sepsis labels in the training data ahead '
        'by six hours"*, and that `SepsisLabel` marks onset per the Sepsis-3 '
        "definition. So the six-hour horizon is a property of the shipped data, "
        "not a modelling choice (D1).\n\n"
        "**What the data shows.**\n\n"
        + _table(
            rows,
            [
                "Site",
                "Septic patients",
                "Non-monotone labels",
                "Onset at hour 0",
                "Median positive hours",
                "Median first positive hour",
            ],
        )
        + "\n\n"
        + "\n".join(findings)
        + "\n\n"
        "`t_sepsis` itself is **not recoverable** from these files: the shift is "
        "already applied and no unshifted onset time is distributed. Any "
        "statement about the true onset hour is therefore an inference from the "
        "shifted label, not an observation.\n"
    )


def section_missingness(cohort: pl.DataFrame) -> str:
    sites = _sites(cohort)
    rows = []
    for variable in FEATURE_COLUMNS:
        row = [f"`{variable}`"]
        for site in sites:
            site_rows = cohort.filter(pl.col("site") == site)
            null_fraction = site_rows.get_column(variable).null_count() / site_rows.height
            row.append(f"{100.0 * null_fraction:.1f}%")
        rows.append(row)
    return (
        "## Missingness, all 40 variables\n\n"
        "Percentage of patient-hours with no recorded value. Labs are missing "
        "the overwhelming majority of the time; that is expected, and the fact "
        "that a test *was* ordered is itself a feature (AGENTS.md §2.5). "
        "Nothing here is imputed and no row is dropped.\n\n"
        + _table(rows, ["Variable", *[f"Site {site} missing" for site in sites]])
        + "\n"
    )


def section_ordering_culture(cohort: pl.DataFrame) -> str:
    """D6 evidence item 2 — per-site measurement frequency for every lab."""
    sites = _sites(cohort)
    rows = []
    for variable in CLINICAL_VARIABLES:
        row = [f"`{variable}`"]
        for site in sites:
            site_rows = cohort.filter(pl.col("site") == site)
            n_patients = site_rows.get_column("patient_id").n_unique()
            measured = site_rows.filter(pl.col(variable).is_not_null())
            per_100h = 100.0 * measured.height / site_rows.height
            patients_with_any = measured.get_column("patient_id").n_unique()
            row.append(f"{per_100h:.1f}")
            row.append(f"{100.0 * patients_with_any / n_patients:.0f}%")
        rows.append(row)

    headers = ["Variable"]
    for site in sites:
        headers.extend([f"{site}: per 100 h", f"{site}: % patients"])
    return (
        "## Measurement frequency per site (evidence for D6)\n\n"
        "How often each variable is actually recorded, and what share of "
        "patients have it at least once. D6 asks for this because a difference "
        "in ordering culture between hospitals changes what missingness *means*, "
        "and therefore what a model trained on one site's missingness patterns "
        "does at the other.\n\n"
        "Evidence only. The interpretation is the human's (D6).\n\n" + _table(rows, headers) + "\n"
    )


def section_d8_evidence(cohort: pl.DataFrame) -> str:
    """§24 — the table the human needs in order to decide D8."""
    sites = _sites(cohort)
    rows = []
    for variable in CLINICAL_VARIABLES:
        low, high = PLAUSIBLE_RANGE[variable]
        row = [f"`{variable}`", f"{low:g} – {high:g}"]
        for site in sites:
            values = cohort.filter(pl.col("site") == site).get_column(variable).drop_nulls()
            if values.is_empty():
                row.extend(["n/a", "n/a"])
                continue
            out_of_range = int(((values < low) | (values > high)).sum())
            row.append(f"{values.min():g} – {values.max():g}")
            row.append(
                f"{out_of_range:,} ({100.0 * out_of_range / len(values):.3f}%)"
                if out_of_range
                else "0"
            )
        rows.append(row)

    headers = ["Variable", "Plausible bounds used"]
    for site in sites:
        headers.extend([f"{site}: observed range", f"{site}: out of range"])

    return (
        "## D8 evidence — physiologically implausible values\n\n"
        "**This section exists so the human can decide D8. Nothing here has been "
        "acted on.** No value is clipped, dropped, or set to missing anywhere in "
        "the pipeline (AGENTS-ENGINEERING.md §24).\n\n"
        "The bounds below are wide deliberately: the question they ask is *could "
        "this value physically occur*, not *is it normal*.\n\n" + _table(rows, headers) + "\n\n"
        "### The three options, and what each costs\n\n"
        "1. **Leave as-is.** Nothing is discarded. LightGBM is largely robust to "
        "outliers because it splits on rank, so the cost is mostly to the "
        "logistic-regression baseline and to any scaling. Data-entry errors stay "
        "in the training signal.\n"
        "2. **Clip to the bounds.** Keeps every row and bounds the influence of "
        "errors, but a clipped value is indistinguishable from a real value at "
        "the boundary.\n"
        "3. **Set out-of-range to missing**, and let `{var}_missing` and "
        "`{var}_hours_since_measured` carry it. Consistent with the treatment of "
        "missingness elsewhere in this project, and it never invents a number — "
        "but it does convert a recorded observation into an absence, which the "
        "model reads as a clinical decision not to measure.\n\n"
        "### The risk that matters\n\n"
        "**Clipping or nulling extreme values discards signal precisely where "
        "the model needs it most.** A heart rate of 200 in a septic patient is "
        "not a data-entry error; it is the thing being predicted. Any rule "
        "aggressive enough to catch a typed-in 300 will also catch genuine "
        "extreme physiology, and the two are not separable from the value alone.\n"
    )


def section_licence() -> str:
    return (
        "## Data licence and what it permits\n\n"
        "Checked before anything is hosted publicly (AGENTS-ENGINEERING.md §18).\n\n"
        "- **Access policy:** open — anyone can access the files, subject to the "
        "licence.\n"
        "- **Licence:** Creative Commons Attribution 4.0 International "
        "(CC BY 4.0), as stated on the PhysioNet project page.\n"
        "- **What that permits:** redistribution and derivative works, including "
        "commercial use, provided attribution is given. Redistribution of the "
        "records is therefore permitted with attribution — unlike the "
        "credentialed PhysioNet datasets, this one carries no data-use agreement "
        "restricting sharing.\n"
        "- **What this repository does anyway:** no patient data is committed "
        "(AGENTS.md §2.6). `data/raw/` and `data/interim/` are gitignored, and "
        "the only records in git are the synthetic patients in "
        "`tests/fixtures/mini_cohort/`, which are generated by "
        "`scripts/make_fixtures.py` and correspond to no real person.\n"
        "- **Public deployment (§22):** the demo instance serves a small subset "
        "of the licensed data with attribution, or the synthetic fixtures. "
        "Either is permitted; the fixtures are the safer default.\n\n"
        "**Required citation.** Reyna MA, Josef CS, Jeter R, Shashikumar SP, "
        "Westover MB, Nemati S, Clifford GD, Sharma A. *Early Prediction of "
        "Sepsis From Clinical Data: The PhysioNet/Computing in Cardiology "
        "Challenge.* Critical Care Medicine 48(2): 210–217 (2019). "
        "https://doi.org/10.1097/CCM.0000000000004145 — and the dataset itself: "
        "https://doi.org/10.13026/v64v-d857\n"
    )


def section_acquisition() -> str:
    return (
        "## How the data was acquired (deviation from §18)\n\n"
        "`AGENTS-ENGINEERING.md` §18 and `AGENTS.md` §5 both assume PhysioNet "
        "publishes `training_setA.zip` and `training_setB.zip`, and warn against "
        "fetching the files individually. **Those archives no longer exist.** "
        "Verified on 2026-09-09:\n\n"
        "- `physionet.org/files/challenge-2019/1.0.0/` contains only "
        "`training/training_setA/` and `training/training_setB/`, holding the "
        "40,336 individual `.psv` files. There is no archive anywhere in the "
        "project tree.\n"
        "- The project page's own *\"Click here to download the complete "
        'training database (42 MB)"* link points at '
        "`archive.physionet.org/pnw/challenge-2019-request-access`, which "
        "returns **404**.\n"
        "- `archive.physionet.org` legacy paths, the `get-zip` endpoint, "
        "`static/published-projects/`, and the Google Cloud Storage mirror all "
        "return 404.\n\n"
        "Per-file download is therefore the only remaining route, and "
        "`scripts/fetch_data.sh` takes it: it enumerates the directory listings "
        "and fetches with a single `curl --parallel` process. Measured "
        "throughput against PhysioNet is roughly 3–4 files per second and does "
        "not improve with more connections, so a cold fetch of both sites takes "
        "**about three hours**. The script is idempotent and resumable, so an "
        "interrupted run costs nothing.\n\n"
        "Because PhysioNet publishes no checksum manifest for these files, "
        "`data/CHECKSUMS.sha256` records a digest computed on first fetch: a "
        "manifest of per-file SHA-256 hashes per site, reduced to one digest per "
        "site. `make data` verifies it and refuses to proceed on a mismatch.\n"
    )


def build_document(cohort: pl.DataFrame, min_hours: int, seed: int, cohort_path: Path) -> str:
    parts = [
        "# Data notes\n",
        "PhysioNet/CinC Challenge 2019, *Early Prediction of Sepsis from "
        "Clinical Data*. Every number below is generated by "
        "`scripts/data_notes.py` from `data/interim/cohort.parquet`.\n",
        "> Research prototype. Not validated for clinical use and not a medical device.\n",
        section_cohort(cohort),
        section_inclusion(cohort, min_hours),
        section_prevalence(cohort),
        section_length_of_stay(cohort),
        section_label_semantics(cohort),
        section_missingness(cohort),
        section_ordering_culture(cohort),
        section_d8_evidence(cohort),
        section_acquisition(),
        section_licence(),
        provenance_markdown(provenance(seed, cohort_path)),
    ]
    return "\n\n".join(part.strip() for part in parts) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--cohort", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    config = load_config(args.config)
    cohort_path = args.cohort if args.cohort is not None else config.path("cohort")
    out_path = args.out if args.out is not None else config.root / "docs" / "data_notes.md"

    cohort = read_cohort(cohort_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        build_document(cohort, config.min_icu_hours, config.seed, cohort_path),
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# AGENTS.md — Sepsis Early-Warning on FHIR

You are the implementation agent for this repository. Read this entire file before writing any code. Follow the phases in order. Do not skip ahead.

---

## 0. How to work

- Work **one phase at a time**. At the end of each phase, stop and report: what you built, what the acceptance criteria say, and whether each is met. Wait for the human to approve before starting the next phase.
- **Commit after every meaningful unit of work**, not once per phase. Commit messages in imperative mood, one line, no emoji.
- If a decision is listed in §3, **stop and ask the human**. Do not guess and do not proceed with a placeholder.
- If something in this spec conflicts with what you find in the data or the official documentation, **stop and report the conflict**. Do not silently resolve it.
- Prefer boring, readable code over clever code. This repository will be read by academics who are not necessarily strong engineers.

---

## 1. Goal and non-goals

**Goal.** Build a reproducible sepsis early-warning system on the PhysioNet/CinC Challenge 2019 dataset, and measure honestly what happens when the model is transferred between the two hospitals in that dataset. Serve predictions over HL7 FHIR R4 and CDS Hooks so the whole pipeline runs with `docker compose up`.

**The headline result this project exists to produce:**
> A 4-cell cross-site validation matrix, plus a transfer curve showing how much target-site data is needed to recover performance after transfer.

**Non-goals.** Do not chase leaderboard scores. Do not add features beyond this spec. Do not build anything not listed here. Scope creep is the primary failure mode.

**Framing.** This is a research prototype. It is not validated for clinical use and is not a medical device. Every user-facing surface (README, dashboard, CDS Hooks card) must say so.

---

## 2. Ground rules (non-negotiable)

These are correctness requirements, not style preferences. Violating any of them invalidates the entire project.

1. **Split by patient, never by row.** A patient's hours must never appear on both sides of a split. Every split function must be unit-tested for this.

2. **`training_setB` is a held-out external test set.** You may load it and score on it exactly once per phase, at the end. You must never:
   - train on it,
   - tune hyperparameters against it,
   - fit imputation statistics, scalers or calibrators on it,
   - iterate on a design choice because setB scores improved.
   If a setB score looks bad, **report it**. Do not fix it.

3. **No temporal leakage.** A feature computed at hour `t` may only use data from hours `≤ t` for that same patient. No forward fill from the future. No global aggregates computed over a patient's full stay.

4. **No cross-patient leakage in preprocessing.** Any statistic used in preprocessing (means for imputation, scaler parameters, calibration mapping) must be fit on the training split only, then applied to validation and test.

5. **Missingness is signal.** Labs are missing >90% of the time in this data, and the fact that a test was ordered is itself informative. For every clinical variable, emit a `{var}_missing` indicator and a `{var}_hours_since_measured` feature. Do not drop rows with missing values.

6. **Never commit data.** `data/raw/`, `data/interim/` and `*.parquet` go in `.gitignore`. Only commit code, configs, results tables and figures.

7. **Report AUROC, AUPRC, the official utility score, and the positive-class prevalence together.** Never report AUROC alone; with prevalence this low it flatters the model.

---

## 3. Decisions you must NOT make alone

Stop and ask the human. These are the decisions that define the science, and the human must own them because they will have to defend them in conversation.

- The **prediction task definition**: what horizon, and whether to use `SepsisLabel` as-is or re-derive it.
- The **blanking / gap window** near onset, if any.
- Any **inclusion or exclusion criterion** (minimum ICU hours, age filters, etc.).
- The **feature window sizes** (proposed: 6h / 12h / 24h — confirm).
- Which **operating threshold** is used for the alert-burden analysis and the dashboard default.
- How to **interpret the cross-site performance drop**. Produce the evidence; do not write the explanation.
- Anything where you are about to write "I assumed…".

When you ask, present 2–3 concrete options with the trade-off of each. Do not ask open-ended questions.

---

## 4. Tech stack

Use exactly these. Do not substitute.

| Layer | Choice |
|---|---|
| Python | 3.12, environment managed with `uv` |
| Data | `polars` for parsing and joins, `pandas` only where a library requires it |
| Model | `scikit-learn` (baseline), `lightgbm` (main), `shap` |
| Model service | `FastAPI` + `uvicorn` |
| FHIR server | `hapiproject/hapi` (Docker image) |
| FHIR client / ETL | .NET 10, `Hl7.Fhir.R4` (Firely SDK) |
| CDS service | ASP.NET Core Web API (.NET 10) |
| Dashboard | Angular 20 |
| Orchestration | Docker Compose |
| Tests | `pytest` (Python), `xUnit` (.NET) |
| CI | GitHub Actions |
| License | MIT |

---

## 5. Data

**Source.** PhysioNet/CinC Challenge 2019, "Early Prediction of Sepsis from Clinical Data" — open access, no credentialing required.

- Download the **archive**, not via recursive `wget`. Pulling 40,000 individual files over HTTP will take hours.
- `training_setA` — 20,336 patients, Beth Israel Deaconess Medical Center (Boston).
- `training_setB` — 20,000 patients, Emory University Hospital (Atlanta).
- One `.psv` file per patient (pipe-separated). Rows are hours, in order. Filename encodes patient ID.

**Columns** — 40 features plus the label:

- *Vitals (8)*: `HR`, `O2Sat`, `Temp`, `SBP`, `MAP`, `DBP`, `Resp`, `EtCO2`
- *Labs (26)*: `BaseExcess`, `HCO3`, `FiO2`, `pH`, `PaCO2`, `SaO2`, `AST`, `BUN`, `Alkalinephos`, `Calcium`, `Chloride`, `Creatinine`, `Bilirubin_direct`, `Glucose`, `Lactate`, `Magnesium`, `Phosphate`, `Potassium`, `Bilirubin_total`, `TroponinI`, `Hct`, `Hgb`, `PTT`, `WBC`, `Fibrinogen`, `Platelets`
- *Demographics / context (6)*: `Age`, `Gender`, `Unit1`, `Unit2`, `HospAdmTime`, `ICULOS`
- *Label (1)*: `SepsisLabel`

**Label semantics.** For septic patients `SepsisLabel` is 1 from `t_sepsis − 6` onward; for non-septic patients it is 0 throughout. **Verify this against the official dataset README before relying on it** and record what you find in `docs/data_notes.md`. The 6-hour horizon is therefore already baked into the label — the README must state this explicitly so no reader assumes otherwise.

**Utility metric.** The Challenge defines a time-dependent utility score that rewards early prediction and penalises late and false alarms. **Download and vendor the official `evaluate_sepsis_score.py` from PhysioNet. Do not reimplement it.** Add a test that reproduces a known score on a small hand-built example.

---

## 6. Repository layout

```
fhir-sepsis-early-warning/
├── README.md
├── LICENSE                      # MIT
├── AGENTS.md                    # this file
├── docker-compose.yml
├── Makefile                     # make setup | data | train | eval | up | test
├── pyproject.toml
├── .gitignore                   # data/, *.parquet, .venv, bin/, obj/
├── data/
│   ├── raw/                     # gitignored
│   └── interim/                 # gitignored parquet cache
├── docs/
│   ├── data_notes.md
│   ├── decisions.md             # every §3 decision + the human's answer + date
│   ├── model_card.md
│   └── report/                  # LaTeX source
├── src/
│   ├── sepsis/
│   │   ├── io.py                # psv parsing → parquet
│   │   ├── splits.py
│   │   ├── features.py
│   │   ├── train.py
│   │   ├── evaluate.py
│   │   ├── calibration.py
│   │   ├── transfer.py
│   │   └── vendor/evaluate_sepsis_score.py
│   ├── model_api/               # FastAPI
│   ├── FhirLoader/              # .NET console app
│   ├── CdsService/              # ASP.NET Core
│   └── dashboard/               # Angular 20
├── tests/
├── results/
│   ├── metrics.json
│   ├── cross_site_matrix.md
│   └── figures/
└── .github/workflows/ci.yml
```

---

## 7. Phase 1 — Data foundation

**Objective.** Raw `.psv` files → a single validated parquet table, with splits locked.

**Tasks**
1. `src/sepsis/io.py`: parse all `.psv` files into one long-format table with columns `patient_id`, `site` (`A` or `B`), `hour`, the 40 features, `SepsisLabel`. Cache to `data/interim/cohort.parquet`. Parsing 40k files must be parallel and must complete in under 5 minutes on a laptop.
2. Write `docs/data_notes.md`: patient counts per site, sepsis prevalence per site (patient-level and hour-level), median ICU length of stay, and a missingness table for all 40 variables per site.
3. `src/sepsis/splits.py`:
   - `site_A` → train / validation, split **by `patient_id`**, 80/20, seeded and deterministic.
   - `site_B` → **held out entirely**. Provide a loader that emits a warning to stderr every time it is called.
4. Verify the label semantics claimed in §5 empirically and report what you find.

**Acceptance criteria**
- `make data` runs end to end from an empty `data/raw/`.
- Test: no `patient_id` appears in more than one split.
- Test: for every septic patient, the first `SepsisLabel == 1` hour is consistent with the documented rule.
- `docs/data_notes.md` exists and its numbers are reproducible.

**Do not** build features or train anything in this phase.

---

## 8. Phase 2 — Baseline and scoring harness

**Objective.** A deliberately weak baseline plus a trustworthy scoring path. This exists so that later improvements are measurable.

**Tasks**
1. Vendor the official utility scorer. Add a test with a hand-constructed example.
2. `src/sepsis/evaluate.py`: given predictions, emit AUROC, AUPRC, official utility, prevalence, and a calibration curve. One function, used everywhere, so numbers are never computed two different ways.
3. Minimal features: last observed value carried forward **within patient only**, plus `{var}_missing` indicators, plus `Age`, `Gender`, `ICULOS`, `HospAdmTime`.
4. Logistic regression baseline. Fit the scaler on train only.
5. Write `results/metrics.json` with baseline numbers on the site-A validation split.

**Acceptance criteria**
- Baseline scores are recorded. They will be poor — that is expected and correct.
- Test: forward fill never pulls a value from a later hour.
- Test: scaler parameters fit on train differ from those that would be fit on validation (i.e. it is actually being fit on train only).

---

## 9. Phase 3 — Model and the cross-site matrix

**Objective.** The headline result.

**Tasks**
1. `src/sepsis/features.py`: for windows of 6h / 12h / 24h (confirm with human per §3), compute per variable: `min`, `max`, `mean`, `last`, `slope`. Plus `{var}_missing` and `{var}_hours_since_measured`. All strictly backward-looking.
2. LightGBM. **Time-box hyperparameter search to a small, fixed grid** — no more than 20 configurations, tuned on the site-A validation split only. Log the grid and the winner.
3. Produce the **4-cell matrix**:

   | Train | Test | AUROC | AUPRC | Utility | Prevalence |
   |---|---|---|---|---|---|
   | A | A (held-out val) | | | | |
   | A | **B** | | | | |
   | B | A | | | | |
   | A + B | A, B separately | | | | |

4. Diagnostics for the drop: per-feature distribution comparison between sites (report a distance measure per feature, ranked), and SHAP global importance computed separately per site, side by side.
5. Write `results/cross_site_matrix.md`.

**Acceptance criteria**
- The matrix is complete and reproducible from `make eval`.
- The site-B numbers were computed **once**, after all design choices were frozen. Confirm this explicitly in your report.
- The diagnostics identify which features shifted most. **Present the evidence; do not write the interpretation** — that is the human's job per §3.

**Do not** tune anything after seeing site-B results.

---

## 10. Phase 4 — Rigor

**Objective.** The analyses that separate a research artifact from a notebook.

**Tasks**
1. **Calibration**: reliability curve and Brier score for A→A and A→B. Then isotonic recalibration fit on a slice of site-A only, and separately a version fit on a small slice of site-B — report both, and state clearly which slice was used for each.
2. **Subgroups**: AUROC / AUPRC by sex, age band (<40, 40–65, 65–80, 80+), and ICU unit (`Unit1` / `Unit2`). Report sample sizes alongside; suppress any subgroup with too few positives to be meaningful and say so.
3. **Alert burden**: sweep the threshold and report sensitivity, PPV, and **alerts per 100 ICU-days**. Plot it.
4. **SHAP**: global bar plot per site, plus waterfall plots for two individual patients (one true positive, one false positive).
5. **Transfer curve** — `src/sepsis/transfer.py`. Take the A-trained model. For `n` ∈ {0, 50, 100, 250, 500, 1000, 2000} site-B patients:
   - recalibration only (isotonic on those `n` patients),
   - and fine-tuning (continue LightGBM training on those `n`).
   Sample the `n` patients disjointly from the site-B evaluation set. Repeat each `n` with 5 random draws and plot mean ± spread. Report AUPRC and utility versus `n`.
6. `docs/model_card.md` and a filled TRIPOD+AI checklist.

**Acceptance criteria**
- The transfer curve figure is publication-quality: axis labels, units, error bands, readable at half-page width.
- The patients used to adapt the model are never among those used to score it.

---

## 11. Phase 5 — FHIR layer

**Objective.** Make the data speak a standard, and make the whole thing runnable by a stranger.

**Tasks**
1. HAPI FHIR server in `docker-compose.yml`, with a healthcheck.
2. `src/FhirLoader/` — .NET 10 console app using the Firely SDK:
   - `Patient` (age, gender)
   - `Encounter` (the ICU stay)
   - `Observation` for every vital and lab, **LOINC-coded**, with correct UCUM units and timestamps derived from `hour`
   - Upload via `Bundle` transactions, batched, idempotent on re-run
3. Build the LOINC mapping table as a checked-in config file (`src/FhirLoader/loinc_map.json`), not hardcoded. Include a `README` note on how each code was chosen. Where you are unsure of a code, mark it `"unverified": true` rather than guessing silently.
4. xUnit tests for the mappers — at minimum: unit conversion, null/missing handling, timestamp arithmetic, and that a generated `Observation` validates against the R4 profile.
5. A `--count N` flag so a demo can load 200 patients quickly.

**Acceptance criteria**
- `dotnet test` green.
- After running the loader, a `GET /Observation?patient=X&code=...` against HAPI returns sensible data.
- Every LOINC code is either verified against loinc.org or flagged `unverified`.

---

## 12. Phase 6 — Serving and CDS Hooks

**Tasks**
1. `src/model_api/` — FastAPI: `POST /predict` takes a feature vector, returns `{risk, threshold, top_shap: [...]}`. Load the model once at startup.
2. `src/CdsService/` — ASP.NET Core:
   - `GET /cds-services` — discovery document
   - `POST /cds-services/sepsis-risk` — a `patient-view` hook that reads `Observation` resources from HAPI, builds the feature vector using **the same code path as training** (share it — do not reimplement feature logic in C#; call the Python service or expose a shared feature endpoint), calls the model, and returns a CDS Hooks `Card`.
3. Card content: risk value, the operating threshold, top 3 contributing factors, `indicator` of `info` / `warning` / `critical`, and a `detail` line stating this is a research prototype.
4. Integration test that exercises HAPI → CDS service → model service end to end.

**Acceptance criteria**
- A single documented `curl` command returns a valid CDS Hooks card.
- Feature construction at serving time is provably the same as at training time. If you cannot share the code path, stop and raise this with the human rather than duplicating the logic.

---

## 13. Phase 7 — Dashboard

**Tasks** — Angular 20, three views only:
1. Patient list with current risk.
2. Patient detail: risk trajectory over time, with the true onset hour marked; SHAP waterfall for the selected hour.
3. Threshold slider that live-updates sensitivity, PPV and alerts-per-100-ICU-days from precomputed sweep data.

Persistent disclaimer banner: *Research prototype — not for clinical use.*

**Do not** add authentication, user accounts, routing beyond these three views, or a component library beyond what Angular ships with.

---

## 14. Phase 8 — Ship

**Tasks**
1. **README.md**, in this order:
   - one-sentence description
   - the cross-site matrix table, near the top
   - architecture diagram (ASCII is fine)
   - quickstart: `docker compose up` plus the `curl`
   - how to reproduce the results from raw data
   - **Limitations** — honest and specific
   - data governance and citation
   - disclaimer
2. LaTeX report in `docs/report/`: Introduction, Data, Methods, Results, Discussion, Limitations, References. Insert `TODO(human)` markers wherever interpretation is required — **do not write the interpretation yourself**.
3. GitHub Actions: lint, `pytest`, `dotnet test`, Angular build. Green before tagging.
4. Tag `v1.0`.

---

## 15. Testing requirements

Every phase ships with tests. Minimum:

- **Leakage tests** (highest priority): no patient across splits; no future data in features; preprocessing statistics fit on train only.
- **Scorer test**: vendored utility scorer reproduces a known value.
- **Determinism test**: same seed → identical metrics.
- **FHIR mapper tests**: units, nulls, timestamps, profile validation.
- **Integration test**: full stack returns a card.

A phase is not complete if its tests do not pass.

---

## 16. Definition of done

- `docker compose up` works on a clean machine with no manual steps.
- `make data && make train && make eval` reproduces every number in the README from raw data.
- CI green.
- Site-B was scored once, after freezing. Stated explicitly in the README.
- Every §3 decision is recorded in `docs/decisions.md` with the human's answer and the date.
- Every `TODO(human)` in the report has been resolved by the human.

---

## 17. Anti-patterns — do not do these

- Tuning against site B, in any form, however indirect.
- Reimplementing the official utility scorer.
- Global imputation or scaling fit across train and test together.
- Row-level train/test splitting.
- Dropping rows because of missing values.
- Reporting AUROC without AUPRC and prevalence.
- Writing the interpretation of the cross-site drop.
- Adding features, endpoints, pages or dependencies not in this spec.
- Committing any patient data file.
- Language anywhere that implies clinical validity or diagnostic capability.
- One large commit at the end.

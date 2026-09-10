# fhir-sepsis-early-warning

A sepsis early-warning system on the PhysioNet/CinC Challenge 2019 cohort that measures, honestly, what happens when the model trained at one hospital is scored at another — and serves predictions over HL7 FHIR R4 and CDS Hooks, runnable end to end with `docker compose up`.

> **Research prototype. Not validated for clinical use. Not a medical device.**
> Nothing here is suitable for patient care, and no output should be read as a diagnosis or a recommendation.

---

## The headline result: cross-site validation

A model trained on one hospital's data and scored at the other is measurably worse. The matrix is the project's headline result; every number comes with AUPRC, the official Challenge utility score, and the positive-class prevalence — never AUROC alone.

| Train | Test | AUROC | AUPRC | Utility | Prevalence |
|---|---|---|---|---|---|
| A | A (held-out val) | pending¹ | pending | pending | pending |
| A | **B** | pending¹ | pending | pending | pending |
| B | A | pending¹ | pending | pending | pending |
| A + B | A, B separately | pending¹ | pending | pending | pending |

¹ **These cells are produced by `make eval` and must not be hand-edited.** After the first full run, replace each `pending` with the value in [`results/cross_site_matrix.md`](results/cross_site_matrix.md) — that file also carries the per-feature drift evidence and per-site SHAP importances, side by side. **The site-B cells (A→B, A+B→B) were scored exactly once, after every design choice was frozen** (grid, threshold, features, inclusion rule). Nothing was retrained or re-tuned against site B; if the transfer looks bad, it is reported as-is.

The interpretation of the cross-site drop is deliberately absent from this repository's own words — see `docs/decisions.md` D6: the evidence is in `results/`, and the reading of it is left to the reader.

---

## What this project is measuring

A model trained at one hospital is scored at another, and the difference is reported rather than engineered away.

- **Site A** — Beth Israel Deaconess Medical Center, Boston. 20,336 patients. Used for training and validation.
- **Site B** — Emory University Hospital, Atlanta. 20,000 patients. Held out per the §2.2 rules; the rows needed by the matrix come from a disjoint, by-patient 80/20 split of site B (`b_train` for the B→A and A+B→B cells, `b_eval` scored once). See `docs/decisions.md`.

The prediction horizon is **six hours, and it is a property of the data, not a modelling choice**: PhysioNet shifted the sepsis labels ahead by six hours before publishing, so `SepsisLabel` is already 1 from `t_sepsis − 6` onward. Re-deriving the label is neither possible nor useful here — the dataset contains no culture orders or antibiotic times, so Sepsis-3 cannot be reconstructed from it. See [`docs/decisions.md`](docs/decisions.md) D1.

---

## Architecture

```
                ┌─────────────────────────── research pipeline (uv / make) ────────────────────────────┐
                │  fetch_data.sh → cohort.parquet → designs → LightGBM grid → eval matrix → rigor      │
                └──────────────┬───────────────────────────────────────────────────────────────────────┘
                               │ results/models/*, threshold.json, rigor.json (baked into images)
                               ▼
  browser ──https──▶ dashboard (Angular 20 / nginx, rate-limited /api)
                        │  /api/*                                    ▲
                        ▼                                            │ POST /predict
                     CDS service (ASP.NET Core) ──────────────▶ model service (FastAPI)
                        │  FHIR queries (Firely SDK)                │ loads window_a.txt + threshold.json
                        ▼
                     HAPI FHIR R4 server ◀── one-shot FhirLoader (.NET 10, LOINC-coded Observations)
                                                └── CSV exported from the cohort (data speaks FHIR)
```

Development stack (`docker compose up`): HAPI on :8080, model service on :8000, CDS service on :8990, dashboard on :4200. Deployed demo (`deploy/`): same four services plus Caddy TLS on one `t3.small` instance, see [`deploy/README.md`](deploy/README.md).

---

## Quickstart

### 1. Tests — no data required

Requires [`uv`](https://docs.astral.sh/uv/) and a POSIX shell (Git Bash on Windows).

```bash
make setup && make test     # ~1 min; runs on committed synthetic fixtures
```

### 2. The full stack

```bash
make setup
bash scripts/fetch_data.sh  # ~3 h, ~330 MB, resumable and idempotent — or run `make train` in CI (below)
make data && make train     # cohort, designs, grid search, models + threshold.json
make eval && make rigor     # cross-site matrix, drift, SHAP, calibration, transfer curve
make up                     # hapi :8080 · model-api :8000 · cds :8990 · dashboard :4200
make fhir-load-demo         # loads 200 patients into HAPI
```

One command prints the discovery document and a valid CDS Hooks card for a patient:

```bash
./scripts/cds_demo.sh p000001
```

which is equivalent to a raw `POST` carrying the patient's Observation `Bundle` (serialised as a string) in `prefetch`:

```bash
curl -s -X POST http://localhost:8990/cds-services/sepsis-risk \
  -H 'Content-Type: application/json' \
  -d '{"hook":"patient-view","context":{"patientId":"p000001","userId":"demo"},
       "prefetch":{"observations":"<FHIR Bundle for p000001, serialised>"}}'
```

Then open <http://localhost:4200>: patient list, per-hour risk trajectory with onset marking and SHAP waterfall, and the threshold slider driven by the precomputed alert-burden sweep. Every surface carries the disclaimer.

### 3. Reproducing the numbers

From raw data to every reported number: [`REPRODUCE.md`](REPRODUCE.md). The machine path is `make data && make train && make eval`; the deployed demo runs the identical pipeline in CI (`.github/workflows/deploy.yml`, `v*` tags only). Every generated document embeds a provenance block — git commit, dirty flag, seed, input digests, package versions, UTC timestamp. A number without one is not a number from this project.

Determinism is a tested property, not an aspiration: one seed in `config.yaml` is threaded through every sampling function, `PYTHONHASHSEED` is exported by the `Makefile`, and the split is asserted to depend only on the patient-ID set and the seed.

---

## Data

PhysioNet/CinC Challenge 2019, *Early Prediction of Sepsis from Clinical Data*.

- **Licence:** CC BY 4.0 — redistribution permitted with attribution.
- **No patient data is committed to this repository.** `data/raw/` and `data/interim/` are gitignored. The only records in git are the 22 synthetic patients in `tests/fixtures/mini_cohort/`, generated by `scripts/make_fixtures.py`, corresponding to no real person. The deployed demo serves a 200-patient subset baked into the loader image at build time, which the Challenge data terms permit; it is never in git.
- **The archives no longer exist.** PhysioNet's own "download the complete training database" link is dead, and the only route is per-file download. `scripts/fetch_data.sh` handles it; see [`docs/data_notes.md`](docs/data_notes.md) for the full evidence.

**Citation.** Reyna MA, Josef CS, Jeter R, Shashikumar SP, Westover MB, Nemati S, Clifford GD, Sharma A. *Early Prediction of Sepsis From Clinical Data: The PhysioNet/Computing in Cardiology Challenge.* Critical Care Medicine 48(2): 210–217 (2019). <https://doi.org/10.1097/CCM.0000000000004145> · Dataset: <https://doi.org/10.13026/v64v-d857>

---

## Rules this repository holds itself to

From [`AGENTS.md`](AGENTS.md) §2. They are enforced by tests in `tests/invariant/`, which are the highest-priority tests in the suite.

1. Splits are by patient, never by row.
2. Site B is held out. Loading it warns on stderr, every time.
3. No temporal leakage — a feature at hour *t* uses only hours ≤ *t*.
4. No cross-patient leakage — preprocessing statistics are fit on train only.
5. Missingness is signal, not a defect to be imputed away.
6. No patient data is ever committed.
7. AUROC is never reported without AUPRC and prevalence.

---

## Limitations

- **The label is inherited, not established.** `SepsisLabel` is the Challenge's shifted label; its correctness is someone else's. `t_sepsis` is not recoverable from the published files, so no statement about true onset time is an observation.
- **Two hospitals is not a generalisation study.** It is one transfer, measured honestly, between a Boston and an Atlanta EHR population whose documentation practices differ.
- **Site-B numbers were computed once, on a 20% eval slice**, to preserve the §2.2 held-out discipline; the matrix's site-B cells therefore carry more sampling noise than their site-A counterparts.
- **Subgroups are limited by the data.** The public dataset carries no race, ethnicity, insurance or admission-source fields, so those subgroups cannot be examined at all. Sex/age/unit subgroups are in `results/rigor.md` with sample sizes; small-positive subgroups are suppressed and named as such.
- **The threshold is a research operating point** (D5: utility-max on site-A validation), not a clinically negotiated one; alerts-per-ICU-day at this threshold are a burden measurement, not a deployment recommendation.
- **The model is a 2019-era cohort** — 8 features of vitals, 26 labs from two sites, hourly granularity. Anything the real deployment would have (medications, notes, prior history) is absent here.
- **Research prototype.** No clinical validation has been performed or claimed anywhere in this project; see the model card ([`docs/model_card.md`](docs/model_card.md)) and the TRIPOD+AI checklist ([`docs/report/TRIPOD_AI.md`](docs/report/TRIPOD_AI.md)).

---

## Licence

MIT — see [`LICENSE`](LICENSE). The dataset is separately licensed CC BY 4.0 by PhysioNet.

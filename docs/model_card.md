# Model card — sepsis early-warning on FHIR

Research prototype. Not validated for clinical use. Not a medical device.

---

## The model

| | |
|---|---|
| Task | Hourly sepsis early-warning on the ICU stay; positive class = hour where `SepsisLabel == 1` (sepsis onset − 6 h onward, as defined by the Challenge) |
| Classifier | LightGBM (gradient boosting), trained on per-hour feature rows |
| Features | Backward-looking window statistics (6 h / 12 h / 24 h): min, max, mean, last, slope; per-variable `{var}_missing` and `{var}_hours_since_measured`; plus `Age`, `Gender`, `ICULOS`, `HospAdmTime` (see `docs/data_notes.md` for missingness) |
| Training data | Site-A hospital, 2019 Challenge `training_setA`, split by patient 80/20 train/validation. Site B held out; adaptation data for the transfer study drawn from the site-B *training* slice after phase-freeze (recorded in `docs/decisions.md`) |
| Operating threshold | D5: utility-maximising threshold on site-A validation (§ `docs/decisions.md`), frozen before any site-B scoring |
| Reported metrics | AUROC, AUPRC, official Challenge utility score, positive-class prevalence — always together (AGENTS.md §2.7) |

## Intended use

- Reproducible research on cross-hospital transfer of an early-warning model.
- Studying how much target-site data (recalibration vs. fine-tuning) is needed to
  recover a transferred model's AUPRC and utility.
- Serving **simulated** predictions as FHIR Observations and CDS Hooks cards in a
  dockerised demo stack.

**Unintended uses.** Clinical deployment, treatment decisions, safety-critical
alerting, or any decision about a real patient. The label is the Challenge's
proxy definition; the data is one type of ICU documentation; models are
transferred across hospitals without clinical validation.

## Known limitations

- The 6-hour horizon is baked into `SepsisLabel`; the model does not learn an
  onset horizon of its own.
- Missingness is treated as signal; small shifts in ordering habits between
  hospitals become distribution shift.
- Site B is a *different hospital*, and the headline study is how far the A
  model drops there — the deployment target is the *opposite* of the training
  hospital.
- Positive-class prevalence is low; AUPRC and utility are the honest numbers,
  AUROC flatters.
- Operationally unvalidated: no alert-fatigue trial, no workflow integration.

## Fairness

| Attribute | Subgroup AUROC / AUPRC compared on site-A validation |
|---|---|
| Sex | see `results/rigor.md` §Subgroups |
| Age band | <40, 40–65, 65–80, 80+ |
| ICU unit | `Unit1`, `Unit2` |

Subgroups with fewer than 50 positive hours are suppressed and reported as such
(`scripts/eval_rigor.py`). Sample sizes are reported alongside every metric.

## Model selection and fitting

- Hyperparameters: small fixed grid (18 configs, `config.yaml` → lightgbm),
  selected on site-A validation AUPRC only. `results/hyperparameter_search.json`.
- Seed `20190801`; `PYTHONHASHSEED=0` (Makefile) for determinism.
- Preprocessing statistics (scaler for the Phase-2 baseline; isotonic
  recalibration for Phase 4) are fit on training slices and applied to
  validation/test — never fit across or on test.

## Scores

All numbers below are filled from `results/` by Phase 8; until then this file
is a template with live links.

| Cell | AUROC | AUPRC | Utility (thr) | Prevalence |
|---|---|---|---|---|
| A→A | `results/metrics.json` | | | |
| A→B | `results/metrics.json` | | | |
| B→A | `results/metrics.json` | | | |
| A+B→A | `results/metrics.json` | | | |
| A+B→B | `results/metrics.json` | | | |

Transfer: `results/rigor.json` → `transfer`; figure `results/figures/transfer_curve.png`.

## TRIPOD+AI flag

`docs/report/TRIPOD_AI.md` states which of the TRIPOD+AI reporting items are
**reported**, which are **not applicable** (research prototype, no patient-level
deployment, no registered protocol), and which are **flagged `TODO(human)`** for
the Phase-8 report.

## Provenance & versioning

- Data: PhysioNet/CinC Challenge 2019, open access, no credentialing.
- Vendor scorer: `src/sepsis/vendor/evaluate_sepsis_score.py` (official, vendored,
  tested — never reimplemented).
- Cohort checksum and split provenance: `results/metrics.json` and
  `scripts/build_cohort.py`.
- git commit of the model artifacts is recorded at eval time
  (`provenance()` in `src/sepsis/`).
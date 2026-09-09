# TRIPOD+AI self-assessment — Sepsis early-warning on FHIR

Research prototype, not for clinical use. This file is a living checklist for
the Phase-8 report. Status per item:

- **reported** — the item's evidence exists in this repository,
- **n/a** — not applicable to a research prototype (stated why),
- **TODO(human)** — the evidence requires the human's judgement; do not auto-fill.

| Item | Status | Where |
|---|---|---|
| Title identifies development/validation, target population | reported | README; docs/model_card.md |
| Abstract structured summary | TODO(human) | docs/report/abstract |
| Rationale: clinical problem + intended use | reported | model_card.md Intended use |
| Objective defined with target population | reported | model_card.md |
| Data source(s), data provenance, study period | reported | AGENTS.md §5; docs/data_notes.md |
| Eligibility criteria & exclusions (D3 etc.) | reported | docs/decisions.md |
| Outcome definition & measurement | reported | docs/data_notes.md label semantics |
| Prediction horizon | reported | docs/decisions.md (D1), model_card |
| Sample size rationale | TODO(human) | report Methods |
| Missing data handling | reported | missingness-as-signal; docs/data_notes.md |
| Predictors: how measured, when in stay | reported | src/sepsis/features.py; model_card |
| Input type (static/dynamic/continuous) declared | reported | model_card features table |
| Data fed at prediction time is shown on a timeline (no future data) | reported | leakage tests; docs/decisions.md (D4) |
| Model development: modelling approach, hyperparameters | reported | config.yaml; results/hyperparameter_search.json |
| Model selection rationale | reported | AGENTS.md §9.2 |
| Preprocessing fit only on development data | reported | Phase-2 test; AGENTS.md §2.4 |
| Performance measures primary: AUROC, AUPRC, utility, prevalence | reported | scripts/evaluate.py; results/* |
| Calibration assessment | reported | scripts/eval_rigor.py; results/rigor.md |
| Subgroup analyses | reported | results/rigor.md §Subgroups |
| Transferability measured, interpretation left to human | reported | results/cross_site_matrix.md; results/transfer_curve.png |
| Uncertainty/CI around performance | TODO(human) | transfer curve error bands exist; main-matrix CIs to add |
| Test set independence & site-B once-scored discipline | reported | docs/decisions.md; scripts/eval_matrix.py fingerprint |
| Model card | reported | docs/model_card.md |
| Interpretability (SHAP global + local) | reported | results/shap_*.png |
| Limitations stated | reported | model_card Limitations; README (Phase 8) |
| Registered protocol / ethical approval | n/a | research prototype on open-access de-identified data |
| Data availability | reported | PhysioNet download instructions in README |
| Code & environment availability | reported | Makefile, docker-compose.yml, CI |
| Funding/conflicts | TODO(human) | report |

## TODO(human) items

1. Abstract and confidence intervals.
2. Sample-size rationale paragraph for the transfer study.
3. Funding/conflicts statement.
4. Final regulatory-position sentence (research / non-device framing) as drafted
   but confirmed by the authors.
# docs/decisions.md

Decisions referred to in `AGENTS.md` §3. The agent must not change anything here.
If the agent believes a decision is wrong or unworkable, it must **stop and say so** rather than deviate.

Each entry: the decision, the reasoning, and what was rejected. The reasoning matters more than the decision — it is what gets discussed in interviews.

---

## D1 — Prediction task definition

**Decision.** Use `SepsisLabel` exactly as shipped with the Challenge. Do not re-derive it.

**Reasoning.**
- The 6-hour horizon is already encoded in the label (`SepsisLabel = 1` from `t_sepsis − 6`). Re-deriving would only reproduce it.
- Sepsis-3 cannot be re-derived from this dataset anyway: it requires culture orders and antibiotic administration times to establish suspicion of infection, and neither is present in the 40 columns.
- Using the shipped label keeps results comparable to the ~100 published Challenge entries. That comparability is worth more than a bespoke label.

**Rejected.** Re-deriving labels; changing the horizon to 3h or 12h. Both break comparability for no gain.

**Must appear in the README.** Readers will otherwise assume the horizon is a modelling choice rather than a property of the data.

---

## D2 — Blanking / gap window

**Decision.** No blanking window. Score every hour of every stay, including hours after onset.

**Reasoning.**
- The label already implements the lead time. Adding a blanking window on top would apply the horizon twice.
- The official utility metric evaluates every hour and already handles the timing of predictions — early, on time, late — through its own weighting. Dropping hours would make the utility score incomparable to published results.

**Rejected.** Truncating stays at onset; dropping the 6 hours immediately before onset.

---

## D3 — Inclusion and exclusion criteria

**Decision.** One exclusion only: drop patients with fewer than 8 hourly records. Keep everything else.

**Reasoning.**
- The 24-hour feature window needs some history. 8 hours is the minimum at which the shorter windows are meaningful, and it is a small, defensible cut.
- Every additional exclusion moves the cohort further from the Challenge cohort and weakens comparability.
- No age filter: this is already an adult ICU population.
- No filtering by site, unit, or outcome.

**Required.** Report exactly how many patients and hours this removes, per site, in `docs/data_notes.md`. If it removes more than 3% of patients, flag it rather than proceeding silently.

---

## D4 — Feature window sizes

**Decision.** Three windows: 6h, 12h, 24h. Statistics per variable per window: `min`, `max`, `mean`, `last`, `slope`. Plus `{var}_missing` and `{var}_hours_since_measured`.

**Reasoning.**
- 6h matches the label horizon; 24h captures slower trends such as rising creatinine or falling platelets; 12h sits between.
- This already yields roughly 600 features from 40 variables. Adding a fourth window buys little and costs training time and interpretability.

**Rejected.** 48h and 72h windows — most ICU stays are short, so these would be mostly padding. Additional statistics (median, std, count) — correlated with what is already there.

---

## D5 — Operating threshold

**Decision.** Pick the threshold that maximises the **official utility score on the site-A validation split**. Freeze it. Use that frozen value everywhere: the alert-burden analysis, the dashboard default, and the CDS Hooks card severity.

**Reasoning.**
- Utility is the metric the Challenge was designed around; it already encodes the cost of late and false alarms.
- Selecting on validation rather than training avoids optimistic bias.
- Freezing before touching site B is what makes the site-B number a genuine external result.

**Hard rule.** The threshold is never re-selected using site-B data. If the frozen threshold performs badly on site B, **that is a finding, and it goes in the report.**

**Also report.** A full threshold sweep — sensitivity, PPV, alerts per 100 ICU-days — so a reader can see what a different operating point would cost. Mark the frozen threshold on the plot.

---

## D6 — Interpretation of the cross-site drop

**Decision.** The agent produces evidence only. The human writes the interpretation.

**Evidence the agent must produce:**
1. Per-feature distribution distance between sites, ranked.
2. Per-site measurement frequency for every lab — how often each is actually ordered.
3. Sepsis prevalence per site, at both patient level and hour level.
4. SHAP global importance computed separately per site, presented side by side.
5. Calibration curves for A→A and A→B on the same axes.

**Candidate explanations to check against the evidence** — the human decides which the data supports:
- Different lab ordering culture between hospitals, changing what missingness means.
- Different case mix or ICU type.
- Different prevalence shifting calibration without changing ranking (check: does AUROC hold while calibration breaks?).
- Different measurement frequency changing what "last value" represents.

**Note for the human.** If AUROC holds up but calibration collapses, that is a *recalibration* problem, not a *discrimination* problem — and it is the more interesting and more reportable finding, because it means the model still ranks patients correctly and only its probability scale has moved. Check this first.

---

## D7 — Agent instruction file naming

**Decision.** Keep this specification in `AGENTS.md` at the repository root. If the tooling in use reads a different filename, add that file containing only:

```
See AGENTS.md for the full specification. Follow it exactly.
```

Do not maintain two copies of the spec — they will drift.

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

---

## D8 — Physiologically implausible values

**Decision.** Leave them as-is. No clipping, no nulling, nowhere in the pipeline.

**Reasoning.** (Decision recorded 2026-09-09, per AGENTS-ENGINEERING.md §24.)
- LightGBM splits on rank, so a handful of impossible values changes almost nothing about the learned splits. The cost of leaving them in is close to zero for the model that matters.
- Clipping or nulling discards signal precisely where the model needs it most: a real heart rate of 200 or a rising lactate in a septic patient is not a data-entry error, and the two are not separable from the value alone.
- Setting errors to missing changes what `{var}_missing` and `{var}_hours_since_measured` mean — they would start carrying data-entry noise instead of the clinical decision not to measure.

**Requirements.**
- The logistic-regression baseline and any scaling may therefore see outlier influence; that is accepted and expected, and the baseline is deliberately weak.
- The observed range and out-of-range counts for every variable, per site, are reported in `docs/data_notes.md` so the statement above is checkable.

**Rejected.** Clipping to clinically plausible bounds (a clipped error becomes indistinguishable from a genuine boundary value); setting out-of-range values to missing (converts recorded observations into absences).

---

## Site-B training/eval split (methodological note, recorded 2026-09-09)

**Context.** `AGENTS.md` §2.2 states site B is a held-out external test set and is never used for training; §9's matrix nevertheless requires the rows B→A and A+B→B. These conflict unless the meaning of "held out" is pinned down.

**Resolution.** Site B is split *by patient*, deterministically (same seed as everything else), `site_b_train_fraction=0.8`:

- **`b_train`** — the 80% slice. This is the §9 training data: it is used for the B→A and A+B→B rows. It is **not** used for hyperparameter selection (the grid winner is chosen on site-A validation only, AGENTS.md §9.2) or for the utility-max threshold (D5, site-A validation only).
- **`b_eval`** — the held-out 20%. This is the external test set. It is scored **exactly once per phase, after every design choice is frozen**, keeps the §2.2 stderr warning on every access, and its 20% size means the 4-cell matrix is honest about the true size of the site-B evaluation pool.

**Strictness that is preserved from §2.2.** No patient appears in both `b_train` and `b_eval`. `b_eval` is never trained on, never used for tuning, never used for imputation/scaling/calibration statistics, and never used for design iteration. If a `b_eval` score looks bad it is reported, never fixed by touching `b_eval`.

This is a working definition recorded here so recent commits do not silently redefine `training_setB`. No other §3 decision is changed.

---

## D9 — Demo deployment design (recorded 2026-09-10)

**Context.** §21-22 ask for GHCR publish + an automatic AWS deploy of the demo stack. The AWS account runs only the demo; all heavy training happens in CI or on the human's AWS machine.

**Decisions.**
- **Deploy trigger: `v*` tags only.** A tag push runs the full pipeline (fetch → data → train → eval → rigor), publishes four images (`model-api`, `cds`, `dashboard`, `fhirloader`) to GHCR tagged `sha-<short-SHA>` plus the tag name, then redeploys the stack via CloudFormation + SSM Run Command. `workflow_dispatch` allows manual redeploys.
- **Images: public GHCR packages.** The repository is MIT and the data licence permits a demo subset, so no registry credentials are needed on the instance and the deploy role needs only CloudFormation + SSM. GHCR packages of a public repository are public by default.
- **No long-lived keys.** GitHub exchanges its OIDC token for temporary AWS credentials (`sts assume-role-with-web-identity`, audience `sts.amazonaws.com`), using `curl` + the `aws` CLI rather than a third-party action. One-time role/OIDC-provider setup is `deploy/bootstrap.sh`.
- **Instance: `t3.small` x86_64, not Graviton.** GitHub's standard runners build native amd64 images; emulated arm64 builds are slow and fragile. The images are pulled at deploy time.
- **Model + seed are baked into images.** The model-api image contains the trained model and threshold; the fhirloader image contains a 200-patient demo CSV exported by `scripts/export_fhirloader.py --site A --count 200`. Neither is committed to git (deploy/.gitignore); the CSV lives only in the image, which the Challenge data terms permit for a demo subset.
- **rigor.json placeholder committed.** `results/rigor.json` (`{"alert_burden": []}`) is committed so the CDS image builds and the dashboard sweep degrades gracefully before `make rigor` has run; `make rigor` overwrites it.
- **Instance management is SSM-only.** The instance role is `AmazonSSMManagedInstanceCore`; no SSH key is required (an optional keypair opens port 22 for emergencies). Deploys run `git fetch --depth 1 origin tag <tag>` + `docker compose up -d --pull always` on the instance.
- **Cost controls.** $20/month budget alarm (email at 80%), CloudWatch `StatusCheckFailed` alarm on the instance to an SNS topic, optional email subscription.

**Not decided by the agent.** The `GithubRepo` parameter default in `deploy/template.yml` must be confirmed by the human (it is the repository's `owner/name` as used in git clone URLs), and the repository secrets in deploy/README.md must be set by the human.

---

## D10 — Demo served over plain HTTP until a domain exists (recorded 2026-09-10)

**Decision.** The deployed demo runs on the instance's public IP over plain HTTP
(`deploy/Caddyfile.http`) until the human points a domain at the stack and sets
the `DEPLOY_DOMAIN` secret. The TLS path (`deploy/Caddyfile`, Let's Encrypt via
Caddy) is the default and takes over on the next tag deploy once a domain is
configured — no stack changes are needed to switch.

**Reasoning.** The human has no domain yet and chose not to block the demo on
one (recorded 2026-09-10). Browsers show the not-secure warning on the HTTP
path, which states the prototype status honestly rather than hiding it.

**Requirements.**
- `DEPLOY_DOMAIN`, `BUDGET_EMAIL` and `HEALTH_EMAIL` are now optional stack
  inputs; empty values skip the TLS site, the budget alarm and the health-email
  subscription respectively.
- HTTP mode carries no encryption: acceptable only for the research demo of
  de-identified Challenge data, never for real clinical traffic.

---

## D11 — Fetch tolerates PhysioNet's expired certificate; checksums carry integrity (recorded 2026-09-10)

**Observation.** On 2026-09-10 PhysioNet's serving certificate is expired
(schannel reports `SEC_E_CERT_EXPIRED` for physionet.org; the first Deploy run
failed in seconds at the directory listing for the same reason). This blocks
every automated fetch, on runners and locally alike.

**Decision.** `scripts/fetch_data.sh` passes `--insecure` to the two
physionet.org curl call sites (listing + file downloads). TLS verification of
the transport is no longer the integrity guarantee for these calls.

**Why this is safe.** Integrity is guaranteed by content, not transport:
`data/CHECKSUMS.sha256` — committed to git before any download, containing the
aggregate per-site digest and the scorer digest — is verified by `make data`
(`scripts/verify_checksums.sh`) on every run, and any mismatch refuses to
proceed. A man-in-the-middle or corrupted origin therefore cannot enter the
cohort. The scorer is fetched from raw.githubusercontent.com and keeps normal
certificate verification.

**Reversal.** Once PhysioNet renews its certificate, remove `--insecure` from
the two call sites; nothing else changes. The checksum file records the
expected content either way.

---

## D12 — Training runs on a data-holding machine; CI deploy consumes committed artifacts (recorded 2026-09-10)

**Observation.** The tag-driven "train in CI" design is not viable: the first
Deploy run failed instantly (PhysioNet TLS certificate expired, see D11) and
the second run got 4 minutes into the 40k-file download before the connection
was cut — GitHub runner IPs are rate-limited/blocked by PhysioNet after a
burst. Training on this workstation is also impractical: the 18-configuration
grid on 1.55M patient-hours needs a 2-4 hour budget on the 4-core i5-6500.

**Decision.**
- The pipeline (`make data train eval rigor`) runs **once on a machine that
  holds the data** (the human's AWS machine). Its frozen artifacts are
  committed to git: `results/models/`, `results/threshold.json`,
  `results/metrics.json`, `results/cross_site_matrix.md`, `results/rigor.*`,
  `results/figures/`, `docs/data_notes.md`. Patient data is never committed —
  only model files and aggregate results tables.
- The Deploy workflow no longer trains. It asserts the artifacts are present
  (fails loudly with an actionable message otherwise), bakes them into the
  `model-api` image, and deploys. A tag deploy is now minutes, not hours.
- The demo seed served by the fhirloader image is the **committed synthetic
  fixture cohort** (22 patients, `tests/fixtures/mini_cohort/`, corresponding
  to no real person). No real patient data travels through CI. Loading the
  real-cohort demo subset (`--count 200`) remains available on any machine
  with `data/` via `make fhir-load-demo` — unchanged.
- Site-B discipline is preserved: site B is scored exactly once (on the
  data-holding machine, design frozen), and the deployed model is that same
  recorded model — no second scoring path exists.

**Rejected.** Retrying CI-side fetches (blocked IPs are not a code problem);
artifacts from an S3 bucket (adds credentials the deploy decision D9 removed);
committing the 200-patient real-cohort demo CSV (violates the no-patient-data
rule, AGENTS.md A2.6).

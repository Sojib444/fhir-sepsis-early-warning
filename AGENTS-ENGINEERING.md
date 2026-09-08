# AGENTS-ENGINEERING.md

Addendum to `AGENTS.md`. Sections §18–§24. The rules in `AGENTS.md` §2 (ground rules) and §3 (decisions reserved for the human) apply here unchanged.

Read this alongside `AGENTS.md`, not after it. Several items here are cross-cutting and must be built from Phase 1, not bolted on at the end.

---

## §18 — Data acquisition

The repository must be runnable by a stranger with `git clone` and one command. Manual download instructions are not acceptable for a project whose main claim is reproducibility.

### Tasks

1. `scripts/fetch_data.sh`:
   - Downloads the Challenge 2019 training archives from PhysioNet.
   - Extracts into `data/raw/training_setA/` and `data/raw/training_setB/`.
   - Also fetches the official `evaluate_sepsis_score.py` into `src/sepsis/vendor/`.
   - **Idempotent**: if files exist and checksums match, exit early and say so.
   - **Resumable**: use `curl -C -` or equivalent. The archive is large enough that a dropped connection on a slow link must not mean starting over.
   - **Fails loudly**: on any failure, print the manual download URL and the expected paths, then exit non-zero.

2. `data/CHECKSUMS.sha256` — checked into git. Covers the archives and the vendored scorer. `make data` verifies before doing anything else and refuses to proceed on mismatch.

3. `scripts/make_fixtures.py` — generates `tests/fixtures/mini_cohort/`: roughly 20 synthetic patients in the exact `.psv` format, covering the edge cases in §19. **This is committed to git.** It is hand-constructed, not sampled from the real data, so it carries no licensing question and lets CI run without downloading 40,000 files.

### Acceptance criteria

- On a clean machine: `git clone && make setup && make data && make train && make eval` succeeds with no manual steps.
- `make test` passes with `data/raw/` completely empty (fixtures only).
- Running `make data` twice does not re-download.

### Before hosting anything publicly

Check the licence terms on the PhysioNet dataset page and record what they permit in `docs/data_notes.md`. Open access is not the same as unrestricted redistribution. If in doubt, serve only derived aggregates and the synthetic fixtures publicly, never the raw records.

---

## §19 — Test strategy

Five tiers. Tiers 1–3 must run in CI without the real dataset.

### Tier 1 — Unit
Pure functions, no I/O. Whole tier under 10 seconds. Window statistics, slope computation, time arithmetic, unit conversions, LOINC lookup.

### Tier 2 — Invariant tests (highest priority)

These encode `AGENTS.md` §2. They are the tests that protect the scientific claim; if they are weak, nothing else matters.

- No `patient_id` appears in more than one split.
- No feature at hour `t` changes when data at hours `> t` is mutated. *Implement this as an actual test*: build a patient, compute features, perturb the future, recompute, assert equality. This catches leakage that reading the code will not.
- Scaler / imputation / calibration parameters fit on train differ from those that would be fit on the full set.
- Loading site B emits the required warning.
- Determinism: identical seed produces byte-identical `metrics.json` except for the timestamp field.

### Tier 3 — Fixture-based
Runs the full pipeline over `tests/fixtures/mini_cohort/`. Must cover, at minimum:

| Case | Expectation |
|---|---|
| Patient with exactly 8 hours | Included (boundary of D3) |
| Patient with 7 hours | Excluded |
| Patient with 1 hour | Excluded |
| Septic patient, onset at hour 0 | `SepsisLabel` is 1 from the first row |
| Non-septic patient | All labels 0, no crash |
| Variable never measured for a patient | `hours_since_measured` handled per policy, no NaN leaking into the model |
| Variable measured only in the final hour | No backward fill into earlier hours |
| All labs null, vitals present | Pipeline completes |
| Rows out of order in the file | Sorted or rejected — decide and test it |
| Negative `HospAdmTime` | Handled (patient was in hospital before ICU) |
| Physiologically impossible value | Handled per D8 |

### Tier 4 — Integration
`docker compose up`, wait for healthchecks, load fixtures into HAPI, call the CDS Hooks endpoint, assert a valid card. Torn down afterwards.

### Tier 5 — Regression
`results/golden_metrics.json` holds the accepted numbers. A test compares current output within a tight tolerance. When a change is intended, the golden file is updated **in the same commit**, with the reason in the commit message.

### Rule

Every phase ships with tests. A phase whose tests do not pass is not complete, regardless of whether the feature works when run by hand.

---

## §20 — Bug protocol

Non-negotiable, and it applies to the agent as much as to the human.

1. **Reproduce first.** Write a failing test that captures the bug before touching the fix. The test goes in `tests/regression/` and its name references the issue.
2. **Then fix.** The commit contains both the test and the fix. A fix without a test is not accepted.
3. **Then widen.** Ask: what class of bug is this, and are there sibling cases? If a window-boundary bug is found at 6h, test 12h and 24h too.

### If a bug is found in anything that produced a reported number

This is the part that matters scientifically, and it is what separates this project from a student exercise.

- Recompute **every** affected result.
- Regenerate `results/` and every figure.
- Record in `docs/errata.md`: what was wrong, which numbers changed, by how much, and the commit that fixed it.
- If a number in the README or the report changed, update both.
- **Never silently correct a published number.**

Keeping a visible errata log is a strength, not an admission of weakness. Reviewers read it as evidence that the numbers are trustworthy.

---

## §21 — CI/CD

`.github/workflows/ci.yml`, triggered on push, pull request, and tag.

### Stages

1. **Lint** — `ruff` (Python), `dotnet format --verify-no-changes`, `eslint` (Angular).
2. **Fast tests** — Tiers 1–3. No real data, no Docker. This is the gate that must stay under two minutes, or people stop paying attention to it.
3. **Build** — Docker images for `model_api`, `CdsService`, `dashboard`.
4. **Integration** — Tier 4 against `docker compose`.
5. **Publish** *(tags only)* — push images to GHCR, tagged with both the git SHA and the semver tag.
6. **Deploy** *(tags only, §22)*.

### Requirements

- Cache the `uv`, NuGet and npm stores. An uncached run wastes several minutes every push.
- Pin action versions by SHA, not by floating tag.
- **AWS credentials via GitHub OIDC role assumption. No long-lived access keys in secrets.** If OIDC cannot be made to work, stop and raise it — do not fall back to static keys.
- Status badge in the README.

---

## §22 — Deployment to AWS

**Read this first.** `docker compose up` is the primary and canonical way to run this project. The AWS deployment is a convenience so that a reader can click a link instead of cloning. It must never become the only working path. If the instance is torn down, the repository must still be fully functional.

### Scope — deploy only this

Dashboard, model API, CDS service, and a HAPI instance pre-loaded with ~200 demo patients. **The training pipeline is not deployed.** Training happens locally or in CI; the deployed artifact serves a model file baked into the image.

### Architecture

Keep it small and legible:
- One `t4g.small` EC2 instance, ARM, Amazon Linux 2023.
- Docker Compose on the host, behind nginx as reverse proxy.
- TLS via Caddy or certbot. No plain HTTP.
- Everything read-only from the public side. No write endpoints, no HAPI write access exposed.
- Rate limit the model API at nginx (e.g. 10 req/s per IP).
- `/healthz` endpoint on each service, and a compose healthcheck.

### Infrastructure as code

`deploy/` containing either a single well-commented shell script or minimal Terraform. Manual console clicking is not acceptable — the deployment must be reproducible and the reviewer must be able to see how it was done.

### Cost controls — mandatory

- AWS Budgets alarm at a fixed monthly threshold, with an email alert. Configure it before the first deploy, not after.
- No load balancer, no RDS, no NAT gateway. These are the line items that turn a small demo into a large bill.
- Document the expected monthly cost in `deploy/README.md`.
- Document the teardown command. Someone must be able to destroy everything in one step.

### Public-facing requirements

- Persistent banner: **Research prototype — not for clinical use. Not a medical device.**
- A visible link back to the repository and to the limitations section.
- `robots.txt` disallowing indexing. This is a demo, not a publication.
- Serve only the synthetic fixtures or the demo subset permitted by the data licence (§18).

---

## §23 — Reproducibility contract

Reproducibility is the central claim of this project. Treat a failure here as a correctness bug, not a nice-to-have.

### Environment
- Commit `uv.lock`, `packages.lock.json`, `package-lock.json`.
- Pin Docker base images **by digest**, not by tag. `python:3.12-slim` moves; a digest does not.
- Pin the HAPI image by digest too.

### Randomness
- One seed, defined once in `config.yaml`, threaded through: Python `random`, `numpy`, `lightgbm`, `scikit-learn`, and the split function.
- Set `PYTHONHASHSEED` explicitly in the Makefile.
- LightGBM: `deterministic=true`, `force_row_wise=true`, and a fixed `num_threads`. Without these, results vary across machines and thread counts.
- Any function that samples must take the seed as an argument. No hidden global state.

### Provenance

`results/metrics.json` must embed, alongside every number:
- git commit SHA (and whether the tree was dirty)
- SHA256 of the input data
- seed
- package versions
- UTC timestamp
- which split produced each number

This one file is what lets someone check, a year from now, that a figure in the report matches the code that produced it.

### REPRODUCE.md

Exact steps from a clean machine to every number in the README, with expected runtimes. Test it on a fresh container before tagging `v1.0`. If it takes more than 30 minutes end to end, say so up front.

---

## §24 — A new decision needed from the human

### D8 — Physiologically implausible values

This dataset contains data-entry errors: heart rates of 300, temperatures of 0, negative values where none are possible.

**The agent must not decide this.** Present the human with:
1. The observed range and the count of out-of-range values for each of the 40 variables, per site.
2. Three options: leave as-is; clip to clinically plausible bounds; set out-of-range to missing and let the missingness indicator carry it.
3. A note on which option risks removing genuine extreme physiology — a real heart rate of 200 in a septic patient is not an error, and clipping it discards signal precisely where the model needs it most.

Record the answer in `docs/decisions.md` as D8 before Phase 3.

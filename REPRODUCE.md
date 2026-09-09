# Reproducing every number in this repository

Exact steps from a clean machine. Grows one section per phase; a phase that
has not shipped has nothing to reproduce and says so.

**Total time from a clean clone to the current end state: about 3.5 hours,
almost all of it downloading data.** Everything after the download is minutes.

---

## 0. Prerequisites

| Tool | Version | Why |
|---|---|---|
| [`uv`](https://docs.astral.sh/uv/) | ≥ 0.12 | manages Python 3.12 and the locked environment |
| `bash` | any | `scripts/*.sh`; Git Bash on Windows |
| `curl` | ≥ 7.68 | `--parallel` is used by the fetch script |
| `git`, `make` | any | — |

Python itself is **not** a prerequisite: `uv` installs the pinned 3.12 for you.

On Windows, `make` runs its recipes under `bash` (set in the `Makefile`), so
Git Bash must be on `PATH`.

---

## 1. Environment

```bash
git clone <this repo> && cd fhir-sepsis-early-warning
make setup
```

`make setup` runs `uv sync --all-groups`, which resolves nothing: `uv.lock` is
committed and pins every transitive dependency by hash.

*Expected runtime: 1–3 minutes on a cold cache, seconds after.*

---

## 2. Tests, without any data at all

```bash
make test
```

The whole suite runs against the 22 synthetic patients in
`tests/fixtures/mini_cohort/`, which are committed. `data/raw/` may be empty.

*Expected runtime: under 30 seconds. Expected result: all tests pass.*

---

## 3. The data

```bash
bash scripts/fetch_data.sh
```

- Downloads 40,336 `.psv` files (20,336 site A + 20,000 site B), about 330 MB.
- Fetches the official `evaluate_sepsis_score.py` into `src/sepsis/vendor/`.
- **Idempotent**: a second run downloads nothing.
- **Resumable**: interrupt it and run it again; it continues.
- **Self-checking**: every file is validated for the exact 41-column header and
  41 fields on every row. A truncated transfer is deleted and re-fetched.

*Expected runtime: about 3 hours.* Measured throughput against PhysioNet is
11 files/second and does **not** improve with more connections — the limit is
at their end. `FETCH_PARALLEL` defaults to 16.

> PhysioNet no longer publishes the training archives; the project page's own
> download link is dead. Per-file download is the only remaining route. The
> evidence is in [`docs/data_notes.md`](docs/data_notes.md).

On first completion the script writes per-site digests into
`data/CHECKSUMS.sha256`. **Commit that file.** Every later run verifies against
it and refuses to proceed on a mismatch.

---

## 4. The cohort and the data notes

```bash
make data
```

Runs, in order:

1. `scripts/verify_checksums.sh` — refuses to continue if the data on disk is
   not the data the recorded numbers came from.
2. `scripts/build_cohort.py` — parses every `.psv` into
   `data/interim/cohort.parquet`, one row per patient-hour.
3. `scripts/data_notes.py` — regenerates `docs/data_notes.md`.

*Expected runtime: under 2 minutes.* Parsing is parallel; AGENTS.md §7.1 budgets
5 minutes and the script warns if it exceeds that.

**What to check.** `docs/data_notes.md` should be byte-identical to the
committed copy apart from its provenance block, whose `timestamp_utc` and
`git_commit` naturally differ.

---

## 5. Model, evaluation, services

Not yet built. `make train`, `make eval` and `make up` exit non-zero with the
phase that will implement them.

---

## Determinism

Reproducibility here is a tested property, not a hope.

- One seed, `config.yaml: seed`, threaded through every function that samples.
  No function reaches for a global random state.
- `PYTHONHASHSEED=0` is exported by the `Makefile`, so set-iteration order
  cannot vary between runs.
- The split depends only on the set of patient IDs and the seed — not on file
  order, filesystem order, or worker count. Tested in
  `tests/invariant/test_splits.py`.
- Parallel and serial parsing are asserted to produce identical frames.
- Every generated artefact embeds a provenance block: git commit, dirty flag,
  seed, input digests, package versions, platform, UTC timestamp.

If a number changes and you cannot explain why from the provenance block, treat
it as a correctness bug and follow the bug protocol in
`AGENTS-ENGINEERING.md` §20.

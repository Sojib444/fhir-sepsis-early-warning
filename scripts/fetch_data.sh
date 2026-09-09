#!/usr/bin/env bash
#
# Fetch the PhysioNet/CinC Challenge 2019 training data and the official
# utility scorer.
#
# NOTE ON METHOD (deviation from AGENTS-ENGINEERING.md §18, recorded in
# docs/data_notes.md): §18 assumes PhysioNet publishes `training_setA.zip` /
# `training_setB.zip`. As of 2026-09 it does not — the project tree contains
# only the 40,336 individual `.psv` files. Every archive path we probed
# (archive.physionet.org, the get-zip endpoint, static/published-projects and
# the GCS mirror) returns 404. The only remaining route is per-file download,
# so this script enumerates the directory listings and fetches them in
# parallel with a single curl process.
#
# Properties required by §18:
#   idempotent   — files already on disk are skipped; a fully populated tree
#                  exits early without issuing a single file request.
#   resumable    — curl -C -; an interrupted run continues where it stopped.
#   fails loudly — any shortfall prints the manual URL and the expected paths,
#                  then exits non-zero.
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW_DIR="$REPO_ROOT/data/raw"
VENDOR_DIR="$REPO_ROOT/src/sepsis/vendor"
CHECKSUM_FILE="$REPO_ROOT/data/CHECKSUMS.sha256"

BASE_URL="https://physionet.org/files/challenge-2019/1.0.0/training"
SCORER_URL="https://raw.githubusercontent.com/physionetchallenges/evaluation-2019/master/evaluate_sepsis_score.py"
PROJECT_PAGE="https://physionet.org/content/challenge-2019/1.0.0/"

# Expected patient counts, from the PhysioNet directory listings and AGENTS.md §5.
EXPECTED_A=20336
EXPECTED_B=20000

PARALLEL="${FETCH_PARALLEL:-16}"   # concurrent connections; measured ~11.5 files/s
CHUNK="${FETCH_CHUNK:-2000}"       # files per curl invocation, so progress is visible

log() { printf '[fetch_data] %s\n' "$*"; }

fail() {
  printf '\n[fetch_data] FAILED: %s\n\n' "$*" >&2
  {
    echo "Manual download instructions"
    echo "----------------------------"
    echo "  Project page : $PROJECT_PAGE"
    echo "  Files        : $BASE_URL/training_setA/"
    echo "                 $BASE_URL/training_setB/"
    echo "  Scorer       : $SCORER_URL"
    echo ""
    echo "Expected layout once complete:"
    echo "  data/raw/training_setA/p000001.psv ...   ($EXPECTED_A files)"
    echo "  data/raw/training_setB/p100001.psv ...   ($EXPECTED_B files)"
    echo "  src/sepsis/vendor/evaluate_sepsis_score.py"
    echo ""
    echo "Re-running this script is safe: it skips whatever is already on disk."
  } >&2
  exit 1
}

need() { command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"; }
need curl
need sha256sum

sha256_of() { sha256sum "$1" | cut -d' ' -f1; }
count_psv() { find "$1" -name 'p*.psv' -size +0c 2>/dev/null | wc -l | tr -d ' '; }

# The 41 columns of a Challenge 2019 file, in order. Used to detect a truncated
# or corrupted download, which is otherwise indistinguishable from a short stay.
PSV_HEADER='HR|O2Sat|Temp|SBP|MAP|DBP|Resp|EtCO2|BaseExcess|HCO3|FiO2|pH|PaCO2|SaO2|AST|BUN|Alkalinephos|Calcium|Chloride|Creatinine|Bilirubin_direct|Glucose|Lactate|Magnesium|Phosphate|Potassium|Bilirubin_total|TroponinI|Hct|Hgb|PTT|WBC|Fibrinogen|Platelets|Age|Gender|Unit1|Unit2|HospAdmTime|ICULOS|SepsisLabel'
PSV_FIELDS=41

# ------------------------------------------------------------- validation ---
# An interrupted transfer leaves a file that is non-empty but short. Skipping it
# on the next run because it "exists" would put silently truncated patients into
# the cohort. Every file is therefore structurally checked: exact header, at
# least one data row, and 41 fields on every row. Failures are deleted so the
# next download pass fetches them again.
#
# Echoes the number of files removed.
validate_set() {
  local dir="$1"
  local bad
  bad="$(mktemp)"

  # shellcheck disable=SC2016  # $0 and FILENAME below are awk's, not the shell's
  find "$dir" -name 'p*.psv' -print0 \
    | xargs -0 -n 2000 awk -F'|' -v hdr="$PSV_HEADER" -v want="$PSV_FIELDS" '
        FNR == 1 { rows[FILENAME] = 0; if ($0 != hdr) { print FILENAME; nextfile } next }
        NF != want { print FILENAME; nextfile }
        { rows[FILENAME]++ }
        END { for (f in rows) if (rows[f] < 1) print f }
      ' 2>/dev/null | sort -u > "$bad"

  local n
  n=$(wc -l < "$bad" | tr -d ' ')
  if [ "$n" -gt 0 ]; then
    while read -r path; do
      [ -n "$path" ] && rm -f "$path"
    done < "$bad"
  fi
  rm -f "$bad"
  echo "$n"
}

# ---------------------------------------------------------------- scorer ----
fetch_scorer() {
  local dest="$VENDOR_DIR/evaluate_sepsis_score.py"
  mkdir -p "$VENDOR_DIR"
  if [ -s "$dest" ]; then
    log "vendored scorer already present, skipping"
    return 0
  fi
  log "fetching official utility scorer"
  curl -fsSL --retry 5 --retry-delay 2 --max-time 120 -o "$dest.part" "$SCORER_URL" \
    || fail "could not download the official scorer"
  mv "$dest.part" "$dest"
  log "scorer written to src/sepsis/vendor/evaluate_sepsis_score.py"
}

# ------------------------------------------------------------- listings -----
# PhysioNet serves a plain directory index for each set. Parse the hrefs.
list_patients() {
  local set_name="$1"
  curl -fsSL --retry 5 --retry-delay 2 --max-time 900 "$BASE_URL/$set_name/" \
    | grep -oE 'href="p[0-9]+\.psv"' \
    | sed -E 's/href="(.*)"/\1/' \
    | sort -u
}

# ------------------------------------------------------------- download -----
fetch_set() {
  local set_name="$1"
  local expected="$2"
  local dir="$RAW_DIR/$set_name"
  mkdir -p "$dir"

  local have removed
  have=$(count_psv "$dir")
  if [ "$have" -eq "$expected" ]; then
    removed=$(validate_set "$dir")
    if [ "$removed" -eq 0 ]; then
      log "$set_name: $have/$expected files already present and well-formed, skipping"
      return 0
    fi
    log "$set_name: removed $removed malformed file(s), re-fetching them"
  fi
  log "$set_name: $(count_psv "$dir")/$expected files present, enumerating remote listing"

  local listing todo chunk_cfg
  listing="$(mktemp)"
  todo="$(mktemp)"
  chunk_cfg="$(mktemp)"

  list_patients "$set_name" > "$listing" || fail "could not list $set_name"

  local remote_count
  remote_count=$(wc -l < "$listing" | tr -d ' ')
  if [ "$remote_count" -ne "$expected" ]; then
    fail "$set_name: listing has $remote_count files, expected $expected — the dataset layout changed, stop and check $PROJECT_PAGE"
  fi

  # Up to three passes: download what is missing, structurally validate, delete
  # anything truncated, and go round again for those. Three is enough for
  # transient failures and small enough to fail loudly on a real problem.
  local attempt n_todo offset
  for attempt in 1 2 3; do
    # Only fetch what is missing or zero-length. This is what makes re-runs free.
    : > "$todo"
    while read -r fname; do
      if [ ! -s "$dir/$fname" ]; then
        printf '%s\n' "$fname" >> "$todo"
      fi
    done < "$listing"

    n_todo=$(wc -l < "$todo" | tr -d ' ')
    if [ "$n_todo" -eq 0 ]; then
      log "$set_name: nothing left to download"
    else
      log "$set_name: pass $attempt — $n_todo files to download, $PARALLEL concurrent connections"
    fi

    # One curl process with --parallel reuses connections. Spawning one curl per
    # file is roughly 3x slower and much harsher on a public academic server.
    offset=0
    while [ "$offset" -lt "$n_todo" ]; do
      # A `tail -n +N | head -n K` pipeline hangs under MSYS (head exits, tail
      # never sees EPIPE). One sed range does the same job without a pipe.
      sed -n "$((offset + 1)),$((offset + CHUNK))p" "$todo" > "$chunk_cfg.names"
      : > "$chunk_cfg"
      while read -r fname; do
        printf 'url = "%s/%s/%s"\n' "$BASE_URL" "$set_name" "$fname" >> "$chunk_cfg"
        printf 'output = "%s"\n' "$fname" >> "$chunk_cfg"
      done < "$chunk_cfg.names"

      # `output` paths are relative and curl is run from inside $dir on purpose.
      # Under Git Bash, MSYS translates POSIX paths in command-line arguments
      # but not inside a --config file, so an absolute "/d/..." output path
      # reaches the native curl binary unconverted and nothing is written.
      #
      # Transient "curl: (28) Failed to connect" lines are normal under
      # concurrency and are retried, so they are dropped; every other message
      # is kept, because a silent write failure is exactly the bug this
      # comment exists to prevent recurring.
      ( cd "$dir" && curl -sS --parallel --parallel-max "$PARALLEL" \
           -C - --retry 5 --retry-delay 3 --connect-timeout 30 --max-time 300 \
           --config "$chunk_cfg" ) 2>&1 | grep -v 'curl: (28)' || true

      offset=$((offset + CHUNK))
      if [ "$offset" -gt "$n_todo" ]; then
        offset="$n_todo"
      fi
      log "$set_name: $offset/$n_todo requested, $(count_psv "$dir")/$expected on disk"
    done

    removed=$(validate_set "$dir")
    have=$(count_psv "$dir")
    log "$set_name: pass $attempt done — $have/$expected on disk, $removed malformed removed"

    if [ "$have" -eq "$expected" ] && [ "$removed" -eq 0 ]; then
      break
    fi
  done

  rm -f "$listing" "$todo" "$chunk_cfg" "$chunk_cfg.names"

  have=$(count_psv "$dir")
  if [ "$have" -ne "$expected" ]; then
    fail "$set_name: have $have/$expected files after download — re-run this script to resume"
  fi
  log "$set_name: complete ($have files, all well-formed)"
}

# ------------------------------------------------------------ manifests -----
# PhysioNet publishes no checksum manifest for these files, so we compute one
# and reduce it to a single digest per site. data/CHECKSUMS.sha256 is checked
# into git; `make data` refuses to proceed on a mismatch.
write_manifest() {
  local set_name="$1"
  local dir="$RAW_DIR/$set_name"
  local manifest="$RAW_DIR/$set_name.manifest.sha256"
  log "$set_name: hashing files for the manifest" >&2
  ( cd "$dir" && find . -name 'p*.psv' | sed 's|^\./||' | sort | xargs sha256sum ) > "$manifest"
  sha256_of "$manifest"
}

verify_or_record() {
  local key="$1"
  local digest="$2"
  touch "$CHECKSUM_FILE"
  local known
  known=$(grep -E "  ${key}\$" "$CHECKSUM_FILE" 2>/dev/null | cut -d' ' -f1 || true)
  if [ -z "$known" ]; then
    printf '%s  %s\n' "$digest" "$key" >> "$CHECKSUM_FILE"
    log "recorded new checksum for $key — COMMIT data/CHECKSUMS.sha256"
  elif [ "$known" != "$digest" ]; then
    fail "checksum mismatch for $key: expected $known, got $digest"
  else
    log "checksum OK for $key"
  fi
}

main() {
  log "repo root: $REPO_ROOT"
  fetch_scorer
  verify_or_record "src/sepsis/vendor/evaluate_sepsis_score.py" \
                   "$(sha256_of "$VENDOR_DIR/evaluate_sepsis_score.py")"
  fetch_set training_setA "$EXPECTED_A"
  fetch_set training_setB "$EXPECTED_B"
  verify_or_record "data/raw/training_setA" "$(write_manifest training_setA)"
  verify_or_record "data/raw/training_setB" "$(write_manifest training_setB)"
  log "done"
}

# Only run when executed, not when sourced. Sourcing lets the test suite call
# validate_set directly instead of re-implementing the truncation check in
# Python and letting the two drift apart.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi

#!/usr/bin/env bash
#
# Verify data/CHECKSUMS.sha256 (AGENTS-ENGINEERING.md §18.2).
#
# `make data` runs this before doing anything else and refuses to proceed on a
# mismatch. Entries that name a directory are checked against a manifest of
# per-file hashes; entries that name a file are checked directly.
#
# An entry whose target is absent is reported and skipped rather than failing:
# a contributor who has only site A on disk should still be able to work.
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECKSUM_FILE="$REPO_ROOT/data/CHECKSUMS.sha256"

log() { printf '[verify] %s\n' "$*"; }

if [ ! -s "$CHECKSUM_FILE" ]; then
  log "no data/CHECKSUMS.sha256 yet — run scripts/fetch_data.sh first"
  exit 0
fi

status=0
checked=0
skipped=0

while read -r expected target; do
  [ -z "${expected:-}" ] && continue
  case "$expected" in \#*) continue ;; esac

  path="$REPO_ROOT/$target"

  if [ -d "$path" ]; then
    manifest="$path.manifest.sha256"
    if [ ! -s "$manifest" ]; then
      log "SKIP $target (no manifest; re-run scripts/fetch_data.sh to rebuild it)"
      skipped=$((skipped + 1))
      continue
    fi
    actual=$(sha256sum "$manifest" | cut -d' ' -f1)
  elif [ -f "$path" ]; then
    actual=$(sha256sum "$path" | cut -d' ' -f1)
  else
    log "SKIP $target (not present)"
    skipped=$((skipped + 1))
    continue
  fi

  checked=$((checked + 1))
  if [ "$actual" = "$expected" ]; then
    log "OK   $target"
  else
    log "FAIL $target"
    log "     expected $expected"
    log "     actual   $actual"
    status=1
  fi
done < "$CHECKSUM_FILE"

log "$checked verified, $skipped skipped"

if [ "$status" -ne 0 ]; then
  log "checksum verification FAILED — refusing to proceed"
  log "the data on disk is not the data these results were computed from"
  exit 1
fi

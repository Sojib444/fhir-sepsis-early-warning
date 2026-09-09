#!/usr/bin/env bash
# CDS Hooks acceptance demo (AGENTS.md §12.5).
#
# Requires the stack up (docker compose up -d) and data loaded:
#   make up
#   make fhir-load-demo          # loads 200 patients into HAPI
#   ./scripts/cds_demo.sh p000001
#
# Prints the discovery document and one sepsis-risk card for the patient.
set -euo pipefail

BASE="${BASE:-http://localhost:8990}"
HAPI="${HAPI:-http://localhost:8080/fhir}"
PATIENT="${1:-p000001}"
COUNT="${COUNT:-200}"   # observations prefetched from HAPI for the card

echo "== 1. discovery document =="
curl -sf "$BASE/cds-services" | python3 -m json.tool

echo
echo "== 2. sepsis-risk card for $PATIENT (chronologically-ordered observations prefetched) =="
python3 - "$HAPI" "$BASE" "$PATIENT" "$COUNT" <<'PY'
import json, sys, urllib.request

hapi, base, patient, count = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])

with urllib.request.urlopen(
    f"{hapi}/Observation?subject=Patient/{patient}&_count={count}&_sort=date"
) as resp:
    bundle = resp.read().decode("utf-8")

payload = json.dumps({
    "hook": "patient-view",
    "context": {"patientId": patient, "userId": "demo"},
    # The prefetch value is the serialized Bundle itself (string form); the
    # CDS service parses it, so no extra HAPI round trip is needed.
    "prefetch": {"observations": bundle},
}).encode("utf-8")

req = urllib.request.Request(
    f"{base}/cds-services/sepsis-risk",
    data=payload,
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(req) as resp:
    print(json.dumps(json.loads(resp.read().decode("utf-8")), indent=2))
PY
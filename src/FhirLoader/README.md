# FhirLoader — LOINC mapping notes

`LoincMap.json` is the single checked-in source of truth for how the 34
clinical variables of the Challenge 2019 dataset are coded as FHIR R4
`Observation`s. It is loaded by the console app at startup; nothing is
hardcoded in the mappers.

## How codes were chosen

- Vitals use the standard bedside LOINC components (Heart rate, SpO2 via pulse
  oximetry, Body temperature, BP by location, Respiratory rate).
- `O2Sat` is deliberately **59408-5** (pulse oximetry / SpO2) and `SaO2` is
  **2708-6** (arterial blood, from ABG). These are different measurements with
  different LOINC codes; mapping both to "oxygen saturation" would be lossy.
- FiO2 is stored by the challenge as a **fraction**; LOINC 3150-0 is the
  fraction component, unitless, so the stored fraction is the FHIR value. Do
  not multiply by 100.
- Units are UCUM (`http://unitsofmeasure.org`).

## verified vs unverified

Every entry has `"verified": true|false`.

- `true` — the code was checked against `loinc.org` when this file was written.
- `false` — the code (or the matrix/unit) is taken from memory and must be
  confirmed against `loinc.org` before this mapping is used outside a research
  demo. Each `false` entry has a `note` saying exactly what to verify.

Current `unverified` entries: `EtCO2` (end-tidal CO2 code), `BaseExcess`
(matrix), `pH` (venous vs arterial), `TroponinI` (assay unit), `Fibrinogen`
(code/specimen).

Unverified codes are still loaded — the demo stack must be runnable — but the
loader prints a summary line listing them at startup so no one mistakes the
mapping for reviewed.

## Variables intentionally NOT mapped

- `Age`, `Gender` → go onto `Patient` (`birthDate`, `gender`), not observations.
- `Unit1`, `Unit2` → carried as `Encounter` extensions (no LOINC component).
- `HospAdmTime`, `ICULOS` → provenance of the stay, not clinical measurements.
- `SepsisLabel` → the study label, not a clinical observation; the dashboard
  reads true onset from `results/`, not from FHIR.

## Sex mapping caveat

The Challenge documents `Gender` as 0 or 1 but the README does not say which
way round. This repo maps **0 → Male, 1 → Female** following the MIMIC/PhysioNet
convention used by published Challenge entries, and flags this in
`Program.cs` output as a caveat. Verify against the Challenge README before any
patient-facing use.

## Idempotency convention

Every observation carries

    identifier: { system: "urn:project:sepsis-cds:origin",
                  value: "<patientId>_<variable>_<hour>" }

and every `Bundle` entry is a conditional create (`if-none-exist`) on that
identifier. Re-running the loader against a HAPI server that already contains
the data is therefore a no-op.
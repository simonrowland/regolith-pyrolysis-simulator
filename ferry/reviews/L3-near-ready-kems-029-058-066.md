# L3 — near-ready composition + oxygen + pressure closure (3-family cohort)

**Repo:** regolith-pyrolysis-simulator
**Branch:** `empirical/l3-near-ready-kems-029-058-066-2026-09-22`
**Base:** `origin/work-v064-green` @ `2e9e17c3d`
**Worktree:** `/workspace/repos/wt/slot-08`
**Date:** 2026-09-22 ~22:40 ET (America/Toronto)

## Intent

Close printed composition / oxygen / pressure waypoints on the first 3-family
cohort (A3 ladder: remaining corpus has zero 1–2-family gaps after L1+L2). Land
only **printed** maps. Derived values would carry a **DERIVED** stamp (none
landed here). Vacuum KEMS without printed fO2/buffer/pO2 → oxygen GAP. Figure-only
compositions refused.

## PDFs

| Source | Expected path | Status |
|---|---|---|
| `kems-029-yakovlev-shornikov-2011` | `raw/kems-029-yakovlev-shornikov-2011/…pdf` | **MISSING** in `/workspace/batch-z/pdfs/`, ferry-inbox `from-main-Z-*/pdfs/`, and repo literature pdfs |
| `kems-058-ohara-1987` | `raw/kems-058-ohara-1987/…pdf` | **MISSING** (same) |
| `kems-066-ichise-1977` | `raw/kems-066-ichise-1977/…pdf` | **MISSING** (same; neighbouring ichise-1975/1989 present) |

Landings below use **already-transcribed** Table cells in the extracts (prior
extract workers). No numbers invented. Parent should ASK Dropbox for the three
PDFs if a visual audit is required; this lane did not ASK.

## Before → After (live migrate, write=False; engine_point)

Probe = every migrated observation; composition/oxygen/pressure gaps tallied.

### kems-029-yakovlev-shornikov-2011

| | Before | After |
|---|---|---|
| Composition | missing 6/6 | **unchanged** missing 5/5 scored obs |
| Oxygen | missing | **unchanged** |
| Pressure | missing | **unchanged** |
| engine_point READY | 0 | **0** |

**Refused (P0-risk):**
- **Composition:** multiple meteorite/CAI materials (Murchison, Krymka, Saratov
  chondrules, Efremovka CAI A/B); no single printed composition map. Series-level
  `printed_composition` stays unknown.
- **Oxygen:** O2 table is `method_class: model_derived` /
  `admission_status: model_output_not_measurement` (authors' calculated p(O2)
  from CAI vs chondrite vaporization analysis, cross-linked to kems-028 torr
  table). Not a printed fO2_control / buffer / measured pO2. Left GAP — do not
  invent vacuum-as-fO2.
- **Pressure:** chamber/run pressure not printed as a point (instrument detection
  limits are not a run pressure).

**d-032 Option A:** moved `yakovlev_shornikov_2011_kems_method_geometry`
observations → Na.context as type `apparatus` (method geometry, not a scored
species observation). observation+context ID total conserved (6→5+1).

### kems-058-ohara-1987

| | Before | After |
|---|---|---|
| Composition | missing on all | **19/34** `normalized_initial_composition` on Table 2 charges |
| Oxygen | missing | **unchanged** — vacuum FetO-P2O5 KEMS; no printed fO2/buffer/pO2 |
| Pressure | missing | **unchanged** — chamber pressure not printed as a point |
| engine_point READY | 0 | **0** (oxygen + pressure still open) |

**Landed:** Table 2 samples 1–19 as `feto-p2o5-sample-{1…19}` with
`initial_composition` mole_fraction maps from printed mol% / 100 (unit
conversion only; oxides present in each printed row only; Fe2+/Fe3+ not a
composition component). Split Table 2 activity rows onto those experiments.
Sample 20 remains on the umbrella series (Table 1 only; composition not
invented — already documented in extract).

**Refused:**
- **Oxygen:** PO/PO2/P2 Table 1 columns are phosphorus-bearing gas species
  partial pressures, not oxygen fugacity. `ohara_1987_feto_t_and_pO2_gate` is an
  author estimate gate for L_P (FetO t≈0.95), not a printed oxygen_condition.
  Moved that gate to FeO.context (`apparatus`). Vacuum KEMS → oxygen GAP.
- **Pressure:** no printed chamber/run total_pressure point.

**d-032 Option A:** moved `ohara_1987_kems_geometry_and_calibration` and
`ohara_1987_feto_t_and_pO2_gate` to context (`apparatus`). Split adds new
observation IDs (Ueda L2 pattern); move conserves the two relocated IDs.

### kems-066-ichise-1977

| | Before | After |
|---|---|---|
| Composition | missing on all | **25/45** `normalized_initial_composition` on Table 1 N_Al charges |
| Oxygen | missing | **unchanged** — vacuum Al2O3-cell KEMS; cell reaction ≠ fO2_control |
| Pressure | missing | **unchanged** — chamber pressure explicitly unstated; author P_Al2O/P_Al
  Torr “simple assumption” refused as run pressure |
| engine_point READY | 0 | **0** (oxygen + pressure still open) |

**Landed:** Table 1 N_Al ∈ {0.0, 0.1, …, 0.368, 0.406, …, 1.0} as
`fe-al-nal-{0p0…1p0}` with mole_fraction Composition (Al=N_Al, Fe=1−N_Al
complement). Split Al/Fe Table 1 activity points onto per-charge experiments;
ion-ratio N_Al=0.5 row pointed at `fe-al-nal-0p5`.

**Refused:**
- **Oxygen:** vacuum alumina Knudsen cell; 4Al+Al2O3=3Al2O is a cell-reaction
  assumption for an rejected activity route, not a printed fO2 buffer. Left GAP.
- **Pressure:** chamber_pressure unstated in extract; 0.8/0.4 Torr Al2O/Al pair
  is labelled author estimate / simple assumption — not a measured run pressure.

**d-032 Option A:** moved `ichise_1977_kems_geometry_and_calibration` to
Al.context (`apparatus`).

## READY status

None of the three sources is fully **READY** for `engine_point` after this lane
(expected for a 3-family cohort when oxygen and pressure are unprinted). Lane is
the honest READY **path**: printed composition closed on Ohara 19 charges and
Ichise 12 charges; oxygen/pressure left GAP where not printed; Yakovlev fully
refused.

| Source | READY before | READY after | Composition selected after |
|---|---:|---:|---|
| kems-029 | 0 | 0 | 0/5 |
| kems-058 | 0 | 0 | **19/34** |
| kems-066 | 0 | 0 | **25/45** |

## Tests

- `tools/validate_literature_extracts.py` on all three: **OK**
- `tests/battery/test_waypoints.py` + `test_context_observation_boundary.py` +
  `test_printed_fo2.py`: **69 passed** (`-o addopts=`)

Derived store not regenerated (extract-only lane).

## Commits / push

Tip: *(filled after commit)*


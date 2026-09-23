# L5 — near-ready boulliung / hendrix / sossi (printed waypoints)

**Repo:** regolith-pyrolysis-simulator  
**Branch:** `empirical/l5-near-ready-boulliung-hendrix-sossi-2026-09-22`  
**Base:** `origin/work-v064-green` @ `2e9e17c3d`  
**Worktree:** `/workspace/repos/wt/slot-l5`  
**Date:** 2026-09-22 (America/Toronto)

## Intent

Close printed engine_point waypoints on the three assigned sources. Land only
**printed** evidence. DERIVED stamps when arithmetic is required (none needed
here). No vacuum-as-fO2 invent. Figure-only refused. Honor **d-041** (Sossi
recipe→composition PENDING — do not invent).

## PDFs

| source | PDF |
|---|---|
| `hendrix-2024-reactivity-reduced-simulants` | `/workspace/batch-z/pdfs/hendrix-2024-reactivity-reduced-simulants.pdf` (verified Table 1) |
| `boulliung-2025-mercury-volatile-metals-magmatic` | **MISSING** — ASK written |
| `sossi-2020-cu-zn-isotope-evap-formalism` | **MISSING** (OA fetch blocked) — ASK written; values from prior extract + scout |

ASK: `/workspace/ferry-inbox/from-empirical/ASK-pdfs-2026-09-22-l5.md`

## Before → After (live migrate, write=False; engine_point)

Probe = unique point_conditions contexts per observation; status case-insensitive.

### hendrix-2024-reactivity-reduced-simulants

| | Before | After |
|---|---|---|
| Composition | `unsupported_print_form` (string labels) | **closed** — Table 1 oxide maps on all 3 experiments |
| Oxygen | missing | **unchanged GAP** — pure H2 reduction; no printed fO2 / buffer / pO2 |
| Pressure | missing | **unchanged GAP** — H2 flow described; no printed total/partial pressure |
| engine_point READY | 0/3 | **0/3** (oxygen + pressure still open) |

Landed (PDF Table 1, published p.2489 / PDF p.4), verified against pdftotext:

- JSC-1A: SiO2 46.2, TiO2 1.85, Al2O3 17.9, FeO 11.2, MnO 0.19, MgO 6.87, CaO 9.43, Na2O 3.33, K2O 0.85, P2O5 0.62
- LMS-1: SiO2 42.81, TiO2 4.62, Al2O3 14.13, Cr2O3 0.21, FeO 7.87, MnO 0.15, MgO 18.89, CaO 5.94, Na2O 4.92, K2O 0.57, P2O5 0.44 (SO3 0.11 printed, omitted — not in oxide vocabulary)
- LHS-1: SiO2 44.18, TiO2 0.79, Al2O3 26.24, Cr2O3 0.02, FeO 3.04, MnO 0.05, MgO 11.22, CaO 11.62, Na2O 2.30, K2O 0.46 (SO3 0.1 omitted)

### sossi-2020-cu-zn-isotope-evap-formalism

| | Before | After |
|---|---|---|
| Composition | `unsupported_print_form` (recipe string) | **closed** — measured mean majors of wholly glassy products |
| Temperature | missing at series | **closed on Table 1 run points** (T_C → K) |
| Oxygen | missing | **closed on Table 1 run points** (printed logfO2) |
| engine_point READY | 0 | **50/51** contexts READY |

Landed composition (section 3.1 printed mean, FeOT→FeO label equivalence only):

SiO2 41.70, Al2O3 10.60, MgO 15.54, FeO 14.58, CaO 17.59

**d-041 honored:** recipe An42Di58 + ~15 wt% Fo + ~15 wt% Fe2O3 is retained as
characterization prose only. No calculated composition from recipe.

Table 1 `rows` → `series` so migrate expands per-run points; `temperature_C` →
`T_C` (migrate series axis vocabulary). Per-run `logfO2` merges into
`point_conditions.fO2_log`. Residual 1 GAP context is a non-Table-1 observation
without T/fO2 (alpha / model rows).

### boulliung-2025-mercury-volatile-metals-magmatic

| | Before | After |
|---|---|---|
| Composition | `unsupported_print_form` | **unchanged GAP** — Table 1 body absent from pre-proof |
| Oxygen | missing | **unchanged GAP** — "about FMQ−1" is approximate relative, not a point |
| Pressure | missing | **unchanged GAP** — ~2.7 mbar is pre-seal evacuation, not run total P |
| Temperature | missing at series | observation points carry discrete T; series setpoint absent |
| engine_point READY | 0 | **0** |

Refusals (documented in extract `notes` / `refused`):

1. Invent Table 1 oxides from missing table / figure images
2. Copy Norris & Wood (2017) undoped basalt oxides (not reprinted here)
3. Collapse "about 1 log unit below FMQ" to a point log_fO2 or buffer=FMQ
4. Vacuum / evacuation → oxygen_condition
5. Evacuation ~2.7 mbar → run total_pressure_Pa

## Tests

- `tools/validate_literature_extracts.py` hendrix + sossi: **OK**
- boulliung: YAML parses; pre-existing validator issues (absolute provenance_path,
  equipment shape, fidelity_samples) unchanged by this lane
- Derived store **not** regenerated (extract-only)

## Commits / push

Tip: `aa33a2c2c92937200f5f349ead65f6245f0f933f`


Tip SHA: `aa33a2c2c92937200f5f349ead65f6245f0f933f`  
Pushed: `origin/empirical/l5-near-ready-boulliung-hendrix-sossi-2026-09-22`

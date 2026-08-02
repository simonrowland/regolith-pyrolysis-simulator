# CEA DRAFT fidelity recheck (post-repair)

**Date:** 2026-08-02
**Artifact:** `docs-private/research/2026-08-01-cea-sweep/vp-cea-u0-hits-DRAFT.yaml`
**Tool:** `tools/vp_cea_ingest.py` (slash-year refs, same-name merge, mixed standard states, gas/reaction gating)

## Gates

- `enabled_for_merge: False`
- `enabled_for_production_yaml: False`
- `record_count: 157` == emitted species `157` == 157 unique draft rows
- glued slash-year truncations remaining: 0
- condensed wrong convention: none
- condensed Psat without reactions: none

## Original 10 reviewer fidelity checks (re-run)

| # | Raw row | Numeric | Convention/provenance | Full |
|---:|---|---|---|---|
| 1 | `Ag` | PASS: 27 coeffs, 3 intervals | PASS: ref `g10/97`, `CEA_JANAF_1_bar`, kind `gas_standard_state_thermo` | **PASS** |
| 2 | `AL` | PASS: 27 coeffs, 3 intervals | PASS: ref `g12/97`, `CEA_JANAF_1_bar`, kind `gas_standard_state_thermo` | **PASS** |
| 3 | `TiO2` | PASS: 18 coeffs, 2 intervals | PASS: ref `g10/99`, `CEA_JANAF_1_bar`, kind `gas_standard_state_thermo` | **PASS** |
| 4 | `CaCL2` | PASS: 18 coeffs, 2 intervals | PASS: ref `tpis96`, `CEA_JANAF_1_bar`, kind `gas_standard_state_thermo` | **PASS** |
| 5 | `K2CL2` | PASS: 18 coeffs, 2 intervals | PASS: ref `tpis82`, `CEA_JANAF_1_bar`, kind `gas_standard_state_thermo` | **PASS** |
| 6 | `S2` | PASS: 18 coeffs, 2 intervals | PASS: ref `tpis89`, `CEA_JANAF_1_bar`, kind `gas_standard_state_thermo` | **PASS** |
| 7 | `UO3` | PASS: 27 coeffs, 3 intervals | PASS: ref `tpis82`, `CEA_JANAF_1_bar`, kind `gas_standard_state_thermo` | **PASS** |
| 8 | `FeSO4(cr)` | PASS: 18 coeffs, 2 intervals | PASS: ref `j 6/66`, `CEA_condensed_1_atm`, kind `condensed_standard_state_thermo` | **PASS** |
| 9 | `C(gr)` | PASS: 27 coeffs, 3 intervals | PASS: ref `n 4/83`, `CEA_condensed_1_atm`, kind `condensed_standard_state_thermo` | **PASS** |
| 10 | `Fe2O3(cr)` | PASS: 36 coeffs, 4 intervals | PASS: ref `g 1/01`, `CEA_condensed_1_atm`, kind `condensed_standard_state_thermo` | **PASS** |

## Fresh 5 fidelity checks

| # | Raw row | Numeric | Convention/provenance | Full |
|---:|---|---|---|---|
| 1 | `Na` | PASS: 27 coeffs, 3 intervals | PASS: ref `g 8/97`, `CEA_JANAF_1_bar`, kind `gas_standard_state_thermo` | **PASS** |
| 2 | `O2` | PASS: 27 coeffs, 3 intervals | PASS: ref `tpis89`, `CEA_JANAF_1_bar`, kind `gas_standard_state_thermo` | **PASS** |
| 3 | `SiO` | PASS: 18 coeffs, 2 intervals | PASS: ref `tpis91`, `CEA_JANAF_1_bar`, kind `gas_standard_state_thermo` | **PASS** |
| 4 | `Mg` | PASS: 27 coeffs, 3 intervals | PASS: ref `g 6/97`, `CEA_JANAF_1_bar`, kind `gas_standard_state_thermo` | **PASS** |
| 5 | `FeO` | PASS: 18 coeffs, 2 intervals | PASS: ref `j 9/66`, `CEA_JANAF_1_bar`, kind `gas_standard_state_thermo` | **PASS** |

## Defect disposition (review §Required disposition item 1)

| Defect | Fix |
|---|---|
| Duplicate record identity (Fe2O3(cr) Curie pair) | Same-name records merge all intervals; 4/4 segments emitted |
| Emitted-count mismatch (158 selected / 157 written) | `record_count` counts emitted species after merge (=157) |
| Slash-year ref truncation (`g10/97`→`g10`) | Header parser accepts glued `letter+digits/YY` |
| Mixed standard states | Gas `CEA_JANAF_1_bar` / 1e5 Pa; condensed `CEA_condensed_1_atm` / 101325 Pa |
| Gas/reaction bundling | Unpaired condensed → `condensed_standard_state_thermo`; Psat only with `source_reactions` |

**Totals:** full PASS **15/15**; numeric PASS **15/15**; enable gates remain false.

READY: docs-private/research/2026-08-01-cea-sweep/fidelity-recheck-2026-08-02.md

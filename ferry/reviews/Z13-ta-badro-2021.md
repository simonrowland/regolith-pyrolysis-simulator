# Z13 FIDELITY AUDIT — ta-badro-2021 (engine_point-READY)

**Date:** 2026-09-23 (America/Toronto, EDT)  
**Seat:** Z13 — BACKLOG 5 priority fidelity (B5 numbering; supersedes opportunistic AQ1 Z13 seat for write-up identity)  
**Repo / branch:** `regolith-empirical` @ `empirical/z13-ta-badro-2021-2026-09-23`  
**Base:** `origin/work-v064-green` = `2e9e17c3d138fdfba9269c493f82974a71153fa5`  
**Tip:** `2e9e17c3d138fdfba9269c493f82974a71153fa5` (unchanged; no P0 fix commit)  
**Worktree:** `/workspace/repos/wt/slot-z13-ta-badro`  
**PDF:** `/workspace/ferry-inbox/from-main-B5-20260923T041100Z/audit-pdfs/ta-badro-2021.pdf` (never committed)  
**Rule:** P0 = wrong number stored that can reach a result/score/ledger today. Fix P0 on branch. Latent ≤ P1. No invented data.  
**Priority note:** Only `engine_point`-READY source on green; wrong number here breaks the only reproduction path (`tests/battery/test_engine_intensive_charge.py`, `test_printed_fo2.py`).

## Candidate selection

| Candidate | Disposition |
| --- | --- |
| **ta-badro-2021** | **AUDITED FIRST / ALL observations** (B5 mandate) |

Single-source seat (highest-priority Z item).

## Verdict

| Extract | Obs / layers | Sampled | P0 mismatches | Coverage gaps | Fix commit |
| --- | ---: | ---: | ---: | --- | --- |
| ta-badro-2021 | **28** species obs (24 measured δ + 4 fitted α) + 6 VP runs + starting recipe + T2 | **ALL 28** (+ full Table 1 This-study 6×16 grid + Starting + full Table 2 Advective 4×2) | **0** | Richter-2007 Table 1 rows (declared comparison_data); Sossi/Richter comparison columns in T2; Figs 1–6 figure-only; per-VP durations absent in source | none |

**Overall: PASS — no P0. No extract patch committed. Pushed FF (empty tip = green base).**

---

## Method

1. `pdftotext -layout` + `-raw` under `/workspace/ferry-inbox/reviews/_z13_ta_badro_audit/`.
2. `pdftoppm -png` 200 dpi for Table 1 (PDF p.6 / published 105) and Table 2 (PDF p.13 / published 112); visual confirm of every This-study cell.
3. Dump all `species.*.observations` + `runs` + lab/conditions → JSON; cell-by-cell vs PDF.
4. Fields checked: **value / uncertainty / unit / basis / sign / T·P·fO2 / locator / row→experiment (VP1–VP6)**.
5. `uv run python tools/validate_literature_extracts.py data/literature/extracts/ta-badro-2021.yaml` → **OK**.

---

## 1. ta-badro-2021 (Badro et al. 2021, C. R. Géoscience 353:101–114)

**PDF:** 15 pp. OA Centre Mersenne. Text layer good; Table 1 layout-extractable; Table 2 confirmed on page render (α headers; pdftotext sometimes OCR-misreads α as δ — numbers verified on PNG).

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 2 (`Mg`, `Si`) |
| Observations | **28** (14 Mg + 14 Si) |
| Measured isotope deltas | 24 (6 VP × {δ²⁶Mg, δ²⁵Mg, δ³⁰Si, δ²⁹Si}) |
| Fitted fractionation factors | 4 (Table 2 Advective) |
| Runs | 6 (`VP1`–`VP6`) |
| Experiments | 7 (`vp-series` + `vp1`–`vp6`) |
| Benches | 1 (`aerodynamic-levitation-laser-furnace`) |
| fidelity_samples | 5 |
| Declared scope | 28 source_observations; 0 digitized figures |

### Sample = ALL 28 (+ full T1 This-study + T2 Advective)

#### Table 1 — Starting + residue oxides / ratios / f / deltas (published p.105)

| Sample | MgO | SiO2 | Al2O3 | CaO | MgO/Al2O3 | SiO2/Al2O3 | f(Mg) | f(Si) | δ²⁶Mg | 2se | δ²⁵Mg | 2se | δ³⁰Si | 2se | δ²⁹Si | 2se | OK? |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Starting | 12 | 46 | 19 | 23 | 0.6316 | 2.4211 | — | — | — | — | — | — | — | — | — | — | yes |
| VP1 | 11.61 | 37.62 | 22.99 | 27.78 | 0.5050 | 1.6364 | 0.800 | 0.676 | −3.78 | 0.19 | −1.98 | 0.12 | −1.73 | 0.04 | −0.78 | 0.02 | yes |
| VP2 | 12.05 | 40.03 | 21.74 | 26.18 | 0.5543 | 1.8413 | 0.878 | 0.761 | −4.39 | 0.21 | −2.29 | 0.12 | −2.63 | 0.17 | −1.27 | 0.08 | yes |
| VP3 | 11.9 | 41.45 | 21.16 | 25.49 | 0.5624 | 1.9589 | 0.890 | 0.809 | −4.80 | 0.22 | −2.49 | 0.14 | −2.68 | 0.31 | −1.17 | 0.30 | yes |
| VP4 | 11.94 | 41.68 | 21.08 | 25.31 | 0.5664 | 1.9772 | 0.897 | 0.817 | −4.62 | 0.22 | −2.40 | 0.13 | −2.79 | 0.07 | −1.33 | 0.05 | yes |
| VP5 | 11.9 | 39.08 | 22.25 | 26.77 | 0.5348 | 1.7564 | 0.847 | 0.725 | −4.22 | 0.28 | −2.19 | 0.15 | −2.26 | 0.28 | −0.97 | 0.26 | yes |
| VP6 | 10.77 | 34.81 | 24.51 | 29.92 | 0.4394 | 1.4202 | 0.696 | 0.587 | −2.27 | 0.14 | −1.15 | 0.10 | −1.04 | 0.11 | −0.40 | 0.06 | yes |

Every measured obs checked: `isotope_delta`, `two_se`, `isotope_ratio`, `T_K=1873`, `reference_standard=BHVO-2G`, `units=permil`, `experiment=vpN`, and YAML-anchored `run.residue_composition_wt_pct` + `published_reduction` (f_Mg/f_Si/ratios) match the same VP row. Signs all negative as printed. Basis = residue wt% (not starting recipe) correctly labelled `composition_basis`.

#### Table 2 — Advective (this study) α ± 2SD (published p.112)

| Ratio | Stored factor | 2SD | PDF | OK? |
| --- | ---: | ---: | --- | --- |
| 30Si/28Si | 0.9943 | 0.0006 | Advective 0.9943 / 0.0006 | yes |
| 29Si/28Si | 0.9970 | 0.0004 | 0.9970 / 0.0004 | yes |
| 26Mg/24Mg | 0.9906 | 0.0008 | 0.9906 / 0.0008 | yes |
| 25Mg/24Mg | 0.9950 | 0.0006 | 0.9950 / 0.0006 | yes |

Fit obs tagged `class_tag: derived` / `method_class: model_derived` (Rayleigh eq. 17–18); not treated as direct measurements. `derived_from` lists the six VP deltas of that ratio.

#### Conditions (shared across VP runs; engine_point oxygen path)

| Field | Stored | PDF | OK? |
| --- | --- | --- | --- |
| T | 1873 K | Table 1 caption / Methods | yes |
| pO2 | `10^-9.1` atm | Abstract | yes |
| log fO2 | −9.1 (log10) | Methods p.105 | yes |
| Gas mix | 92% Ar / 1.6% CO2 / 6.4% H2 | Methods | yes |
| IW ref | IW −0.5 (IW = −8.6) | Methods | yes (quote retained) |
| Duration | unknown per-VP; ranges retained | Abstract 60–600 s vs Methods 180–900 s | yes (conflict recorded, no midpoint invented) |
| Precursor chunk | 15 mg × 6 | Methods | yes |
| Bead diameter | ~2 mm | Methods | yes |
| Gas flow | 800–1000 sccm (14–18 cm³/s) | §2 / Fig.1 | yes |
| Nozzle inlet | 1.5 mm | §2 / Fig.1 | yes |
| Chamber P | 1 atm | §4.1 comparative quote | yes (as printed) |

`source_discrepancies.abstract_fit_uncertainties` correctly keeps Abstract ±0.0004 / ±0.0003 for α²⁶Mg / α³⁰Si **without** converting them to Table 2 2SD (0.0008 / 0.0006). Fit observations use Table 2 2SD — right authority for labelled uncertainty.

### Coverage

- **Table 1 This-study** (primary product used by engine_point / fo2 migration): fully represented.
- **Table 2 Advective column**: fully represented.
- Not extracted (acceptable; already declared in `extraction_scope`): Richter et al. 2007 elemental rows in Table 1; Vacuum / 1-atm comparison columns in Table 2; Figs 1–6 trajectories; per-run mass / duration (absent in source — `absent_parameters` + unknown duration).

### Row → experiment mapping

| Obs pattern | experiment_id | run |
| --- | --- | --- |
| `badro_2021_vp{N}_delta*` | `vp{N}` | `runs.VP{N}` residue + conditions |
| `badro_2021_fit_*` | `vp-series` | shared starting recipe + Rayleigh note |

No cross-wired VP rows.

### P0 / P1

- **P0: 0** — every numeric payload that can reach engine_point / oxygen waypoints matches PDF (T, log fO2 −9.1, starting wt% 12/46/19/23, all 24 deltas+2se, all 4 α+2SD, all residue/ratio/f cells).
- **P1 (latent, no edit):** observation `type: rate_series` for isotope_delta / isotopic_fractionation_factor is a schema stretch (not a wrong number). Absolute `source_path` under locators points at a Mac corpus path (hygiene; same class as other extracts). Chamber pressure taken from a comparative 1-atm phrase rather than an apparatus gauge reading.

### Validator

`tools/validate_literature_extracts.py` → **OK: 1 extract file(s) valid**.

---

## Push

```text
branch: empirical/z13-ta-badro-2021-2026-09-23
base/tip: 2e9e17c3d138fdfba9269c493f82974a71153fa5
P0 count: 0
```

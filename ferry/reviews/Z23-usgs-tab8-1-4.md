# Z23 FIDELITY AUDIT — usgs-tab8-1 / usgs-tab8-4

**Date:** 2026-09-23 (America/Toronto, EDT)  
**Seat:** Z23 — BACKLOG 5 remaining USGS lunar sourcebook tables (after Z18 fixed tab8-2)  
**Repo / branch:** `regolith-empirical` @ `empirical/z23-usgs-tab8-1-4-2026-09-23`  
**Base:** `origin/work-v064-green` = `2e9e17c3d138fdfba9269c493f82974a71153fa5`  
**Tip:** `79ebc63c68dfdea43f01552665a751f1832eb511`  
**Worktree:** `/workspace/repos/wt/slot-z23`  
**PDFs:** `/workspace/ferry-inbox/from-main-B5-20260923T041100Z/audit-pdfs/` (never committed)  
**Skip:** `usgs-lunar-sourcebook-tab8-2` — already fixed on `empirical/z18-kato-usgs-robinot-2026-09-23` @ `11e6cf3e3` (VZ6 verifying)  
**Rule:** P0 = wrong number stored that can reach a result/score/ledger today. Fix P0 on branch. Latent ≤ P1. No invented data.

## Candidate selection

| Extract | Obs | Cells | Disposition |
| --- | ---: | ---: | --- |
| `usgs-lunar-sourcebook-tab8-1` | 23 | **781** | **AUDITED — P0=0** |
| `usgs-lunar-sourcebook-tab8-4` | 18 | **678** (post-fix) | **AUDITED + P0 FIXED** |
| usgs-lunar-sourcebook-tab8-2 | — | — | **SKIP** (Z18) |

## Verdict

| Extract | Sampled | P0 mismatches | Coverage gaps | Fix commit |
| --- | ---: | ---: | --- | --- |
| usgs-lunar-sourcebook-tab8-1 | **ALL 781 cells** vs pdftotext-layout column reparse | **0** | Sparse blanks remain blank (N<3 std omitted per source note) | none |
| usgs-lunar-sourcebook-tab8-4 | **ALL 678 cells** vs PNG visual GT + layout (sparse columns) | **61 cell repairs** (column-bleed / misassign / missing restore) | Same Z18 modes; leading-dot forms retained as printed (`.011`, `.0046`) | yes (this branch) |

**Overall: PASS after tab8-4 P0 fix. tab8-1 clean. Pushed FF (no force).**

---

## Method

1. `pdftotext -layout` + `pdftoppm -png -r 200` under `/workspace/ferry-inbox/reviews/_z23_audit/{tab81,tab84}/`.
2. **tab8-1:** character-column reparse of all 3 PDF pages → every extract cell compared (781/781).
3. **tab8-4:** layout reparse + **visual PNG ground truth** for all 18 sample groups (layout alone is ambiguous on sparse siderophile columns — same failure mode as Z18).
4. Fields checked: printed statistic cells (N / average / std / min / max) × oxide or element columns; fidelity pins; blank-vs-absent.
5. Watched for Z18 modes: **leading-digit strip**, **column-bleed**, **zero-pad**.
6. `tools/validate_literature_extracts.py` on both YAML paths — **OK**.

---

## 1. usgs-lunar-sourcebook-tab8-1 (Table A8.1 major oxides wt%)

**PDF:** 3 pp. TAB8_1R.DOC (USGS Lunar Consortium).

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 1 (`lunar_sample_major_oxides`) |
| Observations | **23** (MBAS / S&RB / BX / Anorthosite / Norite / Troctolite / AMET) |
| Table cells | **781** |
| Oxide columns | SiO2, TiO2, Al2O3, FeO, MgO, CaO, Na2O |
| fidelity_samples | 1 (Apollo 11 MBAS SiO2 average **40.46**) |

### Sample = ALL 781 cells

Automated column-aware reparse vs every stored numeric cell. Groups include all 23 YAML sample groups (Norite label protected from `N`-prefix swallow).

| Check | Extract | PDF | OK? |
| --- | --- | --- | --- |
| Apollo 11 MBAS SiO2 avg | **40.46** | p.1 Average / SiO2 | yes |
| Luna 24 MBAS SiO2 std | absent (N=2) | blank | yes |
| Apollo 16 BX SiO2 avg | **45.418** | p.3 | yes |
| Anorthosite Al2O3 avg | **33.4** | p.1 | yes |
| AMET S&RB MgO N | **6** | p.2 | yes |

### P0 / P1

- **P0: 0**
- **P1:** none material; source-omitted std when N<3 left absent (not invented).

---

## 2. usgs-lunar-sourcebook-tab8-4 (Table A8.4 siderophiles)

**PDF:** 3 pp. TAB8_4R.DOC.

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 1 (`siderophile_elements`) |
| Observations | **18** (MBAS + S&RB + AMET; no HMCT/BX per source note) |
| Table cells | **678** post-fix (was 671 with wrong assignments) |
| Element columns | Fe, Co, Ni, Ge, Mo, W, Re, Os, Ir, Au (p.1); Co…Au + Sb/Ru/Pd (p.2–3) |
| fidelity_samples | 3 (Fe 15.2; Sb 3.01; W `<130`) — all still valid |

### Sample = ALL cells (visual GT)

PNG pages used as authority wherever pdftotext glued/shifted sparse columns.

### P0 found (pre-fix) — same modes as Z18

1. **Column-bleed / glue:** Apollo 12 MBAS N `Os: '2917'` = Ir **29** + Au **17** glued; Os column actually blank; Ir/Au values parked under Os then shifted.
2. **Blank-column left-shift:** Apollo 11 S&RB blank Ru pulled Pd→Re→Os→Ir→Au one column left (lost Au).
3. **Sparse misassign:** Luna 24 MBAS W **1**/**30** stored as Ir; Luna 16/20 S&RB Re values stored as Pd; Luna 24 S&RB Os **7.7** stored as Ir (Ir should be **6.9**); Apollo 14 S&RB std **0.14** on Pd instead of Re; Luna 16 S&RB std **0.5** on Ir instead of Au.
4. **Missing restore:** AMET S&RB W maximum `<130` absent from extract.

Leading-dot as-printed forms (`.011`, `.0046`, `.11`, `.30`, `.020`) retained — match PDF glyph, not leading-digit strip.

### Fix

- **61** cell-level ops on branch (ledger: `_z23_audit/tab84/json/p0_fixes.json`); **23** surgical row replacements.
- Reasons: `column_bleed_or_misassign` / `bleed_remove` / `missing_cell_restore`.
- No invented fills for true blanks.
- Post-fix: full 18-group visual GT vs YAML — **0** mismatches; validator **OK**.

### P0 / P1

- **P0: 61 repaired**
- **P1:** pdftotext trailing-period token `0.00190.` cleaned to `0.00190` under Ir min (glyph is the number; period is layout junk at column edge).

---

## Product commits

| SHA | Message |
| --- | --- |
| `79ebc63c68dfdea43f01552665a751f1832eb511` | extracts: repair USGS A8.4 column-bleed / misassigned siderophile cells |

Base `2e9e17c3d` unchanged for tab8-1 (P0=0). Branch pushed to origin (FF, no force-push). No PDFs in git.

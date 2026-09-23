# VZ2 — independently VERIFY Z P0 fixes (Holzheid NiO T_range + Thomas Cl)

**Date:** 2026-09-23 ~00:15 EDT (America/Toronto)  
**Seat:** VZ2 (BACKLOG 5) — adversarial re-check of Z10 / Z12 fix commits (not the fixer)  
**Null hypothesis:** each claimed fix is a false positive (misread PDF, already correct, or wrong replacement).  
**PDFs:** `/workspace/ferry-inbox/from-main-B4-20260923T031628Z/audit-pdfs/`  
**Method:** fresh `pdftotext -layout` (+ Thomas `-raw`); `pdftoppm` 200 dpi of Holzheid PDF p.6 / Thomas PDF p.3; parse tip YAML vs parent; re-read printed cells. READ-ONLY on product. No new worktree claimed (inspected tip checkouts in place). Scratch: `/tmp/vz2/`.

| Tip under verify | SHA | Branch |
| --- | --- | --- |
| Z10 Holzheid (+ Burcat NO out of scope here) | `a8769e268` | `empirical/z10-lange-burcat-holzheid-2026-09-22` |
| Z12 Thomas Cl | `c5c309379` | `empirical/z12-sublimation-thomas-2026-09-22` |

---

## TL;DR

1. **CONFIRM** Holzheid NiO Table 3a `T_range_K` **1667–1673 → 1670–1677** (P0) + bundled NiO/MgO s.d.% and page-26 locator (P1) — tip matches printed row grid.  
2. **CONFIRM** Thomas AgI/Cl-024 `Cl_wt_pct` **2.70 → 2.54** (P0) + Ab/Fo-3 `rows_as_printed` K2O unc **0.05 → 0.02** (P1) — tip matches Table 1.  
3. **0 overturns.** Both P0 fixes are eligible for I2.

READY: `/workspace/ferry-inbox/reviews/VZ2-holzheid-thomas.md`

---

## Verdict table

| claimed fix | exists pre-tip? | PDF printed cell | tip value | live wrong number? | your sev | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| Holzheid NiO `T_range_K` 1667–1673→1670–1677 | **yes** — parent `a8769e268^` had `[1667.0, 1673.0]` while row `T_K` already spanned 1670–1677 | Table 3a Ni (pub. **p.26** / PDF p.6): T = 1673,1673,**1677**,**1670**,1670,1675,1671,1670,1671,1671 → min **1670** max **1677**. **1667** is Table 3b V68 only, not 3a Ni | `[1670.0, 1677.0]` | **yes** — observation-level conditions range (gates consumers independently of row list) | **P0** | **CONFIRM** |
| (+same commit) NiO_sd_pct / MgO_sd_pct on 3a Ni | **yes** — parent mostly defaulted to 5 | Image+text: NiO s.d.% **6,5,5,5,6,5,10,9,5,5**; MgO s.d.% **1,1,1,1,1,1,1,1,7,9** | tip matches exactly | unc metadata (not primary value) | **P1** held | **CONFIRM** |
| (+same) FeO 3b MgO_sd first five 5→1 | **yes** | Table 3b MgO s.d.% **1,1,1,1,1,5,7** | tip matches | unc metadata | **P1** held | **CONFIRM** |
| (+same) Table 3 locator pages 25→26 / 26→27 | **yes** | PDF p.6 header band = journal page **26**; 3c/3d on PDF p.7 = **27** | tip pages 26/27 | locator only | **P1** held | **CONFIRM** |
| Thomas AgI/Cl-024 Cl 2.70→2.54 | **yes** — parent `Cl_wt_pct: 2.7` (unc 0.06 already correct) | Table 1 (PDF p.3): AgI/Cl-024 Cl **2.54 (0.06)**; AgI/Cl-025 Cl **2.70 (0.19)** — 2.70 was 025’s value copied onto 024 | tip `2.54` / `0.06` | **yes** in extracts-v1 Cl series (extracts-v2 Cl/EPMA still unsupported/`unavailable` on this base — fix still required at source) | **P0** | **CONFIRM** |
| (+same) Ab/Fo-3 K2O unc 0.05→0.02 | **yes** in `rows_as_printed` | Ab/Fo-3 K2O **0.32 (0.02)** (Cl 2.75(0.05) already correct in structured row) | tip `0.32(0.02)` | prose snapshot only | **P1** held | **CONFIRM** |

---

## 1. Holzheid 1997 — NiO Table 3a `T_range_K` (Z10 / `a8769e268`)

**Source:** Holzheid, Palme & Chakraborty 1997, *Chem. Geol.* 139:21–38.  
**PDF:** `holzheid-1997-feo-nio-coo-activity-metal-saturated.pdf` (18 pp.; article starts at pub. p.21 → PDF p.6 = pub. **p.26**).

### Printed Table 3a Ni grid (re-read)

| run | T_K | NiO wt% | NiO s.d.% | MgO wt% | MgO s.d.% | γ_NiO |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| V 69 | 1673 | 0.17 | 6 | 7.22 | 1 | 3.06 |
| V 69 | 1673 | 0.21 | 5 | 8.94 | 1 | 2.60 |
| AD 12 | **1677** | 0.26 | 5 | 10.4 | 1 | 2.61 |
| V 70 | **1670** | 0.13 | 5 | 11.1 | 1 | 3.07 |
| V 70 | 1670 | 0.16 | 6 | 13.8 | 1 | 2.59 |
| V 64 | 1675 | 0.15 | 5 | 17.6 | 1 | 3.21 |
| V 66 | 1671 | 0.20 | 10 | 18.2 | 1 | 2.84 |
| V 70 | 1670 | 0.13 | 9 | 18.2 | 1 | 3.30 |
| V 66 | 1671 | 0.18 | 5 | 22.2 | 7 | 3.20 |
| V 65 | 1671 | 0.17 | 5 | 27.5 | 9 | 3.39 |

- **T min/max = 1670 / 1677** → tip `T_range_K: [1670.0, 1677.0]` is exact.  
- Parent `[1667.0, 1673.0]` is wrong on both ends: **1667** appears only on Table **3b** FeNiCo row V68; **1673** as max excludes AD12 @ **1677**. Row-level `T_K` values were already correct pre-fix — the observation-level range was the P0.  
- NiO/MgO s.d.% tip matches the printed columns (OCR blanks MgO s.d. in `pdftotext`; confirmed on `pdftoppm` page + clean Ni crop).  
- Locator `page: 26` for 3a/3b and `page: 27` for 3c/3d matches published pagination.

**Verdict: CONFIRM P0 + companion P1s. Include Z10 tip in I2 for this extract.**

---

## 2. Thomas 2022 — AgI/Cl-024 Cl (Z12 / `c5c309379`)

**Source:** Thomas, Wade & Wood 2023, *Chem. Geol.* 617:121269.  
**PDF:** `thomas-2022-chlorine-bonding-silicate-melts.pdf` (Table 1 on PDF p.3 / published p.121271).

### Printed cells (re-read)

| Experiment | Cl wt% | 2σ |
| --- | ---: | ---: |
| **AgI/Cl-024** | **2.54** | **0.06** |
| AgI/Cl-025 | 2.70 | 0.19 |

- Parent stored Cl-024 as **2.70** with unc **0.06** (unc was already 024’s; value was 025’s) → classic adjacent-row copy.  
- Tip `Cl_wt_pct: 2.54`, `uncertainty_wt_pct: 0.06` matches PDF.  
- Ab/Fo-3 `rows_as_printed`: parent `0.32(0.05)` → tip `0.32(0.02)`; PDF K2O column is **0.32 (0.02)**. Structured Cl row for Ab/Fo-3 was already 2.75±0.05.

**Verdict: CONFIRM P0 Cl fix + P1 K2O unc. Include Z12 tip in I2 for this extract.**

---

## Notes / non-issues

- Co Table 3a `T_range_K` was already `[1670, 1677]` pre-fix (correct); only Ni needed the range correction.  
- Co Table 3a MgO s.d.% still uniformly 5 on tip (Z10 left as latent / not invented) — **out of VZ2 scope**; not overturning anything claimed here.  
- Burcat `'NO':` quote on the same Z10 commit is **not** this seat’s verify target (VZ backlog lists it under other VZ items).  
- No worktree left occupied by VZ2 (read-only use of existing tip checkouts `slot-y11-n11` / `slot-y7-n7`).

---

## Evidence paths

- PDF text: `/tmp/vz2/holzheid/full.txt`, `/tmp/vz2/thomas/full.txt`, `/tmp/vz2/thomas/raw.txt`  
- Rasters: `/tmp/vz2/png/holzheid-p6-06.png`, `/tmp/vz2/png/thomas-p3-03.png`  
- Tip YAMLs:  
  - `…/holzheid-1997-feo-nio-coo-activity-metal-saturated.yaml` @ `a8769e268`  
  - `…/thomas-2022-chlorine-bonding-silicate-melts.yaml` @ `c5c309379`

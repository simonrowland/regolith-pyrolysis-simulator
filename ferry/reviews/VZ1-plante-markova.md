# VZ1 — independently VERIFY Z P0 fixes (Plante-1979 composition + Markova-1984 Al2O3)

**Date:** 2026-09-23 ~00:18 EDT (America/Toronto)  
**Seat:** VZ1 (BACKLOG 5) — adversarial re-check of Z3 / Z7 fix commits (not the fixer)  
**Null hypothesis:** each claimed fix is a false positive (misread PDF, already correct, or wrong replacement).  
**PDFs:** `/workspace/ferry-inbox/from-main-B4-20260923T031628Z/audit-pdfs/`  
  - `kems-042-plante-1979.pdf` (sha256 `a46c2132d5741f63…`)  
  - `kems-026-markova-1984.pdf` (sha256 `4fbf0e84facb8d76…`)  
**Method:** fresh `pdftotext -layout`; `pdftoppm` 200–400 dpi of Plante PDF p.1 (abstract) + p.7 (pub. p.271 §3 Procedure) and Markova PDF p.2 (Tables 1–2); zoom Al₂O₃ row / sample-V column; parse tip YAML vs parent; recompute Markova oxide sum + Table 2 T=0 five-oxide recast. READ-ONLY on product. No new worktree claimed (inspected tip checkouts `slot-z3` / `slot-z7` in place). Scratch: `/tmp/vz1-plante/`, `/tmp/vz1-markova/`.

| Tip under verify | SHA | Branch |
| --- | --- | --- |
| Z3 Plante sample-start | `0e2b2dd69` | `empirical/z3-kems-042-plante-1979-2026-09-22` (on origin) |
| Z7 Markova Al2O3 | `5d0baf6c5` | `empirical/z7-markova-sauerborn-piacente-2026-09-22` (on origin) |

Base for both tips: `origin/work-v064-green` @ `2e9e17c3d`.

---

## TL;DR

1. **CONFIRM** Plante 1979 `printed_composition` → **crystalline K2Si2O5** (P0). Parent used abstract 43.9–11.9 wt% K₂O / Table 2 Series 1104 first-row `K2O: 43.94` + invented `SiO2: 56.06` as sample start; those are running / measured-range values, not the charge. Tip points §3 Procedure p.271; per-row `composition_wt_pct` unchanged.  
2. **CONFIRM** Markova 1984 Table 1 sample V `Al2O3` **19.29 → 18.29** (P0). Printed cell reads **18.29**; oxide sum closes to printed **100.10** only with 18.29; Table 2 right-panel T=0 five-oxide recast matches only with 18.29.  
3. **0 overturns.** No new fix branch. Both P0 tips are eligible for I2.

READY: `/workspace/ferry-inbox/reviews/VZ1-plante-markova.md`

---

## Verdict table

| claimed fix | exists pre-tip? | PDF printed cell | tip value | live wrong number? | your sev | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| Plante `printed_composition` → crystalline K2Si2O5 (refuse Table 2 / abstract) | **yes** — parent: experiment bag = abstract “43.9 to 11.9 wt% K2O”; **324** obs bags = Table 2 Series 1104 first-row `K2O: 43.94` / `SiO2: 56.06` with locator `table: '2'` | §3 Procedure **p.271**: “Crystalline K₂Si₂O₅ was prepared by the solid state reaction at 800 °C … x-ray diffraction pattern showed only K₂Si₂O₅.” Abstract + Results: 43.9–11.9 wt% K₂O is the **measured range** from integrated ³⁹K ion current; Table 2 “Wt % K₂O” is **running** melt composition | experiment + 324 bags = `crystalline K2Si2O5`; locator p.271 §3 with explicit Table 2 / abstract refusal | **yes** — sample-start composition fed consumers as if charge were 43.94 wt% K₂O | **P0** | **CONFIRM** |
| Markova Table 1 sample V `Al2O3` 19.29→18.29 | **yes** — parent `Al2O3: 19.29` | Table 1 (PDF p.2 / pub. p.510) sample V (alumina basalt) Al₂O₃ column: glyph reads **18.29**; printed column total **100.10** | tip `Al2O3: 18.29` + `corrections` entry | **yes** — sample-V start oxide in extract | **P0** | **CONFIRM** |

---

## 1. Plante 1979 — sample-start composition (Z3 / `0e2b2dd69`)

**Source:** E. R. Plante, NBS SP 561 (1979), pp. 265–281.  
**Extract:** `data/literature/extracts/kems-042-plante-1979.yaml`  
**PDF:** `kems-042-plante-1979.pdf` (17 PDF pp = published 265–281).

### Printed evidence (re-read)

| locus | printed | trap? |
| --- | --- | --- |
| Abstract (PDF p.1 / pub. 265) | “composition range **43.9 to 11.9** wt percent K₂O”; “composition variable was determined through use of the integrated ³⁹K ion current” | **yes** if treated as starting assay — it is the **measured campaign range** |
| §3 Procedure (PDF p.7 / pub. **271**) | “**Crystalline K₂Si₂O₅** was prepared by the solid state reaction at 800 °C of Brazilian Optical Quality SiO₂ … and anhydrous K₂CO₃ … x-ray diffraction pattern showed **only K₂Si₂O₅**.” | **true start** — no separate oxide assay printed |
| §3 / Results (pub. 270–272) | composition of evaporating solution from “known starting composition” + Σ I⁺√T; Table 2 lists “bulk K₂O weight percent in the melt” in order taken | Table 2 Wt % K₂O = **running** composition |

Stoichiometric K₂Si₂O₅ ≈ 43.9 wt% K₂O, so Series 1104 first-row `43.94` is the expected **initial running** value after melt — not an independent start assay, and inventing `SiO2: 56.06` as binary complement is not printed.

### Tip vs parent

| check | parent (`0e2b2dd69^`) | tip (`0e2b2dd69`) |
| --- | --- | --- |
| experiment `printed_composition` | abstract range “43.9 to 11.9 wt percent K2O” | `crystalline K2Si2O5` @ p.271 §3 |
| obs `equipment.sample.printed_composition` (324) | map `K2O: 43.94` / `SiO2: 56.06`, locator Table 2 | `crystalline K2Si2O5`, locator §3 Procedure + refusal note |
| per-row `composition_wt_pct` (324) | first five K₂O: 43.94, 43.93, 43.92, 43.84, 43.76 | **identical** (running compositions kept) |
| `SiO2: 56.06` as printed start | 325 | 1 (residual mention only; not sample-start) |

**Verdict: CONFIRM P0.** Include Z3 tip in I2 for this extract. No overturn.

---

## 2. Markova 1984 — Table 1 sample V Al₂O₃ (Z7 / `5d0baf6c5`)

**Source:** Markova, Yakovlev, Belov & Semenov 1984, LPSC XV 509–510.  
**Extract:** `data/literature/extracts/kems-026-markova-1984.yaml`  
**PDF:** `kems-026-markova-1984.pdf` (2 letter pp; Tables 1–2 on PDF p.2 = pub. 510).

### Printed Table 1 sample V (re-read)

| oxide | tip (post-fix) | notes |
| --- | ---: | --- |
| SiO2 | 50.01 | matches print |
| TiO2 | 0.95 | matches |
| **Al2O3** | **18.29** | **CONFIRMED on 400 dpi zoom of Al₂O₃ row, column V** (5th data column). Not 19.29. |
| FeO | 3.93 | matches |
| Fe2O3 | 5.59 | matches |
| MnO | 0.20 | matches |
| MgO | 5.95 | matches |
| CaO | 12.36 | matches |
| Na2O | 2.39 | matches |
| K2O | 0.42 | matches |
| **printed sum** | **100.10** | printed total row |

### Independent constraints (null = keep 19.29)

| constraint | with Al2O3=**18.29** | with Al2O3=**19.29** |
| --- | --- | --- |
| Σ ten oxides vs printed **100.10** | **100.09** (Δ −0.01) | **101.09** (Δ +0.99) |
| Five-oxide renorm (all Fe as FeO; Fe₂O₃→FeO ×0.8998) vs Table 2 V T=0 printed `52.32 / 19.13 / 9.37 / 6.25 / 12.93` | **52.33 / 19.14 / 9.38 / 6.23 / 12.93** (max |Δ| ≤ 0.02) | **51.79 / 19.98 / 9.28 / 6.16 / 12.80** (SiO₂ Δ −0.53; Al₂O₃ Δ +0.85) |

Parent stored **19.29** (fails both). Tip stores **18.29** + documents the correction. Damaged typewriter 8/9 noise is real on this scan, but the glyph + both hard constraints select **18.29**.

**Verdict: CONFIRM P0.** Include Z7 tip in I2 for `kems-026-markova-1984`. (Sauerborn / Piacente on the same branch were P0=0 in Z7 — out of VZ1 scope; not re-audited here.)

---

## Counts (verifier)

| class | claimed | verifier |
| --- | ---: | ---: |
| **P0 CONFIRM** | 2 | **2** |
| **P0 OVERTURN** | — | **0** |
| New fix branch | — | **none** |

## TL;DR (3 lines)

- **CONFIRMED P0: 2 / 2.** Plante crystalline K2Si2O5 sample-start and Markova sample V Al2O3=18.29 both match the printed cells and independent constraints.  
- **FALSE-POSITIVE (as fixes): 0.** Null hypothesis rejected for both.  
- Path: `/workspace/ferry-inbox/reviews/VZ1-plante-markova.md`

READY: /workspace/ferry-inbox/reviews/VZ1-plante-markova.md

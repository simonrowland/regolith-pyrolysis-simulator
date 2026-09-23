# VZ6 — independently VERIFY Z18 USGS Table A8.2 P0 fixes (+ Kato/Robinot spot)

**Date:** 2026-09-23 ~00:51 EDT (America/Toronto)  
**Seat:** VZ6 (BACKLOG 5) — adversarial re-check of Z18 tip (different seat than fixer)  
**Null hypothesis:** claimed USGS cell repairs (and Kato/Robinot P0=0) are false positives.  
**Fix tip under verify:** `11e6cf3e32626501a9193e04e822124b06f6f2f5` on `origin/empirical/z18-kato-usgs-robinot-2026-09-23`  
**Base:** `origin/work-v064-green` = `2e9e17c3d`  
**Fixer write-up:** `/workspace/ferry-inbox/reviews/Z18-kato-usgs-robinot.md`  
**PDFs (not in git):** `/workspace/ferry-inbox/from-main-B5-20260923T041100Z/audit-pdfs/`  
  - `usgs-lunar-sourcebook-tab8-2.pdf`  
  - `kems-049-kato-1993-ms-review.pdf`  
  - `kems-044-robinot-2026.pdf`  
**Method:** READ-ONLY on product. Fresh `pdftotext -layout` + independent column-nearest parse of all 10 data pages; 250–300 dpi crops for Li A11 / A14 BX / Norite Lu / Kato Table 1 + §2.3; tip YAML vs parent `^` for cited cells. Scratch: `/workspace/ferry-inbox/reviews/_vz6_audit/`. **No force-push. No correcting commit** (0 overturns).

| Claim | Scope | This seat |
| --- | --- | --- |
| USGS 321 P0 cell repairs | digit-strip / column-bleed / zero-pad | stratified ≥30 + cited Li pin vs PDF |
| Kato P0=0 | spot 5 obs | Table 1 Fe/S/Si/W + §2.3 Al model |
| Robinot P0=0 | spot 5 obs | yield / mass balance / oxides / model |

---

## TL;DR

1. **CONFIRM** Li Apollo 11 MBAS average **`.5` → `18.5`** (P0). Parent tip-1 had `.5`; PDF Table A8.2 p.1 Average/Li prints **18.5**; tip YAML `18.5`.  
2. **CONFIRM 37/37** stratified USGS cells (7 write-up examples + 30 stratified across digit-strip / column-bleed / zero-pad) — tip == claimed new == independent PDF parse.  
3. **CONFIRM 5/5 Kato** and **CONFIRM 5/5 Robinot** — 0 overturns (matches fixer P0=0 claim).  
4. **0 overturns overall.** Ledger nuance only: raw `p0_fixes.json` has **321 lines / 320 unique keys** (Lu Norite N listed twice: intermediate `new=3` then `manual_pdf_column_fix` → **13**). Tip + PDF = **13**. Not an overturn of product.

READY: `/workspace/ferry-inbox/reviews/VZ6-usgs-tab8-2.md`

---

## Counts

| Bucket | CONFIRM | OVERTURN |
| --- | ---: | ---: |
| USGS cited Li A11 MBAS avg | **1** | **0** |
| USGS stratified sample (≥30) | **37** | **0** |
| Kato spot | **5** | **0** |
| Robinot spot | **5** | **0** |
| **TOTAL** | **48** | **0** |

**Tip:** `11e6cf3e32626501a9193e04e822124b06f6f2f5`  
**I2:** USGS fix tip eligible; Kato + Robinot need no product commit.

---

## 1. Cited pin — Li Apollo 11 MBAS average

| locus | parent (`11e6cf3e3^`) | tip (`11e6cf3e3`) | PDF print |
| --- | --- | --- | --- |
| `species.Li…rows` Apollo 11 MBAS / average | **`.5`** | **`18.5`** | Table A8.2 PDF p.1 Average row, Li column: **18.5** (pdftotext + 300 dpi crop) |

Also visible same block: K N **15**, K min **500**, K max **2500** (parent had `15 1` / `00` / `00`).

**Verdict: CONFIRM P0.**

---

## 2. Stratified USGS sample (n=37)

Independent `pdftotext -layout` → column-center token assigner on pages 1–10 (FRONT Li…Eu / BACK Gd…U). Compared tip YAML `value_as_printed` to claimed `p0_fixes.json` `new` and to this parse (not the fixer’s `reparsed_cells.json` as sole oracle). Seed `6060623` (different seat).

| class (mapped) | n in sample | result |
| --- | ---: | --- |
| digit-strip (`leading_digit_restore`) | 14 | all CONFIRM |
| column-bleed (`bleed_trim` / `bleed_replace_with_pdf` / manuals) | 14 | all CONFIRM |
| zero-pad (`zero_pad_restore`) | 8 | all CONFIRM |
| write-up examples (subset of above) | 7 | all CONFIRM |

### Write-up examples (re-checked)

| element | sample_group | statistic | old → new | tip | indep PDF | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| Li | Apollo 11 MBAS | average | `.5` → `18.5` | 18.5 | 18.5 | **CONFIRM** |
| K | Anorthosite | average | `23` → `123` | 123 | 123 | **CONFIRM** |
| K | Apollo 11 MBAS | N | `15 1` → `15` | 15 | 15 | **CONFIRM** |
| Be | Apollo 14 BX | maximum | `12 79` → `12` | 12 | 12 (visual crop) | **CONFIRM** |
| K | Apollo 11 MBAS | minimum | `00` → `500` | 500 | 500 | **CONFIRM** |
| K | Apollo 11 MBAS | maximum | `00` → `2500` | 2500 | 2500 | **CONFIRM** |
| Lu | Norite | N | `3`→`13` (final ledger) | 13 | 13 (p.7 Norite N: Yb/Lu both **13**) | **CONFIRM** |

### Extra stratified pins (examples)

| class | cell | old → new | PDF |
| --- | --- | --- | --- |
| digit-strip | Ba Apollo 11 MBAS maximum | `60`→`260` | 260 |
| digit-strip | Zr Apollo 17 S&RB average | `30`→`230` | 230 |
| column-bleed | Y Apollo 11 S&RB maximum | `131 3`→`131` | 131 |
| column-bleed | Cs Apollo 14 BX maximum | `0 1`→`2000` | 2000 (Maximum row Cs) |
| zero-pad | Zr Apollo 16 BX maximum | `00`→`2100` | 2100 |
| zero-pad | K Apollo 17 BX maximum | `00`→`2700` | 2700 |

Full sample JSON: `_vz6_audit/usgs/stratified_indep.json`.

Integrity (unique fix keys): tip matches claimed `new` for **320/320**; independent parse hit **319/320** (miss was Y Apollo 17 S&RB minimum — PDF label typo **`Minumum`**, value **44** under Y; tip `44` — visual/text CONFIRM, not overturn).

---

## 3. Kato spot (5) — expect 0 overturns

PDF: ScanSnap, page rot; Table 1 on pub. p.302 (landscape). Tip branch values vs render:

| # | observation | tip | PDF | OK? |
| ---: | --- | --- | --- | --- |
| 1 | Table 1 Fe Psat | **8.0** Pa @ 1873 K | Table 1 Fe **8.0** | yes |
| 2 | Table 1 S (1 mass%) | **1.7** Pa | S **1.7** (1 mass%) | yes |
| 3 | Table 1 Si | **8.8×10⁻¹** Pa | Si **8.8×10⁻¹** | yes |
| 4 | Table 1 W | **6.7×10⁻¹¹** Pa | W **6.7×10⁻¹¹** | yes |
| 5 | §2.3 Al₂O₃-cell model | log₁₀(pO₂/MPa)=**−1.27**; a_Al=**3.8×10⁻⁶**; a_Al@a_V₂O₃=0.1 → **1.2×10⁻⁵** | p.300 prose same | yes |

**Verdict: 5 CONFIRM / 0 overturn.** (P spot not required; S/P 1 mass% pairing and Mn **5.2×10³** also consistent with Table 1 ranking column.)

---

## 4. Robinot spot (5) — expect 0 overturns

Fresh `pdftotext -layout` on `kems-044-robinot-2026.pdf`:

| # | observation | tip | PDF | OK? |
| ---: | --- | --- | --- | --- |
| 1 | O₂ yield | **35 mg**, **1.05 %**, **2.47 %**, **31 mg/kWh**, sample **3.38 g** | abstract + §4.1 | yes |
| 2 | Mass balance prose | glass **1.82 g**; captured **1.1 g**; unaccounted **0.27 g**; recovery **92 %** | §4.1 | yes |
| 3 | Fig.4c bar labels | 1.82 / 0.2 / 0.35 / 0.2 / 0.51 / 0.035 / 0.265 (sum 3.38) | stored labels (compete with prose; not averaged) | yes |
| 4 | EAC-1 oxides (Šeško) | SiO₂ **44.41**; Na₂O **2.95**; O content **44.41** wt% | p.3 composition | yes |
| 5 | HSC / comparable | model **1.37 %**; comparable run **1.17 %** | §4.3 / §4.1 | yes |

**Verdict: 5 CONFIRM / 0 overturn.**

---

## Product / process

- No overturn → **no correcting commit** on a vz6 branch.  
- Inspected tip in place at `/workspace/repos/wt/slot-z4` (fixer worktree); did not force-push.  
- No PDFs committed.  
- Dropbox STATUS staged beside this review for Mac ferry.

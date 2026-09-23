# Z18 FIDELITY AUDIT — kato-1993 / usgs-tab8-2 / robinot-2026

**Date:** 2026-09-23 (America/Toronto, EDT)  
**Seat:** Z18 — BACKLOG 5, three preferred remaining (skip Z13–Z17)  
**Repo / branch:** `regolith-empirical` @ `empirical/z18-kato-usgs-robinot-2026-09-23`  
**Base:** `origin/work-v064-green` = `2e9e17c3d138fdfba9269c493f82974a71153fa5`  
**Tip:** `11e6cf3e32626501a9193e04e822124b06f6f2f5`  
**Worktree:** `/workspace/repos/wt/slot-z4`  
**PDFs:** `/workspace/ferry-inbox/from-main-B5-20260923T041100Z/audit-pdfs/` (never committed)  
**Rule:** P0 = wrong number stored that can reach a result/score/ledger today. Fix P0 on branch. Latent ≤ P1. No invented data.

## Candidate selection

Prefer list (obs count on `data/literature/extracts/*.yaml`; skip Z13–Z17 write-ups / in-flight seats):

| Extract | Obs | Disposition |
| --- | ---: | --- |
| `kems-049-kato-1993-ms-review` | 26 | **AUDITED** |
| `usgs-lunar-sourcebook-tab8-2` | 25 (2185 table cells) | **AUDITED + P0 FIXED** |
| `kems-044-robinot-2026` | 19 | **AUDITED** |
| kems-087-yamada-kato-1980 | 16 | deferred |
| kems-088-ichise-1975 | 16 | deferred |
| ts1985 | 16 | deferred |
| usgs-lunar-sourcebook-tab8-4 | 0 qty | skip (no numeric observations) |
| miller / markova / halwax | — | **SKIP** (Z15) |
| kambayashi / ohara / ichise-1977 | — | **SKIP** (Z16) |

## Verdict

| Extract | Obs (YAML) | Sampled | P0 mismatches | Coverage gaps | Fix commit |
| --- | ---: | ---: | ---: | --- | --- |
| kems-049-kato-1993-ms-review | 26 | **ALL 26** | **0** | Figs 3,5–8 figure-only (already admitted); Tables 2–5 bibliographic → deepening | none |
| usgs-lunar-sourcebook-tab8-2 | 25 obs / 2185 cells | **ALL 2185 cells** vs pdftotext-layout + column reparse; fidelity pins | **321 cell repairs** (leading-digit strip, column-bleed, zero-pad) | Sparse blanks remain blank (not invented); AMET S&RB / some Norite edge cells not in reparse map left untouched when ambiguous | yes (this branch) |
| kems-044-robinot-2026 | 19 | **ALL 19** + Fig.4c bar labels vs page render | **0** | Figs 1,4a,4b,5,8 figure-only / no printed point table (already admitted); SI not in corpus PDF | none |

**Overall: PASS after USGS P0 fix. Kato + Robinot clean. Pushed FF.**

---

## Method

1. `pdftotext -layout` under `/workspace/ferry-inbox/reviews/_z18_audit/{kato,usgs82,robinot}/txt/`.
2. Kato is a ScanSnap scan (empty text layer): `pdftoppm -png -r 200` raw pages (viewer rotation already applied); Table 1 + §2.3 crops read visually.
3. USGS: character-column reparse of all 11 PDF pages → `_z18_audit/usgs82/json/reparsed_cells.json`; every extract cell compared; high-confidence mismatches repaired.
4. Robinot: text-layer checks for all printed yields / oxides / mass-balance; Fig.4c bar labels checked on page render (PDF p.9 / article p.8).
5. Fields checked: **value / uncertainty / unit / basis / sign / T / locator / statement_as_published** vs PDF.
6. `tools/validate_literature_extracts.py` on the three YAML paths — **OK**.

---

## 1. kems-049-kato-1993-ms-review (Kato 1993 J. Mass Spectrom. Soc. Jpn. review)

**PDF:** 20 pp. official scan, page rot 180 (pdftoppm upright). Published pp. 297–316.

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 20 (Fe, S, P, Mn, Al, Sn, Cu, Ge, Cr, Au, Pd, Co, Ni, Si, Ti, V, Mo, Nb, Ta, W) |
| Observations | **26** (19× Table-1 `psat_series` + 1× Al model `activity_coefficient` + 5× figure-only + 1× Ge phase figure-only) |
| fidelity_samples | 3 (Fe Psat, Si Psat, Al log pO₂) |

### Sample = ALL 26

| Check | Extract | PDF | OK? |
| --- | --- | --- | --- |
| Table 1 Fe Psat | **8.0 Pa** @ 1873 K | Table 1 p.302 Fe **8.0** | yes |
| Table 1 S / P (1 mass%) | **1.7** / **1.4×10⁻⁴** Pa | same | yes |
| Table 1 Mn…W grid | 5.2×10³ … 6.7×10⁻¹¹ | all 19 ranking pressures match render | yes |
| §2.3 Al₂O₃-cell model | log₁₀(pO₂/MPa)=**−1.27**; a_V=0.1; a_V₂O₃=1; a_Al=**3.8×10⁻⁶**; a_Al@a_V₂O₃=0.1 → **1.2×10⁻⁵** | p.300 prose | yes |
| Figs 3,5–8 | `admission_status: figure_only` | no axis digitisation | yes |

### Coverage

- Stored: Table 1 pure-element (or 1 mass% S,P) ranking Psat; Furukawa–Kato Al₂O₃-cell illustration; figure-only admissions for reprinted Fe–V / Fe–P / Fe–Mo / Fe–Ge plots.
- Not extracted (acceptable): Tables 2–5 investigator bibliographies → `deepening`; no new Kato primary series in this review paper.

### P0 / P1

- **P0: 0**
- **P1:** Psat column is `quoted_unattributed` ranking (already labelled); Mn liquid bracket *[1.0×10⁴]* on Table 1 not stored as a second row (latent coverage, not a wrong number).

---

## 2. usgs-lunar-sourcebook-tab8-2 (Lunar Sourcebook Table A8.2)

**PDF:** 11 pp. USGS extract of Table A8.2 (incompatible-trace statistics, µg/g).

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 25 (Li…U) |
| Observations | **25** (`gibbs_table` / compilation statistics, one per element) |
| Table cells | **2185** `value_as_printed` rows |
| fidelity_samples | 4 (Li avg; Eu/Gd/U N) |

### Sample = ALL 2185 cells

Automated column-aware reparse of pdftotext `-layout` vs every stored cell, plus visual/PDF spot checks on Apollo 11–17 MBAS / S&RB / BX blocks.

### P0 found (pre-fix)

Systematic transcription damage:

1. **Leading-digit strip** on Li and other columns (e.g. Apollo 11 MBAS Li average `.5` vs PDF **18.5**; K Anorthosite average `23` vs **123**).
2. **Column bleed** into `value_as_printed` (whitespace-joined tokens, e.g. K N `15 1` vs **15**; Be BX maxima `12 79` vs **12**).
3. **Zero-pad collapse** (K Apollo 11 MBAS min/max `00`/`00` vs **500**/**2500**).

Fidelity pin Li Apollo 11 MBAS average was itself wrong (`.5` → **18.5**).

### Fix

- **321** cell repairs on branch (ledger: `_z18_audit/usgs82/json/p0_fixes.json`).
- Reasons: `leading_digit_restore` / `bleed_trim` / `bleed_replace_with_pdf` / `zero_pad_restore` / manual Lu Norite N **13**.
- Ambiguous parser-conflict keys left unchanged (not invented).
- Post-fix: no remaining whitespace / leading-dot / `00` values; validator **OK**.

### Coverage

- Full Table A8.2 statistical grid for 25 incompatible elements as compilation tables.
- Not a primary experiment (already `secondary_compilation` / `compilation_assessed`).

### P0 / P1

- **P0: 321 repaired** (wrong printed numbers that could score/ledger).
- **P1:** possible residual sparse-cell omissions where PDF blank and extract absent; Norite/Troctolite header alignment remains delicate on p.2/p.7 — spot-checked repaired cells against layout text.

---

## 3. kems-044-robinot-2026 (Robinot et al. 2026 Adv. Space Res. / HAL)

**PDF:** 15 pp. HAL article-in-press (cover = PDF p.1). Solar vacuum pyrolysis of EAC-1; O₂ trace analyser (not KEMS).

### Inventory

| Layer | Count |
| --- | ---: |
| Species | O₂ + deposit elements (Na, K, Fe, Si, Mg, Al, Ca, Ti) + EAC-1 composition carrier |
| Observations | **19** |
| fidelity_samples | 4 (35 mg; 1.05 %; glass 1.82 g; Na₂O 2.95 %) |

### Sample = ALL 19

| Check | Extract | PDF | OK? |
| --- | --- | --- | --- |
| O₂ yield | **35 mg**, **1.05 %**, **2.47 %** of feedstock O, **31 mg/kWh** | abstract + §4.1 | yes |
| Sample / T / P / flux | **3.38 g**; ~**1800 °C**; fill **10 mbar** / run ~**13 mbar**; **4.8 MW/m²**; Ar **0.3 NL/min** | §3–4 | yes |
| Mass balance prose | glass **1.82 g**; captured **1.1 g**; unaccounted **0.27 g**; recovery **92 %**; conclusion vaporized **1.3 g** (competing, not averaged) | §4.1 + conclusion | yes |
| Fig.4c bar labels | glass **1.82**; holder **0.2**; window **0.35**; condenser **0.2**; filter **0.51**; O₂ **0.035**; discrepancy **0.265** (sum 3.38) | page render | yes |
| EAC-1 oxides (Šeško) | SiO₂ **44.41**; Al₂O₃ **12.8**; Fe₂O₃ **12.2**; MgO **12.09**; CaO **10.98**; Na₂O **2.95**; …; O content **44.41** wt% (same numeral as SiO₂, both printed) | p.3 | yes |
| HSC expected / comparable | **1.37 %** model; **1.17 %** comparable run | §4.3 / §4.1 | yes |
| Fig.8 astronaut | **0.84 kg O₂/day**; **80 kg** regolith @ 1.05 % | prose | yes |
| Figure-only admissions | Figs 1,4a,4b,5,8 | stand | yes |

### Coverage

- Primary measured O₂ yield, mass balance, quoted EAC-1 composition, qualitative EDS/XRD deposit notes, model/figure admissions.
- SI supplementary plot not in corpus PDF (already noted).

### P0 / P1

- **P0: 0**
- **P1:** chamber pressure 10 vs ~13 mbar retained as competing printed values (already documented); SiO₂ and oxygen both 44.41 wt% is as printed, not a unit error.

---

## Commands

```bash
pdftotext -layout …/kems-044-robinot-2026.pdf _z18_audit/robinot/txt/full.txt
pdftotext -layout …/usgs-lunar-sourcebook-tab8-2.pdf _z18_audit/usgs82/txt/full.txt
pdftoppm -png -r 200 …/kems-049-kato-1993-ms-review.pdf _z18_audit/kato/png/raw
uv run python tools/validate_literature_extracts.py \
  data/literature/extracts/kems-049-kato-1993-ms-review.yaml \
  data/literature/extracts/usgs-lunar-sourcebook-tab8-2.yaml \
  data/literature/extracts/kems-044-robinot-2026.yaml
git push -u origin empirical/z18-kato-usgs-robinot-2026-09-23
```

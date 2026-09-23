# Z10 FIDELITY AUDIT — lange / burcat / holzheid

**Date:** 2026-09-22 (America/Toronto, EDT)  
**Seat:** Z10 (BACKLOG 4) — three extracts, one seat  
**Repo / branch:** `regolith-empirical` @ `empirical/z10-lange-burcat-holzheid-2026-09-22`  
**Base:** `origin/work-v064-green` = `2e9e17c3d`  
**Tip:** `a8769e268` (one fix commit)  
**Worktree:** `/workspace/repos/wt/slot-y11-n11` (recycled free slot)  
**PDFs:** `/workspace/ferry-inbox/from-main-B4-20260923T031628Z/audit-pdfs/` (never committed)  
**Rule:** P0 = wrong number stored that can reach a result/score/ledger today. Fix P0 on branch. Latent ≤ P1. No invented data.

## Verdict

| Extract | Obs (YAML) | Sampled | P0 mismatches | Coverage gaps | Fix commit |
| --- | ---: | ---: | ---: | --- | --- |
| ammin-76-904-lange-1991 | 5 obs + 1 context | **ALL** (6) | **0** | none material | none |
| burcat-third-millennium | 37 | **ALL 37** | **0** (after fix) | intro Tables 1–3 only (not BURCAT.THR polynomials) | `a8769e268` (`'NO':` quote + v2 formula) |
| holzheid-1997-feo-nio-coo-activity-metal-saturated | 9 | **ALL 9** (+ full Table 3a/3b row grids) | **1 fixed** (NiO `T_range_K`) | Co Table 3a MgO s.d. column still uniform 5 (OCR-dropped; not invented); metal-phase wt% on 3b not row-extracted; E2 AD/BK experiment split not on this base | `a8769e268` (NiO s.d./MgO s.d., FeO MgO s.d., Table 3 pages) |

**Overall: PASS after fix — P0 cleared on tip. Pushed FF.**

---

## Method

1. `pdftotext -layout` + selective `pdftoppm` rasters under `/workspace/ferry-inbox/reviews/_z10_audit/`.
2. Full dump of `data/literature/extracts/{ammin,burcat,holzheid}*.yaml` → JSON scratch.
3. Fields checked per sample: **value / unit / basis / sign / conditions (T, fO2) / locator** vs PDF.
4. `uv run python tools/validate_literature_extracts.py` on burcat + holzheid → **OK** after edits. Lange has pre-existing schema hygiene FAILs on tip (source_id stem, absolute provenance_path, `quoted_comparator` type, missing fidelity_samples) — not introduced here; numeric fidelity unaffected.

---

## 1. ammin-76-904-lange-1991 (Lange, De Yoreo & Navrotsky 1991, Am. Mineral. 76:904–912)

**PDF:** 9 pp. Primary step-scan calorimetry on diopside.

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 1 (`CaMgSi2O6`) |
| Observations | 5 |
| Context | 1 (Table 1 composition) |
| Experiments | 0 |

### Sample = ALL

| Observation | Locator | Key checks vs PDF | OK? |
| --- | --- | --- | --- |
| `…_table2_step_scan_heat_capacity_enthalpy` | pub p.908 / PDF p.5 Table 2 | All **29** series rows (T, Cp meas/calc/%, H−H298 meas/calc/%) match Table 2; T_range 1403–1762 K; apparatus masses 1.30602 / 0.94967 g; Au calib 1337 K | yes |
| `…_fusion_and_melting_transition` | Abstract p.904 | onset 1606 K; interval 1606–1665; prior mp 1665; first 20% / 80% intervals 44 / 15 K; ΔfusH **137.7 ± 2.4** kJ/mol; Cp scatter 5% / 12%; heat-content agreement 0.3% | yes |
| `…_crystallization_repeated_runs` | Fig.6 / p.910 | start 1691 K; 10 K/h; peaks 1576 / 1534 / 1566 K; earlier 30 K/h from 1715 → 1545 K; no forsterite heat effect | yes |
| `…_author_regression_liquid_heat_capacity` | Eq.6 p.909 | liquid Cp 312 ± 10; combined 334 ± 7 (more accurate unc. ~20) | yes |
| `…_quoted_comparator_enthalpy_values` | p.909 | Stebbins 138.1; Richet&Bottinga 137.7 / 334; Ferrier 128; Stebbins 1984 353; Adamkovicova wollastonite 57.3 @ 1821 K | yes |
| context Table 1 composition | p.905 | crystal/glass/ideal wt% + formula atoms match Table 1 (SiO2 55.75(0.64) etc.) | yes |

**P0: 0. Coverage:** full Table 2 landed; no vacuum/fO2 (correctly absent).

---

## 2. burcat-third-millennium (Burcat & Ruscic 2005 / 2017 intro, ANL-05/20)

**PDF:** 418 pp. Extract covers **printed Tables 1–3 only** (ΔfH298 exemplars), not the BURCAT.THR NASA-7 polynomial harvest (`compilations/burcat/`).

### Inventory

| Layer | Count |
| --- | ---: |
| Species / observations | **37** |
| Table 1 (ATcT key gases) | 12 |
| Table 2 (hydrocarbons bomb/flame) | 5 |
| Table 3 (radicals) | 20 |
| fidelity_samples | 3 (O, CH4, OH) |

### Sample = ALL 37 (value + unc + unit kJ/mol + phase gas + locator page/table)

**Table 1 (p.13)** — all match PDF:

| Species | ΔfH298 | unc | OK? |
| --- | ---: | ---: | --- |
| C | 717.065 | 0.146 | yes |
| H | 217.997 | 0.0001 | yes |
| O | 249.229 | 0.002 | yes |
| N | 472.459 | 0.044 | yes |
| S | 277.17 | 0.25 | yes |
| Cl | 121.302 | 0.001 | yes |
| NO | 91.097 | 0.084 | yes (after key fix) |
| CO | −110.538 | 0.026 | yes |
| H2O | −241.815 | 0.031 | yes |
| CO2 | −393.472 | 0.014 | yes |
| SO2 | −296.84 | 0.21 | yes |
| NO2 | 34.025 | 0.085 | yes |

**Table 2 (p.14)** bomb / flame — all match (CH4 −74.85/−74.48 … i-C4H10 −135.60/−134.18).

**Table 3 (p.15)** — all 20 radicals match (OH 37.34 … C6H5 339.7 ± 2.5), including large unc on C2O (±63).

### P0 / fix

- **Pre-fix:** unquoted YAML species key `NO:` parsed as boolean `False` → extracts-v2 `formula: "False"` (identity corruption). Numeric ΔfH was correct; v2 values are `unavailable` (unsupported quantity `delta_fH298`), so **not a live wrong number in score today** → classified **P1**, fixed anyway because nasa-cea already quotes `'NO':` and identity would poison any future quantity map.
- **Fix:** `data/literature/extracts/burcat-third-millennium.yaml` → `'NO':`; extracts-v2 formula → `NO`.

**Coverage:** intentional subset (intro tables). Full BURCAT.THR polynomials live under `data/literature/compilations/burcat/` (out of this extract’s claim).

---

## 3. holzheid-1997-feo-nio-coo-activity-metal-saturated (Holzheid, Palme & Chakraborty 1997, Chem. Geol. 139:21–38)

**PDF:** 18 pp. (published pages 21–38).

### Inventory

| Layer | Count |
| --- | ---: |
| Species keys | 7 |
| Observations | 9 |
| Experiments | 1 (`variable-mgo-equilibration-series`, categorical composition on this base — E2 AD/BK split is on another branch) |
| fidelity_samples | 3 (V69 CoO 2.03 / NiO 0.17 / FeO 26.0) |

### Sample = ALL observations + full Table 3a Ni / 3b Fe row grids

| Observation | Locator (after fix) | Checks | OK? |
| --- | --- | --- | --- |
| Table 1 starting compositions | p.22 T1 | AD/BK SiO2 CaO MgO Al2O3 FeO wt% exact (50.9… / 49.1…) — agrees prior X2 | yes |
| Table 3a Co (8 rows) | p.26 | CoO/MgO/γ/T/log fO2/duration match pdftotext; **MgO_sd_pct still all 5** (PDF MgO s.d. column not recovered for 3a Co; see P1) | primary yes |
| Table 3a Ni (10 rows) | p.26 | Primary NiO/MgO/γ/T/fO2 match; **fixed** NiO_sd (6/10/9…), MgO_sd (1…7/9), **T_range 1670–1677** (was 1667–1673, excluded AD12 @ 1677) | yes after fix |
| Table 3b FeNiCo (7 rows) | p.26 | Primary CoO/NiO/FeO/MgO/γ match; **fixed** MgO_sd on rows 1–5 (5→1); metal-phase Co/Ni/Fe wt% columns not extracted | yes after fix |
| Table 3c BK Co (4) | p.27 | Full silicate+metal+γ rows match p.7 text | yes |
| Table 3d BK Ni (4) | p.27 | Match (incl. V67 75.3 h, NiO 0.171, γ_NiO 2.74) | yes |
| Eqs 7–9 ΔG fits | p.29 | −185092+99.844T−4.898T ln T (NiO); CoO / FeO analogues exact | yes |
| Table 4 summary | p.29 | average_all_data γ CoO 1.51±0.28, NiO 2.70±0.52, FeO 1.70±0.22 + SEM | yes |
| Abstract ranges | p.21 | γ 2.7/1.51/1.7; n=77/76/57; T 1300–1600 °C; IW+1.5…−3; FeO 0–12; MgO 4–30 | yes |

### P0 (fixed)

1. **NiO Table 3a `T_range_K: [1667, 1673]`** — wrong vs printed row temperatures (1670–1677 K; AD12 @ 1677 excluded). Conditions field that can gate consumers → **P0**. Set to `[1670.0, 1677.0]`.

### P1 (fixed or noted)

- **NiO_sd_pct / MgO_sd_pct** on Table 3a Ni: systematic default-to-5 vs printed s.d.% (image-confirmed). Uncertainty metadata; fixed with T_range.
- **FeO Table 3b MgO_sd_pct** first five rows: printed 1, stored 5; fixed.
- **Locator pages:** Table 3a/3b were `page: 25` (prose cites Table 3 there); table body is published **p.26**; 3c/3d on **p.27**. Updated locators + fidelity_sample pins + extracts-v2 pages.
- **Co Table 3a MgO_sd_pct** still uniformly 5: PDF column not reliably recovered by `pdftotext` (blank gaps); **not invented** replacements — left as **latent P1 / suspect**, same default pattern that was wrong on Ni/FeO when recoverable.
- **Coverage:** Table 3b metal-phase Co/Ni/Fe wt% not in row maps; Table 2 prior-run grids not extracted (scope: “new experimental results” Table 3 + summaries). Base still has categorical experiment composition (E2 AD/BK split is elsewhere).

### fidelity_samples

Pins V69 CoO 2.03 / NiO 0.17 / FeO 26.0 match PDF Table 3a/3b first rows (pages updated to 26).

---

## Validation

```
uv run python tools/validate_literature_extracts.py \
  data/literature/extracts/burcat-third-millennium.yaml \
  data/literature/extracts/holzheid-1997-feo-nio-coo-activity-metal-saturated.yaml
→ OK: 2 extract file(s) valid
```

Lange: pre-existing validator hygiene FAILs on tip (not part of this seat’s numeric P0).

---

## Push

- Branch: `empirical/z10-lange-burcat-holzheid-2026-09-22`
- Tip: **`a8769e268d74bf8ae5bb9f90ba86af0f9130981c`**
- Message: `extracts: Z10 fidelity fixes for burcat NO key and Holzheid Table 3`
- Files: burcat extracts + extracts-v2; holzheid extracts + extracts-v2 locator/pages + Table 3 s.d./T_range
- **FF push:** `origin/empirical/z10-lange-burcat-holzheid-2026-09-22` @ `a8769e268`
- PDFs not committed

**Verdict: PASS (P0=0 on tip after fix).**

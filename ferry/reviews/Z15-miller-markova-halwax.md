# Z15 FIDELITY AUDIT — miller-armatys-2013 / markova-1983 / halwax-2024

**Date:** 2026-09-23 (America/Toronto, EDT)  
**Seat:** Z15 — BACKLOG 5, three extracts (retry after prior dispatch blocked)  
**Repo / branch:** `regolith-empirical` @ `empirical/z15-miller-markova-halwax-2026-09-23`  
**Base:** `origin/work-v064-green` = `2e9e17c3d138fdfba9269c493f82974a71153fa5`  
**Tip:** `2e9e17c3d138fdfba9269c493f82974a71153fa5` (unchanged; no P0 fix commit)  
**Worktree:** `/workspace/repos/wt/slot-z15`  
**PDFs:** `/workspace/ferry-inbox/from-main-B5-20260923T041100Z/audit-pdfs/` (never committed)  
**Rule:** P0 = wrong number stored that can reach a result/score/ledger today. Fix P0 on branch. Latent ≤ P1. No invented data.

## Candidate selection

Prefer list (obs count on `data/literature/extracts/*.yaml`; skip ta-badro / fegley-2023 / kems-027 / kems-045 / Z16 in-flight kems-057/058/066):

| Extract | Obs | Disposition |
| --- | ---: | --- |
| `kems-019-miller-armatys-2013` | 28 | **AUDITED** |
| `kems-025-markova-1983` | 22 | **AUDITED** |
| `kems-031-halwax-2024` | 22 | **AUDITED** |
| kems-028-yakovlev-1984 | 19 | deferred |
| kems-003-pound-1972 | 18 | deferred |
| kems-011-wetzel-gail-2013 | 17 | deferred |
| ts1985 | 16 | deferred |
| kems-002-ohno-1967 | 12 | deferred |

## Verdict

| Extract | Obs (YAML) | Sampled | P0 mismatches | Coverage gaps | Fix commit |
| --- | ---: | ---: | ---: | --- | --- |
| kems-019-miller-armatys-2013 | 28 | **ALL 28** (+5 fidelity pins) | **0** | Supp. Tables 1–7 true-absence (declared); numeric KEMS in cited primaries not in held PDF | none |
| kems-025-markova-1983 | 22 | **ALL 22**; composition table **ALL 40** oxide+total cells | **0** | Figs 1–4 lg Pᵢ curves figure-only (declared); PO/PO₂/TiO₂/O/O₂/Al₂O₂/MgO(?) measured-not-tabulated | none |
| kems-031-halwax-2024 | 22 | **ALL 22**; Tables I–VII numeric grids sampled ≥40 cells | **0** | Figs 8–13 Psat Arrhenius figure-only; Figs 14–15 FactSage model (declared) | none |

**Overall: PASS — no P0. No extract patch committed. Pushed FF (empty tip = green base).**

---

## Method

1. `pdftotext -layout` under `/workspace/ferry-inbox/reviews/_z15_audit/{miller,halwax}/` (Markova scan has no usable text layer).
2. `pdftoppm -png` 200–300 dpi for Markova pp.460–461 and table crop; visual + tesseract cross-check of composition grid.
3. Dump all species observations → JSON; cell-by-cell / quote-by-quote vs PDF.
4. Fields checked: **value / uncertainty / unit / basis / sign / T / locator / statement_as_published** vs PDF.
5. `validate_literature_extracts.py` **not re-run** (no extract edits).

---

## 1. kems-019-miller-armatys-2013 (Miller & Armatys 2013, Open Ceramics review)

**PDF:** 9 pp. Word→PDF text layer good. Held PDF is the review body; Supplementary Tables 1–7 are **true-absence** (already admitted).

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 7 (`Al`, `Ca`, `Fe`, `K`, `Na`, `SiO`, `Ti`) |
| Observations | **28** (mostly `vapour_species_map_no_numeric_pressures` + method windows + qualitative α) |
| fidelity_samples | 5 |

### Sample = ALL 28

| Observation / pin | Locator | Key checks vs PDF | OK? |
| --- | --- | --- | --- |
| `…_silica_vapour_species` (+ quoted) | p.7 §4 | **SiO(g), O2/O(g)**; high stability of SiO | yes |
| `…_al2o_major_al_species` (+ quoted) | p.7 | **Al2O(g)** major; minor **CaAlO(g)** | yes |
| `…_gaseous_silicates_minor` (+ quoted) | p.7 | **CaSiO3, BaSiO3, SrSiO, and AlSiO** minor | yes |
| `…_alkaline_earth_borate_vapour` / alkali borate | p.7 | **ABO2**, **Ca(BO2)2, SrBO2, BaBO2**, dimers **A2(BO2)2**; BO/B2O2/B2O3 | yes |
| `…_zaitsev_fe_si_nonmetal` (+ quoted) | p.5 | Zaitsev binary/ternary B/Si/P + Mn/Cr/Fe | yes |
| `…_zaitsev_na2o_sio2` (+ quoted) | p.7 body / p.9 [63] | slag models [63]; title **Thermodynamics of Na2O-SiO2 melts** | yes |
| `…_ti_oxides_and_titanates` (+ quoted) | p.6 | **Ti10O19**; perovskite titanates… | yes |
| `…_popovic_pzt` (+ quoted) | p.9 [55] | PbO-ZrO2-TiO2 RCM cite | yes |
| `…_chatillon_silica_phosphates` (+ quoted) | p.7 | Chatillon silica+phosphate activities | yes |
| `…_sio2_steam_hydroxides` (+ quoted) | p.6–7 | SiO2, V2O5, TcxOy in H2O; hydroxides | yes |
| `…_borate_silicate_glasses` | p.7 | **Over 30** glasses (count, not a P value) | yes |
| `…_refractory_oxide_small_alpha` (+ quoted) | p.6 | small vaporization coefficient for refractory oxides | yes |
| `…_kems_p_and_t_window` | p.2 | **10-5 … 10 Pa**; **above 2500 K** | yes |
| `…_ir_cell_2200k` | p.6 | Hilpert Ir cells **up to 2200 K** | yes |
| `…_supp_tables_1_7_true_absence` | p.4 | Tables 1–7 inventory declared absent from held PDF | yes |

### Coverage

- Review prose species maps, method capability window, and qualitative α represented.
- Numeric activities / pressures live only in supplementary / cited primaries — correctly **not** invented.

### P0 / P1

- **P0: 0**
- **P1 (latent):** review-compilation observations carry `units: none (…)`; consumers must not treat species-map rows as numeric Psat. Already typed.

---

## 2. kems-025-markova-1983 (Markova, Yakovlev, Semenov & Belov 1983, LPSC XIV 460–461)

**PDF:** 2 pp. ADS scan; text layer empty. Values checked via 200–300 dpi page renders + table OCR.

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 18 (`Al2O3`, `Al`, `Na`, `K`, `Fe`, `SiO`, `Mg`, `Ca`, `AlO`, `Al2O`, `TiO`, `FeO`, `CaO`, `PO`, `PO2`, `TiO2`, `O`, `O2`, `Al2O2`, `MgO`) |
| Observations | **22** (1 composition table + method + Al-proportion bound + figure-only / not-tabulated psats) |
| fidelity_samples | 4 |

### Sample = ALL 22 + full composition grid

**Composition table (p.461) — ALL cells:**

| Oxide | 1 anorthite | 2 anorth. gabbro | 3 A16 basalt | 4 troctolite | Extract match |
| --- | ---: | ---: | ---: | ---: | --- |
| SiO₂ | 42.86 | 42.73 | 45.40 | 44.16 | yes |
| TiO₂ | 0.05 | 0.05 | 0.32 | 0.13 | yes |
| Al₂O₃ | **36.63** | 26.72 | 28.63 | **20.29** | yes |
| FeO | 0.55 | 5.54 | 4.25 | **8.84** | yes |
| MnO | 0.00 | 0.09 | 0.06 | 0.14 | yes |
| MgO | 0.11 | 8.27 | 4.38 | 15.58 | yes |
| CaO | 19.16 | 14.31 | 16.39 | 10.79 | yes |
| Na₂O | 0.55 | 1.26 | 0.41 | 0.71 | yes |
| K₂O | 0.06 | 0.11 | 0.06 | 0.08 | yes |
| Total | 100.05 | 99.16 | 99.96 | 100.72 | yes |

| Observation | Locator | Key checks vs PDF | OK? |
| --- | --- | --- | --- |
| `…_kems_method_and_inventory` | p.460 Technique | Knudsen; **MS-1301**; **3–7 grad/min**; species list Na…O₂ + FeO/CaO/MgO(?)/Al₂O₂ insignificant | yes |
| `…_initial_compositions_table` | p.461 Table | all 40 cells above; footnote 1–4 lithologies; sample 3 = **68415,40** | yes |
| `…_al_alo_al2o_proportion_bound` | p.460 | Al prevailed; AlO+Al₂O **≤10%** | yes |
| `…_*_psat_figs_figure_only` (Na/K/Fe/SiO/Mg/Ca/Al/AlO/Al₂O/TiO/FeO/CaO) | Figs 1–4 | axis **lg Pᵢ, torr** vs **T°C 1000–2500**; figure-only admission; labeled-per-figure flags checked against caption | yes |
| `…_*_detected_not_tabulated` (PO/PO₂/TiO₂/O/O₂/Al₂O₂/MgO) | p.460 | detected / insignificant / MgO(?) — no printed coordinates | yes |

T_range_K **1273.15–2773.15** = 1000–2500 °C + 273.15 (axis conversion; trail present).

### Coverage

- Only printed numeric table (initial compositions) fully landed.
- Partial-pressure curves correctly **figure_only** (not digitised).
- Volatility sequence prose `(Na,K) → Fe → SiO → Mg-Ca, TiO → Al` present in PDF; not required as a numeric observation.

### P0 / P1

- **P0: 0** (note: Al₂O₃ **20.29** wt% troctolite is correct for **this** 1983 abstract; do not confuse with Markova-1984 Al₂O₃ 19.29→18.29 fix verified under VZ1).
- **P1 (latent):** figure-only Psat series cannot feed numeric battery scores until digitised (already refused).

---

## 3. kems-031-halwax-2024 (Halwax et al. 2024, Met. Mater. Trans. B)

**PDF:** 15 pp. Springer text layer good for tables.

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 3 (`MgO`, `CaO`, `Ni`) |
| Observations | **22** (geometry packages, Table I masses, Tables II–VII enthalpies / σᵢ / compositions, not-α admissions) |
| fidelity_samples | 11 |
| benches / experiments | present |

### Sample = ALL 22 (numeric highlights)

| Check | Extract | PDF | OK? |
| --- | --- | --- | --- |
| ~50 mg load / orifice **0.3 mm** / chamber **&lt;10⁻⁵ mbar** | geometry packages | §III p.825 | yes |
| Table I CaO masses | 53.93→45.06; 49.51→45.72 | Table I | yes |
| Table I MgO masses | 53.67→49.25; 51.58→48.28 | Table I (col-4 header misprints “CaO”; extract notes this) | yes |
| Table II Ni this study | **422 ± 4** @ **1727 K**; T **1650–1800 K**; k=**7.6414×10⁻⁹** | Table II + prose | yes |
| Table II lit | 418±8.4 / 421±5 / 417 (no ±) | Table II | yes |
| Table III σᵢ / nᵢ | 40Ca 9.0534/0.96941; 16O 1.2677/0.99757; 16O₂ 1.9016/0.99515; 24Mg 4.6574/0.78990; **70 eV** | Table III | yes |
| Table IV CaO | −624±3; −625±3; mean **−624.5±3.5**; lit −602…−636; FactPS −635 | Table IV | yes |
| Table IV MgO | −604±4; −592±4; mean **−598±10**; lit −635…−589; FactPS −601 | Table IV | yes |
| Abstract means | −624.5±3.5 CaO; −598±10 MgO | abstract | yes |
| Table V Huber–Holley | CaC₂ 0.029/26970 … Ca 99.37 | Table V | yes |
| Table VI Liang | −542.77; −197.50; −285.83; total **−631.10** | Table VI | yes |
| Table VII Shomate–Huffman | −465.77±0.17; **CaO(s)→MgCl₂** −149.78±0.09 (printed as-is); −285.84±0.04; total **−601.83±0.21** | Table VII | yes |
| not_hkl_alpha / figure-only Psat | geometry_not_alpha_* | §IV Figs 8–15 admissions | yes |

### Coverage

- Primary third-law ΔfH results, Ni calibration, Table I masses, ionization constants, and literature reprint tables I–VII represented.
- Measured Psat curves and FactSage recalculations correctly left figure-only / model.

### P0 / P1

- **P0: 0**
- **P1 (latent, paper typography — extract faithful):**
  1. Table I column-4 header prints “Mass After Experiment **CaO**” for MgO after-masses; extract stores MgO after-masses and documents the header glitch.
  2. Table VII reaction 2 is printed **CaO(s) + 2HCl(aq) = MgCl₂(aq) + H₂O(l)** (chemically inconsistent; should be MgO). Extract quotes as printed with `printed_reactant_as_published: CaO(s)` — correct fidelity, not a silent “fix”.
  3. Table VI totals stored as YAML floats `-197.5` / `-631.1` with quotes retaining `-197.50` / `-631.10` — no value change.

---

## Push

```
git push -u origin HEAD:empirical/z15-miller-markova-halwax-2026-09-23
```

**Tip SHA:** `2e9e17c3d138fdfba9269c493f82974a71153fa5` (= `origin/work-v064-green`; no fix commit required)

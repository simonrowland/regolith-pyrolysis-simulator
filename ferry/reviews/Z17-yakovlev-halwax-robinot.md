# Z17 FIDELITY AUDIT — yakovlev-1984 / halwax-2024 / robinot-2026

**Date:** 2026-09-23 (America/Toronto, EDT)  
**Seat:** Z17 — BACKLOG-5  
**Repo / branch:** `regolith-empirical` @ `empirical/z17-yakovlev-halwax-robinot-2026-09-23`  
**Base:** `origin/work-v064-green` = `2e9e17c3d138fdfba9269c493f82974a71153fa5`  
**Tip:** `2e9e17c3d138fdfba9269c493f82974a71153fa5` (unchanged; no P0 fix commit)  
**Worktree:** `/workspace/repos/wt/slot-09`  
**PDFs:** `/workspace/ferry-inbox/from-main-B5-20260923T041100Z/audit-pdfs/` (never committed)  
**Rule:** P0 = wrong number stored that can reach a result/score/ledger today. Fix P0 on branch. Latent ≤ P1. No invented data.

## Candidate selection

| Extract | Obs (YAML) | Disposition |
| --- | ---: | --- |
| `kems-028-yakovlev-1984` | 19 | **AUDITED** (ALL) |
| `kems-031-halwax-2024` | 22 | **AUDITED** (ALL) |
| `kems-044-robinot-2026` | 19 | **AUDITED** (ALL) |

## Verdict

| Extract | Obs | Sampled | P0 | Coverage gaps | Fix commit |
| --- | ---: | ---: | ---: | --- | --- |
| kems-028-yakovlev-1984 | 19 | **ALL 19** + ALL 11 Po2 table cells + 4 fidelity pins | **0** | Figs 1–2 figure-only (already admitted); OCR layer unusable (PNG/render used) | none |
| kems-031-halwax-2024 | 22 | **ALL 22** + Table I/II/III/IV/V/VI/VII cell grids + 11 fidelity pins | **0** | Figs 8–15 Psat / FactSage curves figure-only (already refused); orifice Clausing not invented | none |
| kems-044-robinot-2026 | 19 | **ALL 19** + Fig.4c bar labels + §4.1 prose waypoints + 4 fidelity pins | **0** | Figs 1/4a–b/5/8 curves figure-only; supplementary 1.17% run plot not in corpus PDF | none |

**Overall: PASS — no P0. No extract patch committed. Pushed FF (empty tip = green base).**

---

## Method

1. `pdftotext -layout` under `/workspace/ferry-inbox/reviews/_z17_audit/{yakovlev,halwax,robinot}/`.
2. `pdftoppm` 150–200 dpi for Yakovlev pp.945–946 (Fe/FeO/Po2 crops), Halwax Table I (p.825), II–III (p.826), IV–V (p.830), VI (p.831), VII (p.833), Robinot §4.1 / Fig.4–5 (PDF pp.8–9).
3. Dump all `species.*.observations` → JSON; cell-by-cell / quote-by-quote vs PDF.
4. Fields checked: **value / uncertainty / unit / basis / sign / T / locator / statement_as_published / admission_status** vs PDF.
5. `tools/validate_literature_extracts.py` → **OK: 3 extract file(s) valid** (no extract edits).

---

## 1. kems-028-yakovlev-1984 (Yakovlev, Markova, Semenov & Belov, LPSC XV 945–946)

**PDF:** 2 pp. LPI/ADS scan. Text layer OCR is noisy (typewriter `I`↔`1`); numbers taken from 200 dpi PNG.

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 13 |
| Observations | **19** |
| fidelity_samples | 4 |

### Sample = ALL 19

| Observation | Key checks vs PDF | OK? |
| --- | --- | --- |
| `…_kems_method` | Knudsen cell; T 1000–2500 C; Murchison + Efremovka A/B; fugacity sequence (K,Na)→…→(Al,AlO,Al2O); O2/O; missing Motzfeldt orifice/Clausing | yes |
| `…_Na_P_1225C` / `…_K_P_1225C` | P_Na=**2.7×10⁻⁴**, P_K=**4.2×10⁻⁵** tor @ 1225 C; Na>K; max band 1050–1250 C | yes |
| `…_Fe_troilite_1050C` | P_Fe=**1.8×10⁻⁵** tor @ 1050 C (troilite) | yes |
| `…_Fe_max_1450_1725C` | Printed “Maximum P_FeO=10⁻²”; stored as **Fe(g)** p=**0.01** with corrections[] + Fig.1 Fe label at lg P≈−2 — correct remapping, not a wrong number | yes |
| `…_FeO_top_1650C` | P_FeO=**8.1×10⁻⁵** tor @ 1650 C (PNG confirms ⁻⁵, not ⁻²) | yes |
| `…_SiO_Mg_max_1750_1825C` / `…_Mg_max_…` | P_SiO=**2×10⁻²**, P_Mg=**8.3×10⁻³** tor; simultaneous drop @ 1825 C | yes |
| `…_Ca_1825C` / `…_Ca_max_1950_2025C` | P_Ca=**7.5×10⁻⁵** @ 1825 C; max **4.4×10⁻⁴** @ 1950–2025 C | yes |
| `…_Al_max_2100C` | P_Al=**1.7×10⁻³** tor @ 2100 C; suboxides ≤0.1 of total Al (Murchison) | yes |
| `…_CAI_SiO_max_1875C` | P_SiO=**6.2×10⁻³** @ 1875 C; sharp leap ~10⁻³ | yes |
| `…_CAI_Al_suboxide_fraction` (+ Al2O pair) | type A **0.18–0.20**; type B mean **0.4** | yes |
| `…_Po2_table_calculated` | ALL 11 filled cells: Murchison 2020/2072/2130 = 1.4e-6 / 1.6e-6 / 1.5e-6; Efr.A 2130→2348 = 2.0e-5, 4.7e-6, 3.6e-6, 5.5e-5, 1.9e-4; Efr.B 2130/2205/2267 = 3.4e-4 / 2.8e-4 / 4.1e-5; blanks preserved; equilibria AlO=Al+O, O2=2O; `model_output_not_measurement` | yes |
| Fig.1 / Fig.2 / TiO / S2 qualitative | figure_only / qualitative_not_numeric admissions stand | yes |

### P0 / P1

- **P0: 0**
- **P1 (latent):** Author typewriter prints “P_FeO=10⁻²” for the Fe max band; extract correctly stores Fe(g) with an explicit corrections[] row. Po2 is model-derived (already admitted).

---

## 2. kems-031-halwax-2024 (Halwax, Sergeev, Müller & Schenk, MMTB 55B 2024)

**PDF:** 15 pp. journal PDF. Text layer good for tables.

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 3 (MgO, CaO, Ni) |
| Observations | **22** |
| fidelity_samples | 11 |
| Experiments | 3 |

### Sample = ALL 22

| Observation | Key checks vs PDF | OK? |
| --- | --- | --- |
| Table I MgO masses | 1st **53.67→49.25** mg; 2nd **51.58→48.28**; header typo “Mass After Experiment CaO” noted | yes |
| Table I CaO masses | 1st **53.93→45.06**; 2nd **49.51→45.72** | yes |
| Geometry / not-α | Ir cell orifice **0.3 mm**; chamber start **1×10⁻⁵ mbar**; Clausing/area not invented; explicitly not HKL α | yes |
| Table II Ni sublimation | This study **422±4** kJ/mol @ **1727 K**; JANAF **418±8.4**; Alcock **421±5**; FactSage **417** | yes |
| Table III σᵢ / nᵢ | ⁴⁰Ca **9.0534 / 0.96941**; ¹⁶O **1.2677 / 0.99757**; ¹⁶O₂ **1.9016 / 0.99515**; ²⁴Mg **4.6574 / 0.78990**; 70 eV | yes |
| Table IV MgO | 1st **−604±4**; 2nd **−592±4**; mean **−598±10** (bold); lit 9/15/1/13/14/4/FactPS match | yes |
| Table IV CaO | 1st **−624±3**; 2nd **−625±3**; mean **−624.5±3.5** (bold); lit 9/**3**/4/1/11/12/FactPS match (source **3** = −610±4, not 15) | yes |
| Table V Huber–Holley | CaC₂ 0.029/26970; CaH₂ 0.52/18880; CaO 0.07/—; Mg 0.01/24670; Ca 99.37/? | yes |
| Table VI (Ref.4) | −542.77; −197.50; −285.83; total **−631.10** | yes |
| Table VII (Ref.14) | −465.77±0.17; **CaO(s)**+2HCl→MgCl₂ (as printed) −149.78±0.09; −285.84±0.04; total **−601.83±0.21** | yes |
| Head ΔfH rows | MgO −598.0 / CaO −624.5 mirror Table IV means; melt-activity excluded | yes |

### P0 / P1

- **P0: 0**
- **P1 (latent):** Table VII reaction 2 prints reactant CaO with product MgCl₂ (author/reprint typo); extract quotes as printed with note. Figs 8–15 not digitised.

---

## 3. kems-044-robinot-2026 (Robinot et al., Adv. Space Res. 2026 AIP)

**PDF:** 15 pp. article-in-press. Text layer good.

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 9 |
| Observations | **19** |
| fidelity_samples | 4 |

### Sample = ALL 19

| Observation | Key checks vs PDF | OK? |
| --- | --- | --- |
| `…_o2_yield_measured` | **35 mg** O₂; **1.05 %** mass yield; **2.47 %** of feedstock O; **31 mg O₂/kWh**; sample ≈**3.38 g**; flux **4.8 MW/m²**; fill ~10 mbar / run ~13 mbar both retained | yes |
| `…_o2_prose_rate_points` | 380 W no O₂; 650 W brief peak t=10–15; 1220 W no addl; 1200 W @ t=17 peak **3750 ppm**; 1460 W @ t=25 → 2000→1500 ppm over 33 min | yes |
| `…_mass_balance_text` | glass **1.82 g**; vaporized+captured **1.1 g**; unaccounted **0.27 g**; recovery **92 %**; competing conclusion 1.3 g flagged | yes |
| `…_mass_balance_fig4c_bar_labels` | Glass **1.82** / Holder **0.2** / Window **0.35** / Condenser **0.2** / Filter **0.51** / O₂ **0.035** / discrepancy **0.265**; sum=3.38 | yes |
| `…_o2_comparable_run_1p17` | **1.17 %** similar conditions, different heating rate (supp not in PDF) | yes |
| `…_o2_hsc_expected_1p37_model` | HSC ~**1.37 %** @ 1800 C / 10 mbar; authors’ invalid-comparison caveat kept; model admission | yes |
| `…_eac1_composition_quoted_sesko` | SiO₂ **44.41**, Al₂O₃ **12.80**, Fe₂O₃ **12.20**, MgO **12.09**, CaO **10.98**, Na₂O **2.95**, TiO₂ **2.44**, K₂O **1.32**, P₂O₅ **0.61**, MnO **0.20**; O content **44.41** wt% | yes |
| Deposit EDS/XRD qualitative (Na/K/Fe/Si/Mg/Al/Ca/Ti) | prose attributions match §4.2; Fig.5 figure-only | yes |
| Fig.1 / Fig.4ab / Fig.8 | figure_only admissions; 80 kg / 0.84 kg O₂/day prose retained | yes |

### P0 / P1

- **P0: 0**
- **P1 (latent):** Competing mass-balance figures (1.1 / 1.26 / 1.3 g vaporized) correctly kept separate under `competing_observation_do_not_average`. Fill 10 mbar vs run ~13 mbar both retained.

---

## Validation

```
tools/validate_literature_extracts.py \
  data/literature/extracts/kems-028-yakovlev-1984.yaml \
  data/literature/extracts/kems-031-halwax-2024.yaml \
  data/literature/extracts/kems-044-robinot-2026.yaml
→ OK: 3 extract file(s) valid
```

No extract edits → no re-pin of census tests.

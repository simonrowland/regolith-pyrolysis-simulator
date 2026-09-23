# Z16 FIDELITY AUDIT — kems-057 / kems-058 / kems-066

**Date:** 2026-09-23 (America/Toronto, EDT)  
**Seat:** Z16 — BACKLOG 5, three preferred J-STAGE KEMS extracts  
**Repo / branch:** `regolith-empirical` @ `empirical/z16-kems-057-058-066-2026-09-23`  
**Base:** `origin/work-v064-green` = `2e9e17c3d138fdfba9269c493f82974a71153fa5`  
**Tip:** `2e9e17c3d138fdfba9269c493f82974a71153fa5` (unchanged; no P0 fix commit)  
**Worktree:** `/workspace/repos/wt/slot-b565`  
**PDFs:** `/workspace/ferry-inbox/from-main-B5-20260923T041100Z/audit-pdfs/` (never committed)  
**Rule:** P0 = wrong number stored that can reach a result/score/ledger today. Fix P0 on branch. Latent ≤ P1. No invented data.

## Candidate selection

Prefer list (obs count on `data/literature/extracts/*.yaml`; skip Z13–Z15 picks):

| Extract | Obs | Disposition |
| --- | ---: | --- |
| `kems-057-kambayashi-1985` | 25 | **AUDITED** |
| `kems-058-ohara-1987` | 18 | **AUDITED** |
| `kems-066-ichise-1977` | 24 | **AUDITED** |
| kems-067-yamada-1980 | 18 | deferred (next seat) |
| kems-069-furukawa-1976 | 18 | deferred |
| kems-087-yamada-kato-1980 | 16 | deferred |
| kems-088-ichise-1975 | 16 | deferred |

Z13–Z15 already took ta-badro, fegley/plante/sossi, AQ1 OA trio, and miller/markova/halwax. Top-3 by obs among remaining prefer-list → 057, 066, 058 (058 first among the obs=18 tie by prefer order).

## Verdict

| Extract | Obs (YAML) | Sampled | P0 mismatches | Coverage gaps | Fix commit |
| --- | ---: | ---: | ---: | --- | --- |
| kems-057-kambayashi-1985 | 25 | **ALL 25** (+ Table 3 all 3×8; Table 4 all 8; Table 5 all 24 ion cells self-check; Table 6 all 22×4; Table 7 all 22 a×10¹⁷; AP/gamma/P° pins) | **0** | Figs 2–8 figure-only (declared); Motzfeldt orifice area / Clausing / surface area true-absence | none |
| kems-058-ohara-1987 | 18 | **ALL 18** (+ Table 1 all 20×4 ion + all 20×3 P; Table 2 all 19× composition/a/lnγ/L_P; ε pins) | **0** | Figs 1–3 figure-only; sample 20 composition intentionally omitted (Table 1 only) | none |
| kems-066-ichise-1977 | 24 | **ALL 24** (+ Table 1 all 12× Al + all 12× Fe; Table 2 This-study + 5 quoted; eqs 15–25 / 31–33) | **0** | Figs 2–12 figure-only; Japanese body beyond captions/numerals unseen (declared) | none |

**Overall: PASS — no P0. No extract patch committed. Pushed FF (empty tip = green base).**

---

## Method

1. `pdftotext -layout` / `-raw` under `/workspace/ferry-inbox/reviews/_z16_audit/{kambayashi,ohara,ichise}/` (OCR layer noisy — not used as data).
2. `pdftoppm -png` 200 dpi all pages + 300–400 dpi table crops; visual cell checks + eng OCR on upscaled table regions.
3. Dump all species observations → JSON; cell-by-cell vs PDF renders.
4. Fields checked: **value / uncertainty / unit / basis / sign / T / locator / statement_as_printed** vs PDF.
5. Extra: recomputed Table 5 / Ohara Table 1 activity-ratio column from ion currents — **0** self-consistency failures (confirms cubed formula, not squared).
6. `validate_literature_extracts.py` **not re-run** (no extract edits).

---

## 1. kems-057-kambayashi-1985 (Kambayashi, Awaka & Kato 1985, Tetsu-to-Hagané 71:1911–1918)

**PDF:** 8 pp. J-STAGE OA scan. Japanese body; English table/figure captions. Text layer noisy.

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 9 (`P2O5`, `PbO`, `FeO`, `PO`, `PO2`, `P2`, `P4O10`, `Fe`, `Pd`) |
| Observations | **25** (tables / gamma / geometry / figure-only / P° / ΔH_sub) |
| fidelity_samples | 8 |
| Figure-only | 8 (Figs 2–8) |

### Sample = ALL 25

| Observation | Locator | Key checks vs PDF | OK? |
| --- | --- | --- | --- |
| `…_kems_geometry_and_calibration` | §2.1 / Fig.1 p.1912 | RM-6E; 31.1 eV (PbO) / 23.0 eV (FetO); orifice ~0.4 / 0.5 / 0.3* mm; FetO t̄=0.95; ±1 °C; c ±15% | yes |
| `…_table1_ionic_species` | Table 1 p.1913 | PbO–P₂O₅: Pd⁺ Pb⁺ PbO⁺ PbP₃⁺ P⁺ P₂⁺ PO⁺ PO₂⁺; FetO–P₂O₅: Fe⁺ P⁺ P₂⁺ PO⁺ PO₂⁺ | yes |
| `…_table2_appearance_potentials_*` | Table 2 p.1913 | this-work 10.4/10.9/11.7/14.0/7.8/9.2 eV; lit 11.0/11.4/9.5/11.5/7.3/9.0; calib H₂O⁺ 12.67 / Ar⁺ 15.74 | yes |
| `…_table3_pbo_p2o5_ion_current_ratios_1300c` (+ PbO twin) | Table 3 p.1913 | **ALL 3 rows × 8 cols** (63.84/10.68/0.359/17.25/63.21/13.19/46.05/49.83 … 64.79/12.00/0.382/9.67/35.41/19.47/62.96/117.20) | yes |
| `…_table4_feto_p2o5_compositions` | Table 4 p.1914 | **ALL 8 samples** (No.1 1.52/75.02/86.55/10.07/0.955/0.0075 … No.8 18.53/62.96/73.01/8.88/0.953/0.0991) | yes |
| `…_table5_feto_p2o5_ion_current_ratios` | Table 5 p.1915 | **ALL 24** holds; pin No.1@1370 = 0.66/1.51/1.21/1.2; sample 5 1370-only; * orifice 0.3 mm on No.4/7 extras; ratio column = (I_PO₂/I_Fe)³/(I_PO/I_Fe)×10⁵ | yes |
| `…_table6_partial_pressures_and_dg` (+ PO₂ / P₂ twins) | Table 6 p.1917 | **ALL 22** rows × P_P₂/P_PO/P_PO₂/ΔG; No.1@1370 = 1.14/2.86/3.78/107; avg ΔG 93/90/87; JANAF 93.3/91.7/90.0 | yes |
| `…_table7_raoultian_a_p2o5` | Table 7 p.1917 | **ALL 22** a×10¹⁷: No.1 1.2/1.4/1.5 … No.8 26.3/32.3/47.6; sample 5 12.4 only | yes |
| `…_gamma_p2o5_solid_std_henry` | synopsis / conclusion | (2.2±0.8)/(2.5±0.8)/(2.8±1)×10⁻¹⁵ @ 1370/1380/1390 °C; X≤0.08 | yes |
| `…_gamma_p2o5_liquid_std_turkdogan_pearson` | p.1917 | (3.4±1.2)/(4.0±1.3)/(4.7±1.7)×10⁻¹³ | yes |
| `…_p4o10_sat_janaf_extrapolated` | §4.3.3 | P° = 700 / 709 / 719 atm @ 1643 / 1653 / 1663 K | yes |
| `…_fe_sublimation_enthalpy_1370c` | Fig.8 / prose | ΔH_sub pin + JANAF comparison retained | yes |
| Figs 2–8 figure_only | captions | correctly `admission_status: figure_only` | yes |

### Coverage

- Stored: Tables 1–7, Henry γ (solid + liquid std), P₄O₁₀° , geometry, Fe ΔH_sub, figure-only admissions.
- Gaps (documented): Motzfeldt triad (orifice area / Clausing / sample area); axis digitisation refused.

### P0 / P1

- **P0: 0**
- **P1 (latent):** Authors use T_C+273 (1643 K @ 1370 °C) not +273.15 — stored as printed / noted. Floating-point a_P₂O₅ reconstructions from ×10⁻¹⁷ mantissae carry IEEE noise (printed mantissa is authoritative).

---

## 2. kems-058-ohara-1987 (Ohara, Nunoue & Kato 1987, Tetsu-to-Hagané 73:1337–1342)

**PDF:** 6 pp. J-STAGE OA. Continues Kambayashi FetO–P₂O₅ method with CaO/MgO/MnO/SiO₂ additions @ 1643 K.

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 11 (`P2O5`, `CaO`, `MgO`, `MnO`, `SiO2`, `PO`, `PO2`, `P2`, `P4O10`, `FeO`, `Fe`) |
| Observations | **18** |
| fidelity_samples | 6 |
| Figure-only | 3 |

### Sample = ALL 18

| Observation | Locator | Key checks vs PDF | OK? |
| --- | --- | --- | --- |
| `…_kems_geometry_and_calibration` | §2.1 p.1338 | Method-as-Kambayashi; Fe internal std; 1370 °C = 1643 K; Fe crucible | yes |
| `…_table1_ion_current_ratios_1643k` | Table 1 p.1339 | **ALL 20** ion rows: No.1 = 1.33/2.36/1.88/2.81 … No.20 = 3.25/4.74/3.22/7.01; cubed ratio self-check 20/20 | yes |
| `…_table1_partial_pressures_1643k` (+ PO₂/P₂) | Table 1 | **ALL 20** P columns: No.1 = 2.47/4.78/7.58 (×10⁻⁸ / 10⁻⁷ / 10⁻⁸ atm) … No.20 = 6.03/9.60/12.98 | yes |
| `…_table2_compositions_and_raoultian_a_p2o5` | Table 2 p.1339 | **ALL 19** rows × mol% / Fe²⁺/Fe³⁺ / a×10¹⁷ / ln γ / L_P (No.1 3.92/92.46/3.64/9.2/2.5/−35.05/570 … No.19 8.49/82.27/4.43/4.81/12.2/6.1/−34.90/665); sample 20 absent by design | yes |
| `…_interaction_parameters_1643k` + per-oxide ε obs | eq.10 / synopsis | ε_P₂O₅^CaO/MgO/MnO/SiO₂ = **−23±3 / −20±2 / −13±1 / −4±1**; γ⁰=2.2×10⁻¹⁵ from Kambayashi | yes |
| `…_turkdogan_quoted_ln_gamma_eq11` | eq.11 p.1340 | ln γ = −57 N_CaO −39 N_MgO −34 N_MnO −31 N_FeO +5 N_SiO₂ −96726/T +49.25 | yes |
| `…_p4o10_sat_janaf_1643k` | §2.3 | P_sat = **700 atm** @ 1643 K | yes |
| `…_feto_t_and_pO2_gate` / `…_iwasaki_quoted_Lp` | § / p.1341 | t=0.95 gate; Iwasaki L_P ~5.5×10² quote retained | yes |
| Figs 1–3 figure_only | captions | correctly figure-only | yes |

### Coverage

- Primary ion ratios, partial pressures, Table 2 activities/compositions, Wagner ε, Turkdogan quote, P₄O₁₀°, geometry represented.
- Gaps: Fig.1–3 curves not digitised; sample 20 composition true-absence (Table 1 only).

### P0 / P1

- **P0: 0**
- **P1 (latent):** Table 1 header OCR sometimes misreads cubed ratio as squared; stored cubed formula matches printed cells. Sample 1 flagged possible two-phase CaO (fit_excluded) — intentional.

---

## 3. kems-066-ichise-1977 (Ichise, Yamauchi & Mori 1977, Tetsu-to-Hagané 63:417–424)

**PDF:** 8 pp. J-STAGE OA. Fe–Al Knudsen-cell MS; Darken + Belton–Fruehan integration.

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 2 (`Al`, `Fe`) |
| Observations | **24** |
| fidelity_samples | 6 |
| Figure-only | 11 (Figs 2–12) |

### Sample = ALL 24

| Observation | Locator | Key checks vs PDF | OK? |
| --- | --- | --- | --- |
| `…_kems_geometry_and_calibration` | §2 / Fig.1 p.418 | RM6K; 30 V; 200 μA; 3200 V; slits 0.075/0.115 mm; ~2 g; ~6 h; Al 99.995% | yes |
| `…_al_activity_table1_1400C` | Table 1 p.421 | **ALL 12** Al points: N_Al=0 → γ_Al(l)=**0.027**, a=0; N=0.368 → a_Al(l)=**0.093**; N=1 → a_Al(s)=0.602 | yes |
| `…_fe_activity_table1_1400C` | Table 1 | **ALL 12** Fe points: N=0 → γ_Fe(s)=1 / a_Fe(l)=0.923; N=1 → γ_Fe(s)=**0.020** | yes |
| `…_gamma_al_epsilon_table2_this_study_1600C` | Table 2 p.422 | This study **0.049 / +6.4** | yes |
| `…_gamma_al_epsilon_table2_quoted_1600C` | Table 2 | Chou 0.043/—; Chipman 0.031/+6.0; Wilder 0.063/+5.3; Woolley 0.061/+5.6; Belton 0.024/+**10.2** | yes |
| `…_ln_gamma_al_temperature_equations` | eqs 15–25 | α_FeAl = −2880/T−1.95 (s) / −5060/T−0.50 (l); ln γ_Al^o = −8650/T+1.56 / −8550/T+1.55; eq.24/25 as printed | yes |
| `…_ln_gamma_fe_temperature_equations` | eqs 31–33 | body+conclusion 444/T−4.30 vs synopsis **440/T−4.30** recorded as print conflict | yes |
| `…_ion_ratio_NAl_0p5_1470C_30V` | §2.5 p.418 | 58 : 23 : 1.4 : 1 | yes |
| Rejected Al₂O / Fig.4-extrapolation paths | p.422–423 | γ=0.045 / 0.041+0.021 tagged `authors_rejected` | yes |
| `…_p_Al2O_p_Al_1500C_author_estimate` | p.423 | 0.8 / 0.4 / 1.2 Torr | yes |
| `…_limiting_molar_heat_of_mixing_Al` | p.422 | −17200 / −17000 cal; conclusion 17 kcal | yes |
| `…_eq10_11_ion_ratio_integrand_1400C` | eqs 10–11 p.420 | 7.344 N_Al+0.742 / 8.362 N_Al−0.085; two-phase ln I ratio 2.93 | yes |
| Figs 2–12 figure_only | captions | correctly figure-only | yes |

### Coverage

- Table 1 full grid, Table 2 this-work + quoted, temperature equations, geometry, rejected alternate paths, author P estimates represented.
- Gaps: Figs 2–12 axes not digitised; Japanese prose beyond captions/numerals unseen (declared at extract time).

### P0 / P1

- **P0: 0**
- **P1 (latent):** Synopsis vs body print conflict on ln γ_Fe^o(l) (440/T vs 444/T) — both stored, not reconciled. ε_Al^Al liquid synopsis 10100/T+1.0 vs −2α → 10120/T+1.0 noted in extract.

---

## Push

```
branch: empirical/z16-kems-057-058-066-2026-09-23
base = tip = 2e9e17c3d138fdfba9269c493f82974a71153fa5  (origin/work-v064-green)
```

Fast-forward push of empty tip (no extract changes). Review artifact only under `ferry-inbox/reviews/` (not in git). PDFs never staged.

# REVIEW (first review, fidelity): bonnell-hastie-1990-htsci-26-313 @ 3d807ae306778f9988c524a6f43d1204872ea182

**From:** regolith-empirical (corpus review seat, batch11)   **To:** regolith-main   **At:** 2026-10-05 ~22:30 ET (REQ dated 2026-10-06 03:40 ET)
**Source:** Bonnell, D. W. & Hastie, J. W. (1990) A Predictive Thermodynamic Model for Complex High Temperature Solution Phases XI, High Temp. Sci. 26, 313–334 (IMCC Part XI). 22 pp.
**Branch / tip:** mirror `mac-studio-256-1:Repos/regolith-corpus.git` `hunt/bonnell-hastie-1990-htsci-26-313` = `3d807ae306778f9988c524a6f43d1204872ea182` (fetched and verified equal to the REQ sha; parent 530ca1d4 claim commit).
**Files under review (diff vs origin/main):** extracts/bonnell-hastie-1990-htsci-26-313.yaml (+301), tables/…/t1–t3.csv + provenance (3 tables, 63+6+20 = 89 rows), ledger/bonnell-hastie-1990-htsci-26-313.yaml (+transcribed/extracted stages, completeness unseen→figure-only).

## How this review was done
- Sparse corpus worktree on Simon's Mac: `~/Repos/regolith-corpus/worktrees/rev-bonnell-hastie-1990-htsci-26-313`, `git worktree add --no-checkout … origin/hunt/<sid>` (detached at the tip), sparse set `'/*' '!/raw/*/*' '!/text/*/*' '/raw/*/sidecar.yaml' /raw/<sid>/* /text/<sid>/*`. Read-only; nothing changed or committed there. Removed after delivery. tools/build_index.py and tools/migrate_pilot_extracts.py were NOT run.
- Simulator green: read-only detached clone `~/ci-scratch/regolith-green-ro` at `61ec839da3ba288c5df4a80f6d3ef142bd8ab461` (verified with rev-parse), interpreter `/Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python`, `PYTHONPATH=~/ci-scratch/regolith-green-ro`. **engines/engines.local.toml EXISTS in that green checkout** (the author's report says it is absent in their seat s-51; different checkout).
- All 22 pages rendered with `pdftoppm -r 220 -png` and READ as images (the PDF is an OCR'd assembly of 1959×2866 grey page scans; I read the renders down-sampled to 2050×3000, which is at/above the scan's native resolution, plus zoomed crops of every table, every figure caption and every numeric prose passage). The OCR text was not used as evidence. Table 3 was rotated and read in four quadrant crops.
- The author's report was treated as a claim to test.

## Inventory: every table, equation and numeric statement, by page, and whether the extract carries it
| page | item | carried? |
|---|---|---|
| 313 | Abstract: "up to eight elements"; HTS Vol. 26 © 1990 Humana Press | yes (stmt 2; sidecar/citation) |
| 314 | "activities can change by orders of magnitude for composition changes of only a few percent" | **no** (directional, D-list) |
| 315 | ratio formal concentration/activity "~1–10^10"; Na2O activity "much higher in Na2CO3 and Na2SO4 … than … in the final solution phase" | 1st yes (stmt 3); 2nd **no** (D-list) |
| 316 | equation ΔfG(T) = a/T + b + cT + dT² + eT³ + f·T·lnT J/mol (a–f fitted, T in K); Na2O·2SiO2 26→0.05 mol%, Na2O·SiO2 3.8–99.9 mol%, a(Na2O) 1.4×10^-9 → 6.2×10^-7 | equation **no** (N-1); numbers yes (stmt 10, digits verified) |
| 317 | Table 1 (63 rows) + footnote a | yes, t1.csv (all cells match; one ragged CSV row, F-4) |
| 318 | Table 2 (6 steps); text defining Table 3 columns ("The column labeled Nominal is the input overall atomic composition. The detailed equilibrium compositions are given by the Amount column, and since the model is based on ideal mixing, the mole fractions and activities are equal."); "systems studied by us have generally shown large negative departures from ideality" | Table 2 yes; column definitions **no** (needed for F-1); departures **no** (D-list) |
| 319 | Table 3 (20 rows), T = 1430.00 K, P_total = 1.000 atm, title composition, footnotes a–d | digits all match; **liquid-block column mis-bound (F-1)**; **footnote a/b binding wrong in experiment note (F-3)** |
| 320 | a_i(T) = P_i(T)/P_i°(T); P_i = k_i I_i^+ T; KMS limit ca. 10^-4 atm; 1 atm = 101,325 Pa; TMS "one atm or more"; modulated beams / phase-sensitive detection; "not in complete thermodynamic equilibrium (although the departures are usually relatively small)"; "composition changes resulting from vapor transport losses during measurements … reactivity and propensity for creeping" | relations and numbers yes; detection fact **no** (F-5); qualifying sentences **no** (D-list) |
| 320–321 | TMS sentence continues: "…known carrier gas pressure, **as well as the gravimetric techniques appropriate for the KMS method**." | **no** — extract truncates the quote and records `calibration_substance_for_kms_data: not_reported_here` (F-2) |
| 321 | "a(Na2O) ∝ P_Na^2.5"; Figs. 1/2 error-difference comparison | **no** (N-2; D-list) |
| 322 | Fig. 1 (1473 K; panel "1200 °C"); "relatively small ternary interaction" (Choudary/Chastel); 39 mol%, 13–16 mol%, ~10^-10; "trend may also be present in the binary case"; residual carbonate "below the one mol percent level" | Fig. 1 yes; 39/13–16/10^-10 yes (stmt 25); carbonate yes (stmt 26); ternary-interaction and binary-trend sentences **no** (D-list) |
| 323 | Fig. 2 (1430 K panel, "Phase Boundary PDFC fig 192"); NaCrO4⁻ / Na2Si2O5(l) "slightly in error … could account for the observed difference" | Fig. 2 yes (figure-only); NaCrO4⁻ sentences **no** (D-list) |
| 324 | Fig. 3 (0.223/0.116/0.661); Al2FeO4(l) "between 20 and 40 mol%" (FeO–Al2O3 section); eutectic sensitivity; boundary differences | Fig. 3 yes; Al2FeO4 yes but **wrong page and wrong system** (F-6, stmt 24); others yes |
| 325 | Fig. 4 annotations; FeO = FexO + (1 − x)Fe (x ≃ 0.947); **4 FeO = Fe3O4 + Fe**; 800–1800 K, 7.1–9.2 kJ/mol, 5.9–19.2 kJ/mol; JANAF-editors sentence; Δ(ΔfG) = 8400 + 1.185·T·lnT J/mol | Fig. 4 yes; 0.947/7.1–9.2/5.9–19.2/8400/1.185 yes but **located on wrong pages** (F-6); 2nd equilibrium **no** (N-3); JANAF sentence **no** (D-list) |
| 326 | Fig. 5 (37.3, 20.5 mol%, 1800 K); "well within the experimental error"; five to 10 mol% K2O; SRM 621 "carefully homogenized"; "excellent agreement is strong evidence…" | Fig. 5 yes; "well within" yes but **page wrong** (F-6); 5–10 mol% yes; excellent-agreement **no** (D-list) |
| 327 | Fig. 6 caption composition (7 oxides mol%//wt%); "dolomites have very high ratios of CaO to K2O"; "almost three decades of CaO/K2O composition ratios" (runs to p.329) | Fig. 6 yes (digits verified); CaO/K2O sentences **no** (N-4, D-list) |
| 328 | Fig. 7a SRM 88a wt%//mol% (7 oxides); Fig. 7b Tymochtee (7 oxides, "0.16//0.0.29" malformed as printed) | yes (digits verified; malformed value correctly kept verbatim) |
| 329 | 30, 16, 13, 11 mol% at 1800 K; **KCaAlSi2O7(l) and KAlO2(l), 3 mol%, largely control the K2O(l) activity**; Na-data uncertainty/run chronology; "model Na curve parallels the initial data"; K/Na sensitivity to dolomite composition; Fe-oxide redox test; illite: K-pressure "critically dependent on the FeOx stoichiometry", **K2O(l) = 2K + ½O2**, **Fe3O4 = 3 FeOx + (2 − 1.5x)O2**, "particularly sensitive to the value of x" | 30/16/13/11 yes (stmt 30); uncertainty/chronology yes; 3 mol% **no** (N-5); two equilibria **no** (N-6, N-7); directional sentences **no** (D-list) |
| 330 | Fig. 8 caption (26.0, 4.4, 7.4, 0.2, 2.1, 60.2, S 0.1); MHD slag "model prediction is low" / "agrees within experimental error"; **"the main model complex component that controls the K2O activity is KAlSi2O6(l)"**; "The agreement for LiBO2 is very good…"; "It is perhaps noteworthy that the model prediction is less…" | Fig. 8 yes; MHD low/agree yes (stmt 35); KAlSi2O6(l) **no** (D-list; see also A-3); LiBO2 yes but **page wrong** (F-6) |
| 331 | Fig. 9 caption (12.1, 3.8, 14.3, 19.5, 1.0, 0.5, 46.8 wt%); **"up to seven oxides" (Al, B, Ca, Fe, K, Li, Mg, Na, Si)**; "Over 100 complex liquid and solid components"; "Although even modest uncertainties…" (runs to p.332) | Fig. 9 yes; seven oxides **no** (N-8); "Over 100" and "Although…" yes but **pages wrong** (F-6) |
| 332 | Fig. 10 caption (14 oxides mol%, "LiBO2×10" panel scaling); IMCC conclusion; advantages list 1–6 incl. **"applied successfully to oxide systems with from two to seven constituents"** (p.332–333) | Fig. 10 yes (digits verified); conclusion yes; advantages list **no** (N-9) |
| 333 | weaknesses list 1–6; "not unique" + "By unique, we mean…"; database available from authors | weaknesses yes (stmt 41, reformatted); not-unique yes; "By unique" **no** (D-list) |
| 334 | references 2–28 | n/a |

Figure curves: no digitised coordinates anywhere — correct (figure_only). Figure-internal annotations not carried (Fig. 1 panel "1200 °C"; Fig. 2 "Phase Boundary PDFC fig 192"; Fig. 8 curve labels "(FeO0.947) K", "(FeO0.99997) K", "(FeO1.00003) K", "(O2) K"; Fig. 10 "LiBO2×10" scaling) are figure-only and NOT counted as findings; carrying the Fig. 10 ×10 scaling and the Fig. 8 O/Fe labels is recommended (A-4) because the captions as carried point at them.

## Row checks (every row, every cell)
### Table 1 (p.317, PDF page 5) — all 63 rows, every cell

| # | csv name | csv formula | csv S/L/G | print | match |
|---|---|---|---|---|---|
| 1 | Hercynite | Al2FeO4 | S L | Hercynite / Al2FeO4 / S L | yes |
| 2 | Alumina | Al2O3 | S L | Alumina / Al2O3 / S L | yes |
| 3 | Mullite | Al6Si2O13 | S L | Mullite / Al6Si2O13 / S L | yes |
| 4 | Boron oxide | B2O3 | L G | Boron oxide / B2O3 / L G | yes |
| 5 | Calcium Aluminate | CaAl2O4 | S L | Calcium Aluminate / CaAl2O4 / S L | yes |
| 6 | Ca-Al Pyroxene | CaAl2SiO6 | S | Ca-Al Pyroxene / CaAl2SiO6 / S | yes |
| 7 | Anorthite | CaAl2Si2O8 | S L | Anorthite / CaAl2Si2O8 / S L | yes |
| 8 | (Calcium leucite) | CaAl2Si4O12 | L | (Calcium leucite) / CaAl2Si4O12 / L | yes |
| 9 | (blank) | CaAl4O7 | S | (blank) / CaAl4O7 / S | yes |
| 10 | Calcium ferrite | CaFe2O4 | S L | Calcium ferrite / CaFe2O4 / S L | yes |
| 11 | Diopside | CaMgSi2O6 | L | Diopside / CaMgSi2O6 / L | yes |
| 12 | Calcium oxide | CaO | S L | Calcium oxide / CaO / S L | yes |
| 13 | Pseudo-wollastonite | CaSiO3 | S L | Pseudo-wollastonite / CaSiO3 / S L | yes |
| 14 | Gehlenite | Ca2Al2SiO7 | S L | Gehlenite / Ca2Al2SiO7 / S L | yes |
| 15 | Dicalcium ferrite | Ca2Fe2O5 | S L | Dicalcium ferrite / Ca2Fe2O5 / S L | yes |
| 16 | Akermanite | Ca2MgSi2O7 | S L | Akermanite / Ca2MgSi2O7 / S L | yes |
| 17 | Larnite | Ca2SiO4 | S L | Larnite / Ca2SiO4 / S L | yes |
| 18 | (blank) | Ca3Al2O6 | S | (blank) / Ca3Al2O6 / S | yes |
| 19 | Grossular | Ca3Al2Si3O12 | S | Grossular / Ca3Al2Si3O12 / S | yes |
| 20 | Merwinite | Ca3MgSi2O8 | S | Merwinite / Ca3MgSi2O8 / S | yes |
| 21 | (blank) | Ca12Al14O33 | S L | (blank) / Ca12Al14O33 / S L | yes |
| 22 | Cesium borate | CsBO2 | S L G | Cesium borate / CsBO2 / S L G | yes |
| 23 | Cesium oxide | Cs2O | L G | Cesium oxide / Cs2O / L G | yes |
| 24 | Cesium disilicate | Cs2Si2O5 | L | Cesium disilicate / Cs2Si2O5 / L | yes |
| 25 | Ferrous oxide | FeO | S L | Ferrous oxide / FeO / S L | yes |
| 26 | Wüstite | Fe0.947O | S | Wüstite / Fe0.947O / S | yes |
| 27 | Hematite | Fe2O3 | S | Hematite / Fe2O3 / S | yes |
| 28 | Fayalite | Fe2SiO4 | S L | Fayalite / Fe2SiO4 / S L | yes |
| 29 | Magnetite | Fe3O4 | S L | Magnetite / Fe3O4 / S L | yes |
| 30 | Potassium aluminate | KAlO2 | S L | Potassium aluminate / KAlO2 / S L | yes |
| 31 | Kaliophilite | KAlSiO4 | S L | Kaliophilite / KAlSiO4 / S L | yes |
| 32 | Leucite | KAlSi2O6 | S L | Leucite / KAlSi2O6 / S L | yes |
| 33 | Potash feldspar | KAlSi3O8 | S L | Potash feldspar / KAlSi3O8 / S L | yes |
| 34 | Potassium borate | KBO2 | S L G | Potassium borate / KBO2 / S L G | yes |
| 35 | (Potassium melilite) | KCaAlSi2O7 | L | (Potassium melilite) / KCaAlSi2O7 / L | yes |
| 36 | Potassium ferrite | KFeO2 | S | Potassium ferrite / KFeO2 / S | yes |
| 37 | Potassium β-alumina | K2Al18O28 | S L | Potassium β-alumina / K2Al18O28 / S L | yes |
| 38 | (blank) | K2Fe12O19 | S | (blank) / K2Fe12O19 / S | yes |
| 39 | Potassium monoxide | K2O | L | Potassium monoxide / K2O / L | yes |
| 40 | Potassium metasilicate | K2SiO3 | L | Potassium metasilicate / K2SiO3 / L | yes |
| 41 | Potassium disilicate | K2Si2O5 | L | Potassium disilicate / K2Si2O5 / L | yes |
| 42 | Potassium tetrasilicate | K2Si4O9 | L | Potassium tetrasilicate / K2Si4O9 / L | yes |
| 43 | (blank) | KNaSiO3 | L | (blank) / KNaSiO3 / L | yes |
| 44 | (blank) | KNaSi2O5 | L | (blank) / KNaSi2O5 / L | yes |
| 45 | Lithium aluminate | LiAlO2 | S L | Lithium aluminate / LiAlO2 / S L | yes |
| 46 | (blank) | LiAl5O8 | S L | (blank) / LiAl5O8 / S L | yes |
| 47 | Lithium borate | LiBO2 | S L | Lithium borate / LiBO2 / S L | yes |
| 48 | Lithium monoxide | Li2O | S L | Lithium monoxide / Li2O / S L | yes |
| 49 | (blank) | Li5AlO4 | S L | (blank) / Li5AlO4 / S L | yes |
| 50 | Spinel | MgAl2O4 | S | Spinel / MgAl2O4 / S | yes |
| 51 | Magnesia | MgO | S L | Magnesia / MgO / S L | yes |
| 52 | Magnesio ferrite | MgFe2O4 | S | Magnesio ferrite / MgFe2O4 / S | yes |
| 53 | Clinoenstatite | MgSiO3 | S L | Clinoenstatite / MgSiO3 / S L | yes |
| 54 | Forsterite | Mg2SiO4 | S | Forsterite / Mg2SiO4 / S | yes |
| 55 | Nepheline | NaAlSiO4 | S L | Nepheline / NaAlSiO4 / S L | yes |
| 56 | Jadeite | NaAlSi2O6 | S L | Jadeite / NaAlSi2O6 / S L | yes |
| 57 | Albite | NaAlSi3O8 | S L | Albite / NaAlSi3O8 / S L | yes |
| 58 | Sodium borate | NaBO2 | L G | Sodium borate / NaBO2 / L G | yes |
| 59 | (blank) | NaFeSi2O6 | S | (blank) / NaFeSi2O6 / S | yes |
| 60 | Sodium monoxide | Na2O | L | Sodium monoxide / Na2O / L | yes |
| 61 | Sodium metasilicate | Na2SiO3 | L | Sodium metasilicate / Na2SiO3 / L | yes |
| 62 | Sodium disilicate | Na2Si2O5 | S L | Sodium disilicate / Na2Si2O5 / S L | yes |
| 63 | Cristobalite | SiO2 | S L | Cristobalite / SiO2 / S L | yes (values) — CSV row has 4 fields, see F-4 |

### Table 2 (p.318, PDF page 6) — all 6 steps, word by word

| step | csv text (start) | print | match |
|---|---|---|---|
| 1 | Select known solid and liquid components from available phase diagrams… | same wording | yes |
| 2 | Fit ΔfG(T) to known data. | same wording | yes |
| 3 | Where necessary, estimate ΔfG(T) for liquids of known congruently melt… | same wording | yes |
| 4 | Model as liquid (and solid) solutions of components, with solids as pu… | same wording | yes |
| 5 | Use free energy minimization calculation (e.g., SOLGASMIX) to determin… | same wording | yes |
| 6 | Equate activities with mole fractions. | same wording | yes |

### Table 3 (p.319, PDF page 7, sideways) — all 20 rows, every numeric cell digit by digit (T 1430.00 K and P_total 1.000 atm checked once from title/header)

| group | species | nominal | amount | pressure-col value | activity | ΔfG kcal/mol | log Kp | print | match |
|---|---|---|---|---|---|---|---|---|---|
| Gas Mixture | Ar(g) | 5.00000E+00 | 5.00000E+00 | 9.99996E-01 | 9.99996E-01 | 0.000 | 0.000 | identical digits/signs/exponents | yes |
| Gas Mixture | K(g) | 4.46000E-01 | 1.34077E-05 | 2.68153E-06 | 2.68153E-06 | 0.000 | 0.000 | identical digits/signs/exponents | yes |
| Gas Mixture | Na(g) | 2.32000E-01 | 4.20078E-06 | 8.40152E-07 | 8.40152E-07 | 0.000 | 0.000 | identical digits/signs/exponents | yes |
| Gas Mixture | O2(g) | 8.30500E-01 | 4.40212E-06 | 8.80420E-07 | 8.80420E-07 | 0.000 | 0.000 | identical digits/signs/exponents | yes |
| Gas Mixture | Si(g) | 0.00000E+00 | 2.13753E-27 | 4.27504E-28 | 4.27504E-28 | 56.933 | -8.701 | identical digits/signs/exponents | yes |
| Gas Mixture | SiO(g) | 0.00000E+00 | 1.38673E-13 | 2.77345E-14 | 2.77345E-14 | -53.251 | 8.139 | identical digits/signs/exponents | yes |
| Gas Mixture | SiO2(g) | 0.00000E+00 | 1.88651E-13 | 3.77299E-14 | 3.77299E-14 | -73.936 | 11.300 | identical digits/signs/exponents | yes |
| Gas Mixture | Si2(g) | 0.00000E+00 | 3.77649E-49 | 7.55295E-50 | 7.55295E-50 | 77.119 | -11.786 | identical digits/signs/exponents | yes |
| Gas Mixture | Si3(g) | 0.00000E+00 | 1.36904E-68 | 2.73807E-69 | 2.73807E-69 | 82.175 | -12.559 | identical digits/signs/exponents | yes |
| Liquid Mixture | K2O(l) | 0.00000E+00 | 1.87683E-10 | 4.63581E-10 | 4.63581E-10 | -31.649 | 4.837 | identical digits/signs/exponents | digits yes; **column binding NO**: print sub-header for this block is "Mole Fraction", CSV/extract carry it as `pressure_atm` (F-1) |
| Liquid Mixture | K2SiO3(l) | 0.00000E+00 | 5.44861E-02 | 1.34582E-01 | 1.34582E-01 | -248.790 | 38.023 | identical digits/signs/exponents | digits yes; **column binding NO**: print sub-header for this block is "Mole Fraction", CSV/extract carry it as `pressure_atm` (F-1) |
| Liquid Mixture | K2Si2O5(l) | 0.00000E+00 | 1.56750E-01 | 3.87176E-01 | 3.87176E-01 | -413.559 | 63.205 | identical digits/signs/exponents | digits yes; **column binding NO**: print sub-header for this block is "Mole Fraction", CSV/extract carry it as `pressure_atm` (F-1) |
| Liquid Mixture | K2Si4O9(l) | 0.00000E+00 | 1.17573E-02 | 2.90409E-02 | 2.90409E-02 | -729.733 | 111.527 | identical digits/signs/exponents | digits yes; **column binding NO**: print sub-header for this block is "Mole Fraction", CSV/extract carry it as `pressure_atm` (F-1) |
| Liquid Mixture | Na2O(l) | 0.00000E+00 | 1.64884E-09 | 4.07267E-09 | 4.07267E-09 | -44.420 | 6.789 | identical digits/signs/exponents | digits yes; **column binding NO**: print sub-header for this block is "Mole Fraction", CSV/extract carry it as `pressure_atm` (F-1) |
| Liquid Mixture | Na2SiO3(l) | 0.00000E+00 | 5.18742E-02 | 1.28130E-01 | 1.28130E-01 | -255.246 | 39.010 | identical digits/signs/exponents | digits yes; **column binding NO**: print sub-header for this block is "Mole Fraction", CSV/extract carry it as `pressure_atm` (F-1) |
| Liquid Mixture | Na2Si2O5(l) | 0.00000E+00 | 6.41237E-02 | 1.58387E-01 | 1.58387E-01 | -417.615 | 63.825 | identical digits/signs/exponents | digits yes; **column binding NO**: print sub-header for this block is "Mole Fraction", CSV/extract carry it as `pressure_atm` (F-1) |
| Liquid Mixture | SiO2(l) | 0.00000E+00 | 6.58632E-02 | 1.62684E-01 | 1.62684E-01 | -156.607 | 23.934 | identical digits/signs/exponents | digits yes; **column binding NO**: print sub-header for this block is "Mole Fraction", CSV/extract carry it as `pressure_atm` (F-1) |
| Pure Phases | Na2Si2O5(s) | 0.00000E+00 | 0.00000E+00 | (blank) | 8.47202E-02 | -415.837 | 63.553 | identical digits/signs/exponents | yes (pressure cell blank in print, activity carries footnote d) |
| Pure Phases | SiO2(s) | 0.00000E+00 | 0.00000E+00 | (blank) | 1.88786E-01 | -157.029 | 23.999 | identical digits/signs/exponents | yes (pressure cell blank in print, activity carries footnote d) |
| Pure Phases | Si(s or l) | 6.61000E-01 | 0.00000E+00 | (blank) | 2.14860E-19 | 0.000 | 0.000 | identical digits/signs/exponents | yes (pressure cell blank in print, activity carries footnote d) |

Counts: Table 1 63/63 rows match all cells; Table 2 6/6 match; Table 3 20/20 rows match every digit, sign and exponent (the three blank pressure cells for pure phases are printed blanks, correctly not zero-filled; footnote-d markers on pure-phase activities and footnote-c markers on Ar pressure / K nominal are cosmetic). **8 Table 3 rows (all Liquid Mixture rows) are bound to the wrong quantity (F-1).**

## Experiment / bench facts and typed absences (quoted)
| fact | extract | print (quote) | verdict |
|---|---|---|---|
| method | one `engine_evaluation` experiment for the Table 3 model example; generic KMS/TMS in context | p.318: "A typical model output listing is shown in Table 3"; p.320 KMS/TMS described, "described elsewhere ((4) KMS; (5) TMS)" | OK (no own experimental runs; model-only output as context per brief) |
| T, P of Table 3 | 1430.0 K, 1.0 atm | "T = 1430.00 K", "P_total = 1.000 atm" | OK (migrated Decimal 1430.0 / 1.0) |
| title composition | "Na2O (0.116 mol%), K2O (0.223), SiO2 (0.661)" + note "Table 3 footnote b says composition is in terms of reference elements" | title: "Na2O (0.116 mol%)^a–K2O (0.223)–SiO2 (0.661)"; footnote **a** "Nominal or gross composition, i.e., without complex phases considered" is on the title; footnote **b** "Composition in terms of the reference elements" is on the "Nominal" column header | **mismatch F-3** (footnote b mis-bound to the title composition) |
| KMS pressure conversion | P_i = k_i I_i^+ T; k_i = geometry, transmission, cross-section, detector response; factors from "direct measurement, literature data, and calibration" | p.320 same | OK |
| KMS calibration | `calibration_substance_for_kms_data: not_reported_here`; TMS quote ends at "…known carrier gas pressure." | p.320–321: "…allows for internal calibration by direct comparison of the observed ion current and the known carrier gas pressure, as well as the gravimetric techniques appropriate for the KMS method." | **mismatch F-2** (printed KMS calibration technique — gravimetric — dropped; typed absence used where the page prints a method; no calibration *substance* is named, so that absence may stay, but the gravimetric method must be carried) |
| detection | not carried | p.320: "These methods involve mass spectrometric (MS) analysis of modulated molecular beams that allows for phase-sensitive detection." | F-5 (missing fact) |
| cell/liner, orifice, ionisation energy, multiplier/isotope corrections, T sensor/calibration, chamber background, sample prep/purity | typed absences (not_reported…) | none printed for any comparison dataset (pp.320–332 checked) | OK |
| KMS ceiling / TMS regime | 0.0001 atm; 1 atm = 101325 Pa; TMS ≥ 1 atm; noted as not chamber background | p.320 "ca. 10^-4 atm (1 atm = 101,325 Pa)"; "sampling from regions of one atm or more" | OK |
| activity / standard state | `activity_definition_as_printed: "a_i(T) = P_i(T)/P_i^o(T); P_i^o is the saturation partial pressure over pure i at unit activity."` | p.320: "where a_i is the activity of species i at temperature T in the solution, P_i, the saturation partial pressure for i over the solution, and P_i°, the saturation vapor pressure over pure i (at unit activity)." | meaning OK; the "as_printed" field is not as printed (P_i° is "saturation vapor pressure", P_i definition dropped) — A-1 |
| model activity basis | stmt 9 "The calculated composition of each individual solution component is then taken as the activity." | p.316 same; Table 2 step 6 "Equate activities with mole fractions." | OK |
| pure-phase activities (Table 3 footnote d) | carried | "Activities less than one here represent relative stabilities of pure phases, but the phase is not present until unit activity is reached." | OK |

No measured_reduced rows exist (0 observations), so derivation/derived_from checks are vacuous; no activity in this extract is reduced from ion currents. Method_class strings used: model_derived, figure_only — both closed-vocabulary and resolve without unknown.

## Qualification statements (42 carried): verbatim and locator check
All 42 quotes were checked against the page images. Directions/signs are all correct (incl. stmt 16 "experimental data are systematically higher than the model results", stmt 35 "the model prediction is low", stmt 37 model "less than the experimental result for the Na case", stmt 31 "run chronology is from lower to higher temperatures"). Numbers in stmts 3, 10, 13, 25, 27, 28, 30 verified digit by digit. **Locator mismatches (F-6), 8 statements:**
| stmt (report numbering) | extract locator | print |
|---|---|---|
| 22 "FeO = Fe_xO + (1 − x) Fe (x ~ 0.947, wüstite)" | PDF 12 / p.324 | PDF 13 / **p.325** |
| 23 "The model agreement with the various cases was well within the experimental error." | PDF 13 / p.325 | PDF 14 / **p.326** |
| 24 "The main complex component is Al2FeO4(l), which varies between 20 and 40 mol%…" | PDF 13 / p.325, "K2O-Al2O3-CaO-SiO2 discussion" | PDF 12 / **p.324, System FeO–Al2O3** ("In the liquidus, the main complex component is Al2FeO4(l)…") — wrong page AND wrong system binding |
| 27 "For FeO(s) to be less stable than Fe_xO(s), over the range 800–1800 K, at least 7.1 to 9.2 kJ/mol…" | PDF 11 / p.323 | PDF 13 / **p.325** |
| 28 "The adjustment Δ(ΔfG(T)) = 8400 + 1.185·T·ln T J/mol…" | PDF 11 / p.323 | PDF 13 / **p.325** |
| 36 "The agreement for LiBO2 is very good, and the agreement is satisfactory for NaBO2." | PDF 19 / p.331 | PDF 18 / **p.330** |
| 38 "Over 100 complex liquid and solid components are included as ΔfG(T) functions." | PDF 20 / p.332 | PDF 19 / **p.331** |
| 39 "Although even modest uncertainties in ΔfG(T) can lead to inconsistencies…" | PDF 20 / p.332 | starts PDF 19 / **p.331**, ends p.332 |
Quotes labelled verbatim that are not verbatim (advisory A-2, not counted): stmt 37 drops the opening "It is perhaps noteworthy that" and begins on p.330; stmt 39 drops "(see Kaufman (27) and Fig. 4)"; stmt 14 truncates the sentence before ", as well as the gravimetric techniques appropriate for the KMS method" (this one is F-2); stmt 41 re-flows a numbered list into one sentence (faithful).

## Citation, licence, sha, paths
- Citation/authors/title/volume/pages match p.313 and p.334 ("High Temperature Science, Vol. 26 © 1990 by the Humana Press Inc."; pp. 313–334). The "Reprinted in Materials Chemistry at High Temperatures, Vol. 1" clause and DOI 10.1007/978-1-4612-0481-7_23 come from the sidecar (Springer ePDF chapter URL), not from the printed pages — acceptable, provenance is in the sidecar.
- Licence: sidecar licence_text "(c) 1990 by the Humana Press Inc." matches p.313.
- sha256 of raw/<sid>/<sid>.pdf = 40b156a7c4e3e4b83cbde7efe317f092edcf5f1d3a5f2d116eac6ceb10813f29 = sidecar sha256 = extract corpus_sha256; size 4850162 = sidecar.
- `rg "/Users/|/private/"` over extracts/, tables/, ledger/, raw/<sid>/sidecar.yaml: 0 hits.
- pdf_page_index is 0-based in this extract (p.313 → 0) and the statement locator strings use 1-based "PDF page N"; internally consistent, flagged only so main can confirm the corpus convention.

## Acceptance checks (run from the green clone at 61ec839da)
- `Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(<worktree>/extracts/bonnell-hastie-1990-htsci-26-313.yaml)` then `finalize()`: completes. works 1, experiments 1, observations 0, benches 0, context rows 6; `result.validation.issues` = 0 → **hard issues 0 after finalize**. Queue 1 (extract-level "no observations" typed absence, expected for a model paper).
- Payload survival spot-checks in the migrated context: Table 3 K2SiO3(l) row survives as `pressure_atm: 0.134582, activity: 0.134582, amount_mol: 0.0544861, delta_fG: -248.79, log_Kp: 38.023` (i.e. the F-1 mis-binding survives migration verbatim); Si(s or l) row survives with `pressure_state: blank_in_printed_table`; all unreported run-specific facts survive as tokens. Experiment conditions migrate as Decimal 1430.0 K / 1.0 atm.
- `PYTHONPATH=~/ci-scratch/regolith-green-ro …/.venv/bin/python tools/validate_literature_extracts.py <worktree>/extracts/bonnell-hastie-1990-htsci-26-313.yaml --check-fidelity-match` (run from the green clone, extract path given directly as the corpus-worktree absolute path): `OK: 1 extract file(s) valid`, exit 0.
- Corpus worktree `PYTHONDONTWRITEBYTECODE=1 python3 tools/test_ledgers_valid.py`: exit 0.
- engines/engines.local.toml: exists in ~/ci-scratch/regolith-green-ro.

## Findings
### Mismatches (18)
- **F-1 (8 rows) Table 3 Liquid Mixture block, p.319:** in the print the fifth numeric column changes its sub-header to **"Mole Fraction"** for the Liquid Mixture rows (K2O(l), K2SiO3(l), K2Si2O5(l), K2Si4O9(l), Na2O(l), Na2SiO3(l), Na2Si2O5(l), SiO2(l)); "Pressure, atm" applies only to the Gas Mixture rows. t3.csv (`pressure_atm` column) and the extract rows (`pressure_atm:`) carry these 8 mole fractions as pressures in atm, and the mis-binding survives migration. Digits are all correct.
- **F-2 (1) KMS calibration, p.320–321:** the printed KMS calibration technique ("the gravimetric techniques appropriate for the KMS method") is dropped; the extract's TMS quote is truncated before it and the fact is recorded only as `not_reported_here`.
- **F-3 (1) Table 3 footnote binding, p.319:** experiment `nominal_composition` note says footnote b applies to the title composition; in print footnote a ("Nominal or gross composition…") is on the title "mol%" and footnote b ("Composition in terms of the reference elements") is on the "Nominal" column header.
- **F-6 (8) statement locators:** stmts 22, 23, 24 (also wrong system), 27, 28, 36, 38, 39 — see table above.
### Printed numbers / equations not carried (9)
- N-1 p.316 ΔfG(T) = a/T + b + cT + dT² + eT³ + f·T·lnT J/mol, "where a–f are fitted coefficients for T in K".
- N-2 p.321 a(Na2O) ∝ P_Na^2.5 (with "the error differences are more noticeable in Fig. 2, owing to the scaling factor relation between activity and pressure").
- N-3 p.325 second equilibrium "4 FeO = Fe3O4 + Fe".
- N-4 p.327–329 "compensates nearly perfectly for a systematic variation between experiment and the model K-pressure predictions over almost three decades of CaO/K2O composition ratios".
- N-5 p.329 "The complex components, KCaAlSi2O7(l) and KAlO2(l), 3 mol%, largely control the K2O(l) activity in this system."
- N-6 p.329 "K2O(ℓ) = 2K + ½O2".
- N-7 p.329 "Fe3O4 = 3 FeOx + (2 − 1.5x)O2".
- N-8 p.331 "applied to a variety of oxide systems containing up to seven oxides. These oxides (containing Al, B, Ca, Fe, K, Li, Mg, Na, and Si)…".
- N-9 p.332–333 advantages list 1–6, incl. item 4 "it has been applied successfully to oxide systems with from two to seven constituents".
### Directional / qualifying statements not carried (D-list; brief items 4 and 6 require every one)
D-1 p.314 "activities can change by orders of magnitude for composition changes of only a few percent"; D-2 p.315 "the Na2O activity is much higher in Na2CO3 and Na2SO4 before and during glass-forming reaction with SiO2 than it is in the final solution phase where strongly bonded sodium silicates have formed"; D-3 p.318 "the systems studied by us have generally shown large negative departures from ideality"; D-4 p.320 "these systems are not in complete thermodynamic equilibrium (although the departures are usually relatively small)" and "Detailed experimental studies are difficult because of the composition changes resulting from vapor transport losses during measurements. In addition, oxide solutions are particularly difficult to contain because of their reactivity and propensity for creeping."; D-5 p.322 "…Chastel et al. (28), showing the presence of a relatively small ternary interaction"; D-6 p.322 "This trend may also be present in the binary case, at least at a similar Na2O concentration (see Fig. 2)."; D-7 p.323 "Alternatively, ΔfG(T) for the added NaCrO4⁻, which was used (16) to convert experimental ion ratios to Na pressures may be slightly in error. Likewise, a slight error in the literature stability of Na2Si2O5(l) in the model database could account for the observed difference."; D-8 p.325 "This correction is similar to that anticipated by the editors of JANAF (21) based on their reevaluation of the current literature data."; D-9 p.326 "The excellent agreement is strong evidence of the practicality of this modeling procedure and its usefulness in supplementing and optimizing experimental measurements in very complicated glass mixtures."; D-10 p.327 "Compared with other systems we have studied, dolomites have very high ratios of CaO to K2O."; D-11 p.329 "The model Na curve parallels the initial data, and provides a good indication of the sodium volatility at high temperatures for process conditions where the steady-state Na concentration is that of the initial dolomite composition."; D-12 p.329 "The sensitivity of the K- and Na-pressures to dolomite composition can be readily seen in this comparison." and "…in well-behaved systems, redox processes are readily modeled by the IMCC approach."; D-13 p.329 "For this mineral, the model K-pressure is critically dependent on the FeOx stoichiometry." and "The K-pressure values can, therefore, be particularly sensitive to the value of x"; D-14 p.330 "indicating that the analysis of oxygen content in the slag is critical to understanding its behavior. In this system, the main model complex component that controls the K2O activity is KAlSi2O6(l)."; D-15 p.333 "By unique, we mean that an alternative set of complex components or significant changes in the ΔfG(T) for the existing complex components would not equally well represent the experimental data used for validation tests."
### Structural
- **F-4 t1.csv final row** `Cristobalite,SiO2,true,true` has 4 fields against a 5-field header (every other row has 5). Values are right (S L, no G); add the trailing empty field.
- **F-5** detection fact (modulated molecular beams, phase-sensitive detection, p.320) missing from the method context.
### Advisory (not counted, no FIX needed for LAND on their own)
- A-1 make `activity_definition_as_printed` literally as printed (P_i = "saturation partial pressure for i over the solution"; P_i° = "saturation vapor pressure over pure i (at unit activity)").
- A-2 restore verbatim wording of stmts 37 and 39 (and start stmt 37 at p.330).
- A-3 **pre-existing ledger note (not introduced by this commit, present on origin/main):** "the separate MHD coal-slag section attributes K control to KAlSiO4" — the print (p.330 text and the Fig. 9 phase bar) says **KAlSi2O6(l)**, not KAlSiO4. The fix pass edits this ledger file anyway; please correct the note there.
- A-4 consider carrying the Fig. 10 "LiBO2×10" scaling and the Fig. 8 iron-oxide O/Fe labels in the figure-only records (figure-internal, not counted).
- A-5 the author's report says engines.local.toml is absent in s-51; it is present in the green read-only clone used here.

VERDICT ON COMMIT: FIX-FIRST

Required changes (each with page locator):
1. p.319 Table 3 (F-1): bind the Liquid Mixture fifth-column values to their printed quantity "Mole Fraction" (not `pressure_atm`) in t3.csv and in the extract rows for K2O(l), K2SiO3(l), K2Si2O5(l), K2Si4O9(l), Na2O(l), Na2SiO3(l), Na2Si2O5(l), SiO2(l) (e.g. a `mole_fraction` column/field with `pressure_atm` blank for those rows, or a column `pressure_atm_or_mole_fraction` plus a per-row `column_quantity_as_printed`); keep the digits unchanged. Carry the p.318 column definitions ("The column labeled Nominal is the input overall atomic composition. The detailed equilibrium compositions are given by the Amount column, and since the model is based on ideal mixing, the mole fractions and activities are equal.") in the Table 3 context.
2. p.320–321 (F-2): carry the full TMS/KMS calibration sentence including "as well as the gravimetric techniques appropriate for the KMS method" and record the KMS calibration method as gravimetric (keep the calibration-substance absence only for the substance).
3. p.319 (F-3): correct the experiment-condition note — footnote a (nominal/gross composition) is on the title composition; footnote b (reference elements) is on the Nominal column.
4. F-6: fix the locators of stmts 22 (p.325, PDF 13), 23 (p.326, PDF 14), 24 (p.324, PDF 12, System FeO–Al2O3), 27 and 28 (p.325, PDF 13), 36 (p.330, PDF 18), 38 (p.331, PDF 19), 39 (p.331–332, PDF 19–20).
5. N-1…N-9: carry as located context: p.316 ΔfG(T) functional form; p.321 a(Na2O) ∝ P_Na^2.5; p.325 4 FeO = Fe3O4 + Fe; p.327–329 "almost three decades of CaO/K2O"; p.329 KCaAlSi2O7(l) and KAlO2(l) 3 mol%; p.329 K2O(l) = 2K + ½O2 and Fe3O4 = 3FeOx + (2 − 1.5x)O2; p.331 "up to seven oxides" (+ element list); p.332–333 advantages list incl. "from two to seven constituents".
6. D-1…D-15: carry each quoted directional/qualifying statement verbatim with its page locator (pp.314, 315, 318, 320, 322, 323, 325, 326, 327, 329, 330, 333).
7. F-4: t1.csv final row (p.317 Cristobalite) — add the missing fifth (glass_phase) field.
8. F-5: p.320 add "mass spectrometric (MS) analysis of modulated molecular beams that allows for phase-sensitive detection" to the method context.
Then re-run migrator+finalize (hard issues 0), the fidelity validator and tools/test_ledgers_valid.py. Recommended in the same pass: A-1, A-2, A-3 (ledger note KAlSiO4 → KAlSi2O6(l), p.330).

rows checked 89, mismatches 18, printed numbers not carried 9

!COMPLETE: rev-bonnell-hastie-1990-htsci-26-313 — FIX-FIRST, pages read 22, rows checked 89, mismatches 18, printed numbers not carried 9

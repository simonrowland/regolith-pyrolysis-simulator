# Shornikov (2019) extraction audit

## Source and transcription

The paper is born digital. MinerU content-list table cells and `pdftotext-layout.txt` were compared row-by-row. Table 1 has 47 printed body rows; its solid/liquid/gas column assignment was confirmed against the full page 4 render. Table 2 has 5 printed body rows; its T and six oxide columns were confirmed against the page 5 render. All table cells flagged by MinerU were checked against those page renders. No `<details>` chart block was used.

Audit codes: (a) MinerU and pdftotext agree on the numeric characters; (b) page render confirms a flagged cell or printed range/dash. Pages are absolute PDF page numbers; row is printed body row; column uses table header.

## Table 1 — standard Gibbs energies, PDF page 4 / published page 868

| Printed row | Formula by phase | Solid ΔfG (kJ/mol) | Liquid ΔfG (kJ/mol) | Gas ΔfG (kJ/mol) |
|---:|---|---|---|---|
| 1 | solid CaO; liquid CaO; gas Ca | -421.511 (b, p.4 r1 c2) | -409.450 (b, p.4 r1 c4) | -21.080 (b, p.4 r1 c6) |
| 2 | solid MgO; liquid MgO; gas Ca2 | -376.430 (b, p.4 r2 c2) | -345.380 (b, p.4 r2 c4) | 77.075 (b, p.4 r2 c6) |
| 3 | solid Al2O3; liquid Al2O3; gas CaO | -1033.869 (a, p.4 r3 c2) | -1018.664 (a, p.4 r3 c4) | -74.308 (b, p.4 r3 c6) |
| 4 | solid FeO; liquid FeO; gas Mg | -146.291 (b, p.4 r4 c2) | -152.867 (b, p.4 r4 c4) | -57.263 (b, p.4 r4 c6) |
| 5 | solid TiO2; liquid TiO2; gas Mg2 | -583.299 (b, p.4 r5 c2) | -577.637 (b, p.4 r5 c4) | 34.414 (b, p.4 r5 c6) |
| 6 | solid SiO2; liquid SiO2; gas MgO | -550.476 (b, p.4 r6 c2) | -550.607 (b, p.4 r6 c4) | -107.759 (b, p.4 r6 c6) |
| 7 | solid CaAl2O4; liquid CaAl2O4; gas Al | -1517.600 (a, p.4 r7 c2) | -1521.781 (a, p.4 r7 c4) | 82.257 (b, p.4 r7 c6) |
| 8 | solid CaAl4O7; liquid CaAl4O7; gas Al2 | -2567.915 (a, p.4 r8 c2) | -2566.521 (a, p.4 r8 c4) | 191.266 (b, p.4 r8 c6) |
| 9 | solid CaAl12O19; liquid ; gas AlO | -6714.869 (a, p.4 r9 c2) | — (printed dash) | -74.765 (b, p.4 r9 c6) |
| 10 | solid Ca3Al2O6; liquid ; gas AlO2 | -2375.802 (a, p.4 r10 c2) | — (printed dash) | -99.108 (b, p.4 r10 c6) |
| 11 | solid Ca5Al6O14; liquid ; gas Al2O | -5431.722 (a, p.4 r11 c2) | — (printed dash) | -292.198 (b, p.4 r11 c6) |
| 12 | solid Ca12Al14O33; liquid Ca12Al14O33; gas Al2O2 | -12797.385 (a, p.4 r12 c2) | -12882.862 (a, p.4 r12 c4) | -385.783 (b, p.4 r12 c6) |
| 13 | solid CaTiO3; liquid CaTiO3; gas Al2O3 | -1097.162 (a, p.4 r13 c2) | -1086.922 (a, p.4 r13 c4) | -404.059 (b, p.4 r13 c6) |
| 14 | solid Ca3Ti2O7; liquid ; gas Fe | -2586.656 (a, p.4 r14 c2) | — (printed dash) | 130.028 (b, p.4 r14 c6) |
| 15 | solid Ca4Ti3O10; liquid ; gas Fe2 | -3708.030 (a, p.4 r15 c2) | — (printed dash) | 383.352 (b, p.4 r15 c6) |
| 16 | solid CaSiO3; liquid CaSiO3; gas FeO | -1058.628 (a, p.4 r16 c2) | -1064.401 (a, p.4 r16 c4) | 67.370 (b, p.4 r16 c6) |
| 17 | solid Ca2SiO4; liquid Ca2SiO4; gas FeO2 | -1531.242 (a, p.4 r17 c2) | -1522.120 (a, p.4 r17 c4) | 35.366 (b, p.4 r17 c6) |
| 18 | solid Ca3SiO5; liquid ; gas Si | -1945.934 (a, p.4 r18 c2) | — (printed dash) | 167.228 (b, p.4 r18 c6) |
| 19 | solid Ca3Si2O7; liquid ; gas Si2 | -2577.486 (a, p.4 r19 c2) | — (printed dash) | 216.304 (b, p.4 r19 c6) |
| 20 | solid MgAl2O4; liquid MgAl2O4; gas Si3 | -1454.460 (a, p.4 r20 c2) | -1435.549 (a, p.4 r20 c4) | 265.892 (b, p.4 r20 c6) |
| 21 | solid MgSiO3; liquid MgSiO3; gas Si4 | -956.936 (b, p.4 r21 c2) | -963.013 (b, p.4 r21 c4) | 278.763 (b, p.4 r21 c6) |
| 22 | solid Mg2SiO4; liquid Mg2SiO4; gas SiO | -1360.399 (a, p.4 r22 c2) | -1351.811 (a, p.4 r22 c4) | -248.438 (b, p.4 r22 c6) |
| 23 | solid Al2TiO5; liquid Al2TiO5; gas SiO2 | -1625.208 (a, p.4 r23 c2) | -1622.180 (a, p.4 r23 c4) | -314.875 (b, p.4 r23 c6) |
| 24 | solid Al4TiO8; liquid ; gas Si2O2 | -2649.215 (a, p.4 r24 c2) | — (printed dash) | -546.135 (b, p.4 r24 c6) |
| 25 | solid Al2SiO5; liquid ; gas Ti | -1596.884 (a, p.4 r25 c2) | — (printed dash) | 191.645 (b, p.4 r25 c6) |
| 26 | solid Al2Si2O7; liquid ; gas TiO | -2079.741 (a, p.4 r26 c2) | — (printed dash) | -125.135 (b, p.4 r26 c6) |
| 27 | solid Al6Si2O13; liquid Al6Si2O13; gas TiO2 | -4254.309 (a, p.4 r27 c2) | -4223.411 (a, p.4 r27 c4) | -337.178 (b, p.4 r27 c6) |
| 28 | solid FeAl2O4; liquid FeAl2O4; gas Ti2O3 | -1189.680 (a, p.4 r28 c2) | -1189.518 (a, p.4 r28 c4) | -663.737 (b, p.4 r28 c6) |
| 29 | solid FeTiO3; liquid FeTiO3; gas Ti2O4 | -730.540 (b, p.4 r29 c2) | -743.874 (b, p.4 r29 c4) | -826.662 (b, p.4 r29 c6) |
| 30 | solid FeTi2O5; liquid ; gas O | -1309.606 (a, p.4 r30 c2) | — (printed dash) | 121.662 (b, p.4 r30 c6) |
| 31 | solid Fe2TiO4; liquid Fe2TiO4; gas O2 | -870.076 (b, p.4 r31 c2) | -881.055 (b, p.4 r31 c4) | 0.000 (a, p.4 r31 c6) |
| 32 | solid FeSiO3; liquid ; gas O3 | -701.651 (b, p.4 r32 c2) | — (printed dash) | 273.851 (b, p.4 r32 c6) |
| 33 | solid Fe2SiO4; liquid Fe2SiO4; gas O4 | -839.970 (b, p.4 r33 c2) | -848.079 (b, p.4 r33 c4) | 193.351 (b, p.4 r33 c6) |
| 34 | solid CaAl2SiO6; liquid ; gas CaTiO3 | -2083.286 (a, p.4 r34 c2) | — (printed dash) | -661.870 (b, p.4 r34 c6) |
| 35 | solid CaAl2Si2O8; liquid CaAl2Si2O8; gas AlSi | -2654.359 (a, p.4 r35 c2) | -2666.733 (a, p.4 r35 c4) | 1136.071 (a, p.4 r35 c6) |
| 36 | solid Ca2Al2SiO7; liquid Ca2Al2SiO7; gas AlSiO | -2593.512 (a, p.4 r36 c2) | -2606.167 (a, p.4 r36 c4) | -208.996 (b, p.4 r36 c6) |
| 37 | solid Ca3Al2Si3O12; liquid ; gas MgAlO | -4217.301 (a, p.4 r37 c2) | — (printed dash) | -265.016 (b, p.4 r37 c6) |
| 38 | solid CaMgSiO4; liquid ; gas  | -1461.894 (a, p.4 r38 c2) | — (printed dash) | — (printed dash) |
| 39 | solid CaMgSi2O6; liquid CaMgSi2O6; gas  | -2038.188 (a, p.4 r39 c2) | -2065.893 (a, p.4 r39 c4) | — (printed dash) |
| 40 | solid Ca2MgSi2O7; liquid Ca2MgSi2O7; gas  | -2478.460 (a, p.4 r40 c2) | -2498.156 (a, p.4 r40 c4) | — (printed dash) |
| 41 | solid Ca3MgSi2O8; liquid ; gas  | -2976.156 (a, p.4 r41 c2) | — (printed dash) | — (printed dash) |
| 42 | solid Ca3MgAl4O10; liquid Ca3MgAl4O10; gas  | -3844.251 (a, p.4 r42 c2) | -3843.595 (a, p.4 r42 c4) | — (printed dash) |
| 43 | solid CaTiSiO5; liquid CaTiSiO5; gas  | -1689.686 (a, p.4 r43 c2) | -1717.151 (a, p.4 r43 c4) | — (printed dash) |
| 44 | solid MgAl2SiO6; liquid ; gas  | -1980.995 (a, p.4 r44 c2) | — (printed dash) | — (printed dash) |
| 45 | solid Mg2Al4SiO10; liquid ; gas  | -3451.173 (a, p.4 r45 c2) | — (printed dash) | — (printed dash) |
| 46 | solid Mg2Al4Si5O18; liquid Mg2Al4Si5O18; gas  | -5773.135 (a, p.4 r46 c2) | -5794.061 (a, p.4 r46 c4) | — (printed dash) |
| 47 | solid Mg3Al2Si3O12; liquid Mg3Al2Si3O12; gas  | -3859.457 (a, p.4 r47 c2) | -3872.296 (a, p.4 r47 c4) | — (printed dash) |

For rows with a numeric value, `a` is the independent MinerU/pdftotext character match; the rendered page 4 establishes the table’s complete row and phase-column alignment. `b` marks MinerU-flagged values, all checked directly against the page 4 render. Dashes are not numeric cells and remain absent from the typed observations.

The Table 1 footnote on PDF page 4 states that compounds printed in bold type were found in Ca–Al inclusions of chondrites. The boldface designation is retained as source context; it does not change the row values or phase assignments.

## Table 2 — starting compositions, PDF page 5 / published page 869

| Printed row | Temperature (K) | CaO | MgO | FeO | Al2O3 | TiO2 | SiO2 | Reference |
|---:|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 2173 (a, p.5 r1 c1) | 14.43 (a, p.5 r1 c2) | 25.60 (a, p.5 r1 c3) | — (printed dash, b, p.5 r1 c4) | 23.83 (a, p.5 r1 c5) | — (printed dash, b, p.5 r1 c6) | 36.14 (a, p.5 r1 c7) | Ivanova et al., 2018 |
| 2 | 2173 (a, p.5 r2 c1) | 24.10 (a, p.5 r2 c2) | 24.32 (a, p.5 r2 c3) | — (printed dash, b, p.5 r2 c4) | 15.52 (a, p.5 r2 c5) | — (printed dash, b, p.5 r2 c6) | 36.06 (a, p.5 r2 c7) | Mendybaev and Richter, 2016 |
| 3 | 2173 (a, p.5 r3 c1) | 26.76 (a, p.5 r3 c2) | 17.05 (a, p.5 r3 c3) | — (printed dash, b, p.5 r3 c4) | 11.41 (a, p.5 r3 c5) | — (printed dash, b, p.5 r3 c6) | 44.78 (a, p.5 r3 c7) | Richter et al., 2007 |
| 4 | 1850-2400 (b, p.5 r4 c1) | 19.44 (a, p.5 r4 c2) | 7.26 (a, p.5 r4 c3) | 3.98 (a, p.5 r4 c4) | 18.72 (a, p.5 r4 c5) | 0.25 (a, p.5 r4 c6) | 50.35 (a, p.5 r4 c7) | Markova et al., 1986 |
| 5 | 1700-2300 (b, p.5 r5 c1) | 2.08 (a, p.5 r5 c2) | 35.15 (a, p.5 r5 c3) | 29.18 (a, p.5 r5 c4) | 1.38 (a, p.5 r5 c5) | 0.08 (a, p.5 r5 c6) | 32.13 (a, p.5 r5 c7) | Wang et al., 2001 |

Table 2 numeric characters agree between MinerU and pdftotext. Both printed temperature ranges were render-checked (b); dash cells and Table 2 column assignments were checked against the page 5 render. Table 2 values stay unconverted mol% context, so these are starting-composition records, not scored residue observations.

## Classification and figure candidates

Table 1 caption says its Gibbs-energy values were calculated in the course of this research. Because those model input data and derivation parents are not stored in this extract, d-110 fallback is `quoted_attributed`; the paper’s per-row input lineage is not stated, so no derivation links or specific reference assignment were invented. These one-temperature model-input/comparator values can duplicate JANAF/IVTAN data and are not treated as new primary measurements.

Table 2 rows are quoted attributed model starting compositions. Their printed sample labels are supplied by the adjacent Fig. 2 discussion (5aN-3, CAI 4, CAI B) and Fig. 3 caption (lunar aluminous basalt; synthetic oxide material of solar elemental composition). Schema gap: the structured composition schema has no printed-mol-percent basis for a scored initial-composition vector. The exact setup values therefore remain as unscored `composition` context, without division by 100 or a fabricated fraction.

Figs. 2–3 show model residual-composition trajectories with experimental symbols and are figure-read candidates for the residue_composition rail. No figure-only values were digitized. MinerU `<details>` chart blocks were ignored.

Fig. 1 (PDF page 3) plots calculated activities and mixing energy and is a figure-read candidate. No figure-only values were digitized.

## Isolated migration / store expectation

Real isolated migration: 113 observations; 0 hard issues; 0 unavailable values; 113 queued method notices because no method registry is stated for these table records; 0 registry issues; 5 context rows. Every observation has identity T=2000 K and a located temperature condition of 2000 K. For the first three rows, composition, total_pressure_Pa and fO2_Pa are not_applicable in the DELTA_FG profile because the source prints none. The required standard_pressure_Pa, reaction and formation_elements axes remain unknown because Table 1 does not state them; no standard pressure or formation reaction was inferred. The 113 queue notices are not hard failures or unavailable numeric values. Focused pytest pins three migrated `values.points` to the page 4 render and all 47 source rows; a `-999` mutation of the solid CaO point fails while its parallel `values.value` remains unchanged, and row deletion also fails. MinerU compare: matched=113, no-match=0, mismatch=0. The compact [per-cell compare CSV](shornikov-2019-lunar-meteorite-evaporation-model-mineru-compare.csv) records each stored value, match status, PDF page, MinerU table, and matched row/column. No battery store was regenerated. Expected store delta without regeneration: 1 work, 1 experiment registry record, 113 DELTA_FG observations (47 solid, 29 liquid and 37 gas), and 5 unscored Table 2 composition-context rows; no scored residue trajectory observations. `data/literature/INDEX.*` and the battery store were not changed.

The five Table 2 context rows are nested under `species.CaO.context` because context containers are species-scoped in the existing extract shape; each row records the complete multicomponent oxide melt composition.

### Identity axes printed for the first three migrated rows

| Table 1 cell | composition | total_pressure_Pa | fO2_Pa | temperature_K |
|---|---|---|---|---|
| row 1, solid CaO | not_applicable | not_applicable | not_applicable | 2000 |
| row 1, liquid CaO | not_applicable | not_applicable | not_applicable | 2000 |
| row 1, gas Ca | not_applicable | not_applicable | not_applicable | 2000 |

The DELTA_FG profile also requires `per`, `standard_pressure_Pa`, `reaction` and `formation_elements`: `per=mol_species` maps; the latter three remain unknown because Table 1 does not print their values. `temperature_K` is present in each observation identity and its `point_conditions.temperature_K` located state.

Verification: `tests/test_shornikov_2019_extract.py` and `tests/test_yaml_flow_scalar_commas.py` passed (18 tests); the parameterized literature fidelity check for this extract passed (1 test). The focused test pins three migrated `values.points` values to the page 4 render and rejects a `-999` mutation to the solid CaO point while leaving `values.value` unchanged; deleting one Table 1 row is also rejected.

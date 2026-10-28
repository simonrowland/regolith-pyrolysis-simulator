# Niggli (1913) extraction audit

## Source and scope

Paul Niggli, “The phenomena of equilibria between silica and the alkali carbonates,” *Journal of the American Chemical Society* 35(11) (1913), 1693–1727, DOI 10.1021/ja02200a004. The volume, issue, pages, and DOI are given in the corpus sidecar. Extraction is limited to printed numerical tables and quoted melting-point comparisons; figure-read values are listed as candidates, not transcribed.

## Table II: potassium carbonate–silica, PDF page 8 / printed page 1700

The printed table has 18 initial-charge rows: I–XV and three unnumbered charges. It contains 30 measured column (a) values and 30 column (b) values. The render confirms the row and column assignment. Table QA reported digit agreement 0.625 against pdftotext, so every retained numeric cell below was checked against the rendered page. Values are recorded in printed order as `(temperature °C: (a) weight loss %, (b) mol CO2 per 100 mol SiO2)`; `—` means the table prints no number for that temperature.

| Printed charge | Initial K2CO3/SiO2, wt%; SiO2, mol% | 898 °C (a/b) | 956 °C (a/b) | 998 °C (a/b) |
|---|---|---:|---:|---:|
| I | 96.9/3.1; 6.9 | 6.6/89.6 | — | — |
| II | 96.2/3.8; 8.2 | 8.3/92.9 | — | — |
| III | 95.3/4.7; 10.2 | 9.7/85.5 | — | — |
| IV | 94.9/5.1; 10.9 | 10.5/85.4 | 11.1/90.9 | — |
| V | 92.5/7.5; 15.7 | 15.4/82.4 | 16.6/88.9 | — |
| VI | 90.3/9.7; 19.8 | 20.5/83.1 | — | — |
| VII | 88.1/11.9; 23.8 | 24.7/79.2 | 26.9/86.4 | 28.6/91.7 |
| VIII | 83.1/16.9; 31.9 | 36.2/77.3 | 38.9/83.4 | — |
| IX | 80.4/19.6; 36.1 | 42.6/75.5 | 46.3/82.1 | 48.6/86.2 |
| X | 79.5/20.5; 37.3 | 44.8/75.5 | 48.6/81.9 | 51.4/86.4 |
| XI | 70.7/29.3; 48.8 | 66.7/69.7 | 71.3/74.5 | 75.9/79.4 |
| XII | 69.7/30.3; 49.9 | 68.4/68.5 | 73.0/73.2 | 77.7/77.8 |
| XIII | 67.5/32.5; 52.6 | 74.8/67.4 | 79.3/71.4 | — |
| XIV | 61.5/38.5; 59.1 | — | — | 91.9/63.7 |
| XV | 61.0/39.0; 59.6 | — | 91.4/61.9 | — |
| unnumbered 1 | 56.8/43.2; 63.6 | qualitative liquid + solid only | qualitative liquid + solid only | qualitative liquid + solid only |
| unnumbered 2 | 54.9/45.1; 65.0 | “Melt too viscous” | “Melt too viscous” | “Melt too viscous” |
| unnumbered 3 | 53.4/46.6; 66.7 | solid | solid | 100.0/50.0 |

Column (a) is retained as `mass_loss_fraction` with the printed percent unit. It is Niggli's calculated percentage from measured gravimetric loss; raw row-wise weights are not tabulated, so evidence class is `quoted_attributed`. Column (b) is retained in typed context only. No exact schema quantity represents “mol CO2 displaced per 100 mol SiO2”: `Quantity.EVOLVED_GAS_YIELD` is defined with unit `mol_species_per_initial_kg` in `simulator/battery/enums.py:614`, and `specific_gas_yield` accepts its supported yield units in `simulator/battery/migrate.py:11461-11527`. It is not rebound to a nearby yield token.

The table's theoretical columns 11–12 are excluded. They are the calculated weight loss assuming one mole SiO2 displaces one mole CO2. Three unnumbered charges remain in the row count, including the final numeric point. The final 998 °C point is stored as phase `cr`: the prose on PDF pages 17–18 says the exact K2CO3:SiO2 = 1:2 charge remains solid through 1000 °C and yields K2Si2O5 crystals; K2Si2O5 remains solid at 1005 °C and melts at 1025 °C. The other 29 numeric points are liquid-bearing.

The conditions prose on PDF page 7 states a current of CO2 and that its partial pressure was kept at 1 atm. The extract records that printed CO2 partial pressure, while total pressure and fO2 remain unknown. It does not turn pCO2 into total pressure. The composition axes preserve the printed wt% and SiO2 mol%; binary K2CO3 mole fraction is completed from the stated binary system for identity mapping.

## Schema-gap evidence

The Table II mass-loss measurement has an exact supported observation path: `OBSERVATION_TYPES` includes `mass_loss` in `tools/validate_literature_extracts.py:96-118`, `QUANTITY_ALIASES` includes `mass_loss_fraction` in `simulator/battery/migrate.py:511`, and printed `%` is converted through `_MASS_LOSS_YIELD_FIELDS` in `simulator/battery/migrate.py:6285-6295`.

The auxiliary CO2-displacement basis has no exact observation/quantity pair. `OBSERVATION_TYPES` is a closed set in `tools/validate_literature_extracts.py:96-118`; it includes `mass_loss` for Table II column (a), but no CO2-displacement type. `TYPE_QUANTITY` in `simulator/battery/migrate.py:442-451` maps generic types only to their named quantities; `QUANTITY_ALIASES` at lines 460-519 maps `specific_gas_yield` to `Quantity.EVOLVED_GAS_YIELD`, whose canonical unit is `mol_species_per_initial_kg` (`simulator/battery/enums.py:614`). The migrator accepts `yield_ug_g` or species-specific gas-yield fields (`simulator/battery/migrate.py:9493, 11461-11527`) and converts only µg/g or wt%/percent in that branch; none means the printed mol CO2 per 100 mol SiO2 basis. `PHASE_BASIS_TOKENS` in `simulator/battery/enums.py:676-681` includes `mole_fraction`, but that basis is for phase composition, not displaced gas per initial silica. Existing `equilibrium_phase_composition` records in Park (2021) hold actual phase-composition cells, not this gas-displacement quantity. Thus Table II column (b) and all 15 Table VI measured values remain descriptive context with the exact printed units and a cited schema gap; they are not rebound to a near-miss yield or composition token.

## Other printed values and exclusions

Table VI (PDF page 23 / printed page 1715) has 14 composition rows. Its 15 measured CO2-displacement numbers are retained as context because they use the unsupported mol/100 mol SiO2 basis. By row, the measured values are: 7.1 mol% SiO2, 898 °C = 174; 8.0, 898 °C = 165 and 956 °C = 182; 10.1, 898 °C = 151 and 956 °C = 169; 12.3, 898 °C = 139; 12.5, 898 °C = 137 and 956 °C = 160; 16.1, 898 °C = 135; 22.3, 956 °C = 144; 23.2, 956 °C = 140; 25.6, 956 °C = 141; 26.1, 956 °C = 138; 30.0, 956 °C = 135; 33.1, 956 °C = 134. The 37.1 mol% mixture has no reported numerical measurement. The tabulated equilibrium-species allocations are stoichiometric calculations and are excluded; the 50 mol% metasilicate endpoint is derived, not another run. The 30.0 mol% entry is a mean of two runs, as footnoted. Table VI QA digit agreement was 0.564; the render was used to verify each retained cell and the row/column assignment.

Table VIII (PDF page 33 / printed page 1725) has 14 body rows: six K-system rows, the system divider, and seven Na-system rows. For each row the values below are `original SiO2 mol%: Rotglut (Scheerer) / 956 °C (Niggli) / Gelbglut (Scheerer)`; numeric values remain in the printed mol CO2 per 100 mol SiO2 basis, and “All displaced” remains categorical.

| System | Original SiO2 mol% | Rotglut, Scheerer | 956 °C, Niggli | Gelbglut, Scheerer |
|---|---:|---:|---:|---:|
| K2CO3 + SiO2 | 3.0 | 114 | 96 | 129 |
| K2CO3 + SiO2 | 14.3 | 94 | 89 | 107 |
| K2CO3 + SiO2 | 20.0 | 89 | 87 | 104 |
| K2CO3 + SiO2 | 33.3 | 81 | 83 | 99 |
| K2CO3 + SiO2 | 50.0 | 76 | 73 | 83 |
| K2CO3 + SiO2 | 60.0 | 61 | 61.5 | — |
| Na2CO3 + SiO2 | 3.0 | — | 193 | 197 |
| Na2CO3 + SiO2 | 9.9 | — | 171 | 186 |
| Na2CO3 + SiO2 | 14.3 | 150 | 156 | 178 |
| Na2CO3 + SiO2 | 20.0 | 135 | 145 | 168 |
| Na2CO3 + SiO2 | 33.3 | 110 | 134 | 138 |
| Na2CO3 + SiO2 | 50 | 99 | 100 | 100 |
| Na2CO3 + SiO2 | 60 | All displaced | All displaced | All displaced |

The source identifies the two outer columns as Scheerer's results, “in part recalculated,” and the middle as Niggli's results obtained at 956 °C. The source also says Scheerer heated carbonate and silica in an open crucible over a flame; his CO2 partial pressure is unknown. “Rotglut” and “Gelbglut” have no exact temperature values. All values are retained as comparison context; no missing pressure or temperature is inferred. Table VIII's displaced-moles basis has the same schema gap as Table II column (b) and Table VI.

The adjacent Mallard quote on PDF page 33 / printed page 1725 gives two sodium-system compositions, in the order “mol% SiO2: dark red heat / red heat / higher temperature”: 20.0: 107 / 129 / 148; 8.7: 116 / 143 / —. It is attributed to Mallard, *Ann. chim. phys.*, [4] 28, 250 (1873). These five numeric cells remain context because the quantity basis has no exact token and the temperature labels are qualitative. Niggli says Mallard's mean temperature was somewhat below 900 °C and highest temperature about 950 °C; these statements are not assigned to specific cells. Table VIII and the adjacent Mallard rows were checked on the page render, including OCR forms `I07`, `no`, and `IOO`.

Lebeau's quoted pure K2CO3 decomposition pressures (PDF page 14 / printed page 1706) are left as context: the source writes only “Equil. pressure in mm.” The six rendered pairs are 790 °C/0.5 mm, 810 °C/1 mm, 890 °C/3 mm, 960 °C/5 mm, 970 °C/9 mm, and 1000 °C/12 mm. The migration unit parser maps `mm` as length in `_normalize_printed_unit` (`simulator/battery/migrate.py:3420, 4911-4950`), while pressure parsing requires an explicit pressure unit such as mmHg/Torr (`simulator/battery/migrate.py:4868-4902`). Therefore the extract preserves “mm” and does not create a pressure observation or assume mmHg.

Niggli's K2CO3 melting point 891 °C (PDF page 17) is an observation with `transition_temperature`, `measured_direct`. The same rendered paragraph lists six literature values, all stored as separate `quoted_attributed` transition observations for K2CO3: Quincke 1200 °C; V. Meyer 878.6 °C; Carnelley 834 °C; Le Chatelier 885 °C; Ramsay and Eumorfopoulos 886 °C; Hüttner and Tammann 894 °C. Render checked these values, including the pdftotext/MinerU disagreement on 886 °C.

The K2Si2O5 melting estimate 1015 ±10 °C (PDF page 18 / printed page 1710) is a `quoted_attributed` transition point attributed to Niggli. He reports crystalline samples still completely solid at 1005 °C and all melted at 1025 °C, then states the estimate. The bracket inputs are kept in a cited context record. The focused migration showed that this qualitative solid/liquid bracket cannot be a `derived_from` parent: `QUANTITY_SOURCE_FIELDS[Quantity.TRANSITION_TEMPERATURE]` in `simulator/battery/migrate.py:9495-9502` names only scalar temperature fields, and the transition selector at lines 10912-10922 emits a point. Because those endpoint observations cannot be stored as separate transition observations, the reported estimate follows the quoted-attributed fallback rather than inventing lineage. The point and printed width are retained, with confidence level not stated. The render confirms the ± mark and both limits. No numeric eutectic temperature is given for K2Si2O5–K2SiO3; only a qualitative statement that the binary melting curve is rather flat.

On PDF page 25 / printed page 1717, the attributed Na2SiO3 melting point is 1018 °C (Wallace, *Z. anorg. Chem.*, 63, 1 (1909)); the attributed Na2CO3 melting point is 853 °C (Hüttner and Tammann, *Z. anorg. Chem.*, 43, 215 (1905)). Both are `quoted_attributed` transition observations. The render confirms the digits and citations. The sodium eutectic is only described as very close to pure Na2CO3; no numeric temperature is stated.

No numerical eutectic temperature is printed. The potassium mixtures with little silica exhibit a thermal effect “about 880°” on heating, but Niggli could not determine whether it is the K2CO3–K2SiO3 eutectic; it remains context without an assigned transition type. The sodium eutectic is described only as very close to pure Na2CO3. Table III (time-course), Tables IV–V (derived calculations), and Table VII (melt-composition calculations) are not independent measured tabulations for this extraction.

Figure-read candidates, not digitized: Fig. 2 (K-system CO2 displaced vs. initial SiO2 mol% at 898, 956, and 998 °C); Fig. 3 (K-system CO2 loss fraction vs. composition); Fig. 4 (K-system charge weight/time curve); Fig. 5 (calculated K-system melt components); Fig. 6 (K-system equilibrium constants vs. composition); Fig. 7 (Na-system CO2 displaced vs. composition); Fig. 8 (Na-system CO2 loss fraction vs. composition); Fig. 9 (calculated Na-system melt components); Figs. 10–11 (ternary melt-composition diagrams for the alkali systems at 898 and 956 °C). Fig. 1 depicts the furnace apparatus and is not a data candidate.

## Render audit index

| PDF page | Printed location | Render use |
|---:|---|---|
| 7 | K2CO3 experiment conditions | Confirmed CO2 current and pCO2 maintained at 1 atm. |
| 8 | Table II | Confirmed all retained cells, full headers, row labels, and column assignment after low QA agreement. |
| 14 | Lebeau footnote | Confirmed values and ambiguous “mm” unit. |
| 17 | K2CO3 transition and literature comparisons | Confirmed Niggli 891 °C and Ramsay/Eumorfopoulos 886 °C. |
| 18 | K2Si2O5 transition | Confirmed 1005 °C solid, 1025 °C all melted, and 1015 ±10 °C estimate. |
| 22 | Sodium system conditions | Confirmed one atmosphere of CO2. |
| 23 | Table VI | Confirmed all 15 measured cells and row/column assignment after low QA agreement. |
| 25 | Sodium carbonate/silicate melting points | Confirmed 1018 °C and 853 °C values and attributions. |
| 33 | Table VIII | Confirmed historical comparison values, qualitative labels, and row assignment. |

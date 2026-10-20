# Shornikov 2017 spinel thermodynamics — extraction audit

Source: Shornikov, S. I. (2017), “Thermodynamic Properties of Spinel MgAl2O4: A Mass Spectrometric Study,” *Russian Journal of Physical Chemistry A* 91(2), 287–294. DOI 10.1134/S0036024417020303. Extract: [shornikov-2017-spinel-thermodynamics.yaml](shornikov-2017-spinel-thermodynamics.yaml).

This is not the evaporation paper already extracted as `shornikov-2017-spinel-evaporation` (91(1), 10–16, DOI 10.1134/S0036024417010241). The title, volume number, and DOI differ. PDF page 7, published page 293, prints reference [1] as “S. I. Shornikov, Zh. Fiz. Khim. 90 (2016, in press).” The identity with the evaporation paper is an inference from the 1851–2089 K span, not the citation this paper prints.

## Tables

MinerU `convert` of the content-list JSON wrote `p002-t01` (PDF page 2, published 288) and `p004-t02` (PDF page 4, published 290). `emit` was given only the this-work rows: Table 1 CSV row 29 and Table 2 CSV row 14. It wrote five cells and skipped nothing. `pdftotext -layout` agrees with MinerU on every digit of those cells.

The ± cells are not emit traps. Emit stored the whole cell, for example `-12.02 ± 1.14`, and flagged `non_numeric_in_numeric_column`. After the render check the committed rows split the cell into `value` and `uncertainty`, and that flag was removed. The confidence level is not stated.

Table 1’s temperature cell is the range `1851–2298`, so it was not mapped as `T_K`. The committed rows carry `T_range_K: [1851, 2298]` and an unknown point temperature. No midpoint is stored.

Table 2’s four MgO body rows were tagged `header` because of a rowspan. In the scratch draft those cells were retyped `body` so `rows: [14]` could select the this-work row without emit forcing the MgO numbers out. The emitted column headers still contained those MgO cells. The 600 dpi render shows the real headers are `T_melt`, `ΔH_melt`, and `ΔS_melt`. The committed locators use those headers.

The Table 1 minus sign is U+2013 in pdftotext (`–12.02`). MinerU stored an ASCII hyphen. The render shows a minus. `value` is `-12.02`. `value_as_printed` keeps the en dash.

`transition_temperature` is read from `T_K`. Emit had put `2420 ± 25` on `values.value`. The committed melting-point row puts `2420` on `T_K` and `T_m_K`, with `property_kind: melting_point`, and keeps `2420 ± 25` as `value_as_printed`.

The Table 2 caption prints `J/(mol K)`. The melting-entropy row stores `J/mol K`, the same unit, because the migrator’s unit token does not accept the parentheses. `units_as_printed` keeps the caption spelling. Digits are unchanged.

## Render checks

pdftotext `-bbox` on this file uses a top-left origin. Page renders are 600 dpi (600/72 px per point). An earlier crop that flipped that origin hit the top of each page and was discarded.

| PDF page | Published page | What was read | Crop |
|---:|---:|---|---|
| 2 | 288 | Table 1 this-work row: Knudsen mass spectrometry, 1851–2298, −12.02 ± 1.14, 5.03 ± 0.56, This work | top-origin band covering that row and its neighbours |
| 4 | 290 | Table 2 MgAl2O4 block. The last row before Al2O3 is 2420 ± 25, 55.81 ± 4.62, 23.06 ± 1.73, This work | the whole Table 2 body |
| 6 | 292 | Prose: ΔH_T = 43.79 ± 4.84 kJ/mol and ΔS_T = 28.09 ± 1.64 J/(mol K), liquid state, equation (19) | the paragraph that prints those constants |
| 5 | 291 | Figure 4 series 8. Open right-pointing triangles, no bar, both panels, including the 1917 K marker on each panel | the figure crop, then each stored centre again |
| 3 | 289 | Figure 2 series 16. Seven open right-pointing triangles. The 2210 K point was read on both the 600 dpi and the 1200 dpi render | the high-T chain at 2×, the marked centres, and a 1200 dpi crop of the same chain |

Neighbour rows on pages 2 and 4 match pdftotext, so the this-work row is not a shifted literature row.

A consistency check, not a new observation: 43.79 − (−12.02) = 55.81 and 28.09 − 5.03 = 23.06, and 55.81 / 23.06 is 2420 K to the printed kelvin. That is equations (20)–(22).

## What was admitted

Five scored rows, all `measured_reduced`, stoichiometric MgAl2O4 (MgO 0.5, Al2O3 0.5). Total pressure and fO2 are not printed.

- Table 1 ΔH_T = −12.02 ± 1.14 kJ/mol, crystalline spinel, 1851–2298 K. Quantity `delta_fH`.
- Table 2 ΔH_melt = 55.81 ± 4.62 kJ/mol at the printed melting temperature 2420 K. Quantity `heat_of_fusion`.
- Table 2 ΔS_melt = 23.06 ± 1.73 J/(mol K) at 2420 K. Quantity `entropy_of_fusion`.
- Table 2 T_melt = 2420 ± 25 K. Quantity `transition_temperature`, `property_kind: melting_point`.
- Prose liquid-state ΔH_T = 43.79 ± 4.84 kJ/mol. Quantity `delta_fH`, phase `l`. The sentence does not print a temperature, so the point temperature stays unknown.

Ten Figure 4 activities, `figure_only`, stoichiometric spinel. Quantity `activity` on type `activity_coefficient`. Uncertainty is the read, not a printed ±.

- Panel (a) a(MgO): 0.276 at 1917 K, 0.368 at 2161 K, 0.388 at 2218 K, 0.406 at 2257 K, 0.425 at 2304 K. Read uncertainty ±0.010 and ±5.4 K.
- Panel (b) a(Al2O3): 0.236 at 1917 K, 0.211 at 2162 K, 0.202 at 2220 K, 0.203 at 2259 K, 0.200 at 2306 K. Read uncertainty ±0.005 and ±5.4 K.

Seven Figure 2 Gibbs energies, `figure_only`, quantity `delta_fG`, crystalline spinel. Read uncertainty ±0.30 kJ/mol and ±10 K.

- −22.32 kJ/mol at 2004 K, −22.41 at 2050 K, −22.70 at 2102 K, −22.92 at 2160 K, −23.47 at 2210 K, −23.39 at 2252 K, −23.61 at 2302 K.

The 2210 K point was the disagreement between the two confirm reviews. Sol called it readable. The other review called the cut interior a fair refusal. Both crops locate the same centre. At 600 dpi the midpoint is crop (1873.1, 1068.5), page pixel (3214, 1504), 2209.62 K and −23.469 kJ/mol. At 1200 dpi the white centroid is page pixel (6428, 3007), which falls at crop (1873.0, 1068.2) in the 600 dpi frame, 2209.49 K and −23.464 kJ/mol. The outline is a complete open right triangle. The next outline meets the tip and does not hide the centre. The point is stored.

The 2252 K point is the marker the earlier note called a gap near crop (1926, 1065). The 600 dpi midpoint is crop (1921.5, 1064.6), page pixel (3263, 1500), 2252.04 K and −23.387 kJ/mol. The 1200 dpi white centroid is page pixel (6521, 2999), crop (1919.6, 1064.5) in the 600 dpi frame. The two reads differ by about 2 px.

## What was refused

Two formation entropies are stored and marked `typed_refusal`. The schema has no `delta_fS` token. They are not stored as absolute entropy `S`.

- Table 1 ΔS_T = 5.03 ± 0.56 J/(mol K), 1851–2298 K.
- Prose liquid-state ΔS_T = 28.09 ± 1.64 J/(mol K).

## Context only

- Table 1: 30 literature rows (calorimetry, EMF, thermochemical calculations, earlier KEMS). Not this paper’s measurements.
- Table 2: 15 literature melting rows (4 MgO, 8 other MgAl2O4, 3 Al2O3). Not this paper’s measurements.
- Figure 1: literature heat capacity. Not read.
- Figure 2: pointwise ΔG. Series 16 is the open right-pointing triangle. Seven centres were stored, including 2004 K, 2210 K, and 2252 K. Refused markers are named one by one in the figure-2 context row: squares near 1420 K, up-triangles near 1640 K and 1780–1840 K, a fit line that hides the centre near 1863 K, and a shared interior near 1950 K. Error-bar ends were not read. The least-squares constants remain Table 1. MinerU’s chart table was ignored.
- Figure 3: pressures over spinel cited as reference [1], the evaporation paper already extracted. Not re-read.
- Figure 4: activities. Linear axes, calibrated from the tick marks on the 600 dpi render of PDF page 5. Series 8 is the open right-pointing triangle with no bar. Ten centres were stored, five on each panel. The new ones are the open triangles at 1917 K. Read uncertainty is ±10 px (±5.4 K, and ±0.010 or ±0.005 in activity). Each refused marker is named with its approximate temperature in the figure-4 context row: a vertical bar (series 5 or 6), a filled triangle (series 7), an up-triangle (series 1–4), a legend key, or a centre hidden in a shared mass. MinerU’s scatter table was ignored.
- Figure 5: MgAl2O4–SiO2 phase diagram cited to [55]. Not this paper’s measurements. MinerU’s contour table was ignored.

## Standard state

Two activity rows were read from Figure 4, series 8. Both carry the equation (12)–(13) sentence.

Equations (12)–(13), PDF page 5, published page 291: “where p_i and p_i° are the partial pressures of the components of the gas phase over spinel and simple oxide (pure component), respectively.” The prose says the phase of that pure-component oxide is not stated, and the temperature of the reference measurement is not stated. `reference_state_from_extract` returns unknown on “not stated” before it looks for a Raoultian endmember. The prose does not say “Raoultian” and it does not name pure solid or pure liquid MgO, so `_standard_state_from_extract_text` does not replace that unknown. The points from 2161 K upward lie inside the crystal-state interval 2048–2298 K that the paper takes from reference [54] by equations (16) and (17). The 1917 K point on each panel is below that interval. The paper does not say the pure oxide was measured in the cell at the experiment temperature, at either temperature. The 2026-10-20 pure-oxide-at-T ruling was not applied. No melting point was used. “Crystal state” names the spinel. `phase: cr` is that spinel phase.

“Crystalline” and “liquid” in equations (20)–(22) and in the liquid-state paragraph are the spinel phase, not the oxide reference. The formation rows say the paper does not state the oxide phase. That prose is not on an activity row, so the activity mapper does not read it.

The cell metal is not restated. The final introductory paragraph on published page 290 (PDF page 4) points to reference [52]. It is not copied from the 2016 conference abstract. Crystalline is printed in the conclusion on PDF page 7 and as crystal-line (sol) beside equations (20) and (21) on PDF page 6. The Table 1 caption does not say crystalline. Equation (19) is the fit ΔG_T = ΔH_T − TΔS_T.

## Not run here

No pytest and no `scripts/battery_migrate.py`. The studio commands are in the seat report.

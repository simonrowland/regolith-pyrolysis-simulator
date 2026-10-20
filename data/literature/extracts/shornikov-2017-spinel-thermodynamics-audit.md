# Shornikov 2017 spinel thermodynamics — extraction audit

Source: Shornikov, S. I. (2017), “Thermodynamic Properties of Spinel MgAl2O4: A Mass Spectrometric Study,” *Russian Journal of Physical Chemistry A* 91(2), 287–294. DOI 10.1134/S0036024417020303. Extract: [shornikov-2017-spinel-thermodynamics.yaml](shornikov-2017-spinel-thermodynamics.yaml).

This is not the evaporation paper already extracted as `shornikov-2017-spinel-evaporation` (91(1), 10–16, DOI 10.1134/S0036024417010241). The title, volume number, and DOI differ. Reference [1] of this paper is that evaporation study.

## Tables

MinerU `convert` of the content-list JSON wrote `p002-t01` (PDF page 2, published 288) and `p004-t02` (PDF page 4, published 290). `emit` was given only the this-work rows: Table 1 CSV row 29 and Table 2 CSV row 14. It wrote five cells and skipped nothing. `pdftotext -layout` agrees with MinerU on every digit of those cells.

The ± cells are not emit traps (`non_numeric_in_numeric_column` only). Emit stored the whole cell, for example `-12.02 ± 1.14`. After the render check the committed rows split the cell into `value` and `uncertainty`. The confidence level is not stated.

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

Neighbour rows on pages 2 and 4 match pdftotext, so the this-work row is not a shifted literature row.

A consistency check, not a new observation: 43.79 − (−12.02) = 55.81 and 28.09 − 5.03 = 23.06, and 55.81 / 23.06 is 2420 K to the printed kelvin. That is equations (20)–(22).

## What was admitted

Five scored rows, all `measured_reduced`, stoichiometric MgAl2O4 (MgO 0.5, Al2O3 0.5). Total pressure and fO2 are not printed.

- Table 1 ΔH_T = −12.02 ± 1.14 kJ/mol, crystalline spinel, 1851–2298 K. Quantity `delta_fH`.
- Table 2 ΔH_melt = 55.81 ± 4.62 kJ/mol at the printed melting temperature 2420 K. Quantity `heat_of_fusion`.
- Table 2 ΔS_melt = 23.06 ± 1.73 J/(mol K) at 2420 K. Quantity `entropy_of_fusion`.
- Table 2 T_melt = 2420 ± 25 K. Quantity `transition_temperature`, `property_kind: melting_point`.
- Prose liquid-state ΔH_T = 43.79 ± 4.84 kJ/mol. Quantity `delta_fH`, phase `l`. The sentence does not print a temperature, so the point temperature stays unknown.

## What was refused

Two formation entropies are stored and marked `typed_refusal`. The schema has no `delta_fS` token. They are not stored as absolute entropy `S`.

- Table 1 ΔS_T = 5.03 ± 0.56 J/(mol K), 1851–2298 K.
- Prose liquid-state ΔS_T = 28.09 ± 1.64 J/(mol K).

## Context only

- Table 1: 30 literature rows (calorimetry, EMF, thermochemical calculations, earlier KEMS). Not this paper’s measurements.
- Table 2: 15 literature melting rows (4 MgO, 8 other MgAl2O4, 3 Al2O3). Not this paper’s measurements.
- Figure 1: literature heat capacity. Not read.
- Figure 2: pointwise ΔG. Series 16 (this study) sits on the same trend as series 13 and 15. The least-squares result is already Table 1. No point was read. MinerU’s chart table was ignored.
- Figure 3: pressures over spinel cited as reference [1], the evaporation paper already extracted. Not re-read.
- Figure 4: activities. Linear axes. Series 8 (this work) is open right-pointing triangles. Series 5–7 are the same family and overlap series 8 from about 1850 K to 2300 K on both panels. No marker can be assigned to this work within a stated activity uncertainty. No activity number is stored. MinerU’s scatter table was ignored.
- Figure 5: MgAl2O4–SiO2 phase diagram cited to [55]. Not this paper’s measurements. MinerU’s contour table was ignored.

## Standard state

No activity row was admitted, so migration is not asked to resolve a Raoultian endmember from this extract.

Equations (12)–(13), PDF page 5, published page 291: “where p_i and p_i° are the partial pressures of the components of the gas phase over spinel and simple oxide (pure component), respectively.” The paper does not say whether that pure-component oxide is solid or liquid, and it does not say that the reference pressure was measured in the cell at the experiment temperature or tabulated at that temperature. The 2026-10-20 pure-oxide-at-T ruling was not applied. No melting point was used. The figure-4 context row records that sentence. It does not say “Raoultian” and it does not name pure solid or pure liquid MgO.

“Crystalline” and “liquid” in equations (20)–(22) and in the liquid-state paragraph are the spinel phase, not the oxide reference. The formation rows say the paper does not state the oxide phase. That prose is not on an activity row, so the activity mapper does not read it.

The cell metal is not restated. The paper points to reference [52]. It is not copied from the 2016 conference abstract.

## Not run here

No pytest and no `scripts/battery_migrate.py`. The studio commands are in the seat report.

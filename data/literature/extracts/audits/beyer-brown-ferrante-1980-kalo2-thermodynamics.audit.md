# Beyer, Ferrante & Brown (1980) KAlO2 extraction audit

## Input and method

Transcription followed the machine-input order: MinerU table blocks and Markdown, then `pdftotext-layout.txt`. The article is born digital, so the pdftotext layer supplies exact characters where legible; the rendered PDF pages 4 and 5 were inspected to verify every table's row/column assignment and to settle MinerU/pdftotext disagreements. The six `Series` groupings in Table 1 were also reconciled to the corpus correction note: Series I 10 points, Series II 12 points, Series III 32 points, total 54 experimental Cp pairs.

The MinerU pre-screen reported numeric disagreements on Table 1 (3), Table 2 (2), and the Table 3 continuation on p. 989 (10). All were checked against the rendered page. Table 1 examples resolved from the render are 7.76 K / 0.067, 8.53 K / 0.096, and 42.81 K / 11.799; pdftotext had `1.76`, `x.53`, and `42.X1`. Table 2 resolves the 704.0 K deviation as +8 (pdftotext `f8`) and the 802.9 K deviation as +527 (pdftotext +521). On the Table 3 continuation the render resolves OCR substitutions including 18.69, 51.87, 30.18, 8478, 36.48, 80.85, 45187, and 87810. The remaining reported mismatches were likewise checked cell-by-cell on p. 989; none remain unresolved.

The printed table count and extraction count reconcile as follows:

| Printed table | PDF pages | Printed rows or pairs | Numeric body cells | Extracted points |
|---|---:|---:|---:|---:|
| Table 1: experimental Cp | 4 | 54 pairs | 108 | 54 |
| Table 2: experimental enthalpy increments | 4 | 16 rows | 48, including the deviation column | 16 |
| Table 3: smoothed functions | 4–5 | 59 rows (7 + 52) | 295 | 59 per column, four columns |
| **Total** | | | **451** | **306 table values plus 16 retained deviations** |

Table labels in every table locator are the printed caption number. PDF `page` values are absolute PDF pages, distinct from printed article pages. The table draft/compare pre-screen recognizes each of the 306 stored table values: 306 matched, 0 mismatch, 0 locator error, 0 unverifiable; the single no-match in the comparison output is the prose-derived 1295 J/mol transition enthalpy, which is not a table cell.

## Classification and interpretation

- Table 1 records 54 authors' direct adiabatic-calorimetry Cp values, in J/(mol K), per mol KAlO2, with printed chronological series labels preserved.
- Table 2 records 16 authors' direct drop-calorimetry H°(T)-H°(298.15 K) increments in J/mol, per mol KAlO2. `reference_temperature_K: 298.15` is explicit. Its δH column is retained as each point's `deviation_from_smoothed_curve_J_per_mol_as_printed`; footnote a defines it as fit deviation, not uncertainty. The three b-marked transition-region values remain present and flagged as excluded from the curve fit.
- Table 3 is the authors' measured-reduced smoothing of the stored Table 1 and Table 2 inputs using Justice FITAB. Its printed Cp column uses the exact `cp` quantity. The other three columns are preserved as typed-unavailable data rather than assigned near-miss tokens: `S°(T)-S°(0)` is not absolute `Quantity.S`; `H°(T)-H°(0)` is not `Quantity.H_MINUS_H298`; and `-{G°(T)-H°(0)}/T` is a thermal function, not formation `Quantity.DELTA_FG`. The closed token check used `simulator/battery/enums.py::Quantity.S`, `simulator/battery/enums.py::Quantity.H_MINUS_H298`, `simulator/battery/enums.py::Quantity.DELTA_FG`, and `simulator/battery/migrate.py::QUANTITY_ALIASES`.
- The paper reports an observed reversible first-order transition at 810 K. Its 1295 J/mol transition enthalpy is calculated under the first-order assumption, and is retained as a located typed refusal because no exact generic transition-enthalpy quantity token exists; `heat_of_fusion` is not equivalent.
- The authors state that their HCl-calorimeter formation enthalpy values were rejected because the final solution was cloudy. No accepted ΔfH or ΔfG is extracted. Table 3's G function is not ΔfG.
- Sample preparation, single-phase XRD result, unit-cell parameter, Na impurity and chemical analysis are located context. No fusion or melting value is claimed.

The highest direct enthalpy measurement is 1197.8 K. Measurements stopped after capsule failure; Table 3's extension to 1400 K is fitted tabulation, not direct measurement. No pressure or oxygen fugacity is assigned to Table 3. The direct series retain only the helium pressures explicitly printed for their calorimeters: 6.7 kPa for the low-temperature Cp series and 101 kPa for the drop-calorimeter series. Temperature is retained at each row. For the Cp and H-minus-H298 quantity profiles, identity composition, total pressure, and fO2 are marked not applicable by the profile; the printed pressure remains present in each migrated observation's `point_conditions`.

## Migration and expected store delta

The isolated real migrator yields 308 observations: 130 with closed numeric quantities and 178 unavailable because their exact printed forms lack quantity tokens (59 points in each of three Table 3 columns plus one transition-enthalpy result). It reports zero hard issues and zero unknown phases. The only quantity-unknown observation IDs are `beyer80_table3_entropy_increment_from_zero`, `beyer80_table3_negative_gibbs_function_over_T`, `beyer80_table3_enthalpy_increment_from_zero`, and `beyer80_transition_enthalpy_schema_gap`. No battery-store regeneration is committed; expected store delta is +308 observations, including the stated typed-unavailable records.

Three migrated rows were inspected for identity and condition mapping: Table 1 Cp at 51.58 K; Table 2 enthalpy increment at 406.1 K; and Table 3 Cp at 5 K. Temperature reaches each identity (the transition-point temperature itself is represented as the `transition_temperature` value with a located `temperature_K` point condition); helium pressure reaches the direct-row `point_conditions` at 6700 Pa and 101000 Pa respectively. Composition and fO2 remain not applicable/unknown because the source does not print them.

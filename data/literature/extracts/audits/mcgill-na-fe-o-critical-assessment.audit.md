# Moosavi-Khoonsari and Jung (2016) extraction audit

## Source distinction and thesis overlap

The article remains its own source, distinct from `moosavi-khoonsari-2015-na-fe-cmas-thesis`, with the article DOI and its own page/table locators. The journal Tables I–VI duplicate the corresponding assessment tables in thesis Tables 5.1–5.6: Table I ↔ 5.1, Table II ↔ 5.2, Table III ↔ 5.3, Table IV ↔ 5.4, Table V ↔ 5.5, and Table VI ↔ 5.6. This extract does not merge or replace the thesis source.

The article reports 15 Na-Fe-O stoichiometric compounds and discusses three optimized NaFeO2 polymorphs (a/α, b/β, c/γ). The later reported δ form is not included in the optimization because transition thermochemistry was unavailable. Figure-only curves were not digitized. The printed Table II says the Cp function is common to all three modeled NaFeO2 polymorphs.

Table I is a crystal-structure inventory rather than thermochemistry and is not extracted as a data table. Its overlap with thesis Table 5.1 is included in the source-duplication audit above.

## Within-paper NaFeO2 comparison

| Property | Other-work value quoted in this paper | Current assessment in the paper |
|---|---|---|
| ΔfH°298 | −698.02 ± 1.30 kJ/mol, Koehler et al. [28], solution calorimetry | −697.350 kJ/mol, Table II |
| S°298 | 88.34 ± 0.84 J/mol-K, King [32], adiabatic calorimetry | 88.341 J/mol-K, Table II |
| Cp | King [32] low-temperature data and Margrave and Grimley [31] / Christensen et al. [27] heat-content data considered; no numerical figure points extracted | `110.95 + 0.0076933T − 2552200T^-2` J/mol-K; assumed for α, β, γ |
| α→β / β→γ transition enthalpy | Christensen et al. [27] data are the stated experimental basis; the text summarizes the derived values as nearly 0 and 2.177 kJ/mol | 41.84 J/mol at 870 K; 2177 J/mol at 1270 K, Table II |
| Fusion temperature | 1620 K (1347 °C), Table IV references [27, 28] | 1620 K (1347 °C), Table IV |
| Fusion enthalpy | No numeric value printed in text or tables | No number transcribed from Figure 3(a) |

## Full-cell audit

Machine reads came from MinerU table HTML/markdown and the born-digital pdftotext layout. Table layout and all populated numeric cells below were checked against page renders; the render resolves flagged/merged table cells. Decimal digits, signs, uncertainties, exponents, row assignment, and column assignment were checked. No numeric cell remains deferred.

| Table / page | Printed body rows | Cells checked against render | Resolution |
|---|---:|---|---|
| II, PDF p. 5 / printed p. 580 | 33 | Rows 1–33: all populated ΔH°298, S°298, Cp, technique, and reference cells; merged phase labels and the spanning transition cell | (b) render. The Table II row assignment and stacked Cp ranges were checked on the rendered page. The NaFeO2 quoted values and optimized values are pinned by the focused test. |
| III, PDF p. 6 / printed p. 581 | 25 | Every numeric coefficient, H298, S298, Cp expression, exponent, temperature bound, and coordination number in both solution sections | (b) render. The rendered equation cells resolve MinerU LaTex / numeric-mismatch flags, including the Fe2O3 Cp exponents. |
| IV, PDF p. 7 / printed p. 582 | 9 | Rows 1–9: experimental and calculated T, reaction labels, Na2O Exp./Calc. composition cells, and references | (b) render. Exp./Calc. columns and en-dash empty cells confirmed. |
| V, PDF p. 12 / printed p. 587 | 9 | Rows 1–9: experimental and calculated T, reaction labels, and Na2O Exp./Calc. composition cells | (b) render. All digits, ± widths, signs, and empty Exp. cells confirmed. |
| VI, PDF p. 14 / printed p. 589 | 11 | Rows 1–11: T, phase assemblage, Na2O, Fe2O3, and FeO composition cells | (b) render. All digits and species/column assignments confirmed. |

The Table III Fe2O3 solution entropy `873.77363144` has an unusual magnitude, but the 600 dpi render confirms those exact printed digits; it is preserved verbatim.

MinerU QA pre-screen agreement was 0.883 for Table II, 0.726 for Table IV, 0.657 for Table V, and 0.648 for Table VI; Table III contained LaTex and numeric-mismatch flags. Each flagged table was rendered and resolved above. Table II's caption and first-to-last body-row layout were confirmed on the render; it contains 33 printed body rows. Tables IV, V, and VI contain 9, 9, and 11 printed rows respectively.

The table-draft compare matched the quoted Table II enthalpy (row 23, column 2) and entropy (row 22, column 3). It reported one mismatch for Table IV's experimental fusion temperature because the cell contains the paired text `1620 (1347)`; the page render confirms the printed pair is 1620 K (1347 °C), and the stored point is 1620 K. The complete compare output is committed as the companion CSV. The compare tool does not split paired K/°C cell values.

## Interpretation and classification

- Table II measurements and estimates from other authors are `quoted_attributed`; the reference and technique fields remain with each listed value. Values marked “this work” are outputs of the authors' thermodynamic assessment (`model_derived`). The source footnote's conversions from constituent-oxide to elemental basis are retained as notes, not recomputed.
- Tables IV and V distinguish quoted experimental columns from the authors' calculated columns. Table VI contains calculated invariant points. Printed composition values remain mol pct; temperatures retain the printed K and °C pairs.
- Table III's optimized equations and Table II's equation-only values are preserved as located model context. Figure 3 heat-content/Cp/Gibbs-energy curve values, including plotted emf values, and all other figure-read candidates are excluded.
- The Table II transition-enthalpy cells are retained as context because no exact transition-enthalpy quantity token exists in `simulator/battery/enums.py::Quantity`; `Quantity.ENTHALPY_OF_FUSION` is not the solid-solid transition quantity. The extract does not mislabel these values as formation or fusion enthalpy.
- No experimental pressure, oxygen partial pressure, composition, or temperature is attached to an invariant row unless printed in its table/caption. In particular, Table VI does not state an oxygen fugacity.

## Isolated migration

The focused NaFeO2 slice migrates 5 observations with 0 hard issues and 2 unavailable values. The two solid-solid transition enthalpies are typed-refused because the schema has no exact quantity token; the numeric values and transition details remain in their source rows and context. The ΔfH identity's `formation_elements` and `reaction` remain unknown because the paper labels the number as standard formation enthalpy but does not print its element-reference list or balanced formation reaction. The ΔfH and S identity standard pressure is 101325 Pa, converted from the explicitly printed 1 atm standard reference; that reference pressure is not an experimental total-pressure condition. The full-table context rows do not become synthetic observations.

The isolated migrator resolves a known phase and temperature condition on all five rows. Quantity is unknown only for the two typed-refused transition-enthalpy rows; no quantity-unknown or phase-unknown rows arise from token inference. For the ΔfH and S rows, identity temperature is 298 K and standard pressure is 101325 Pa, converted from the text's printed 1 atm standard reference; total pressure and fO2 are not applicable to these quantity profiles. The fusion row resolves phase `cr`, subtype `melting_point`, and point-condition temperature 1620 K; its total pressure remains unknown because Table IV does not print the conditions of the cited experiments.

Expected store delta relative to the seat base: +2 NaFeO2 observations, both unavailable typed-refused transition-enthalpy records; the extract now contains 5 observations total. No store regeneration was committed. The article extraction and focused test cover the printed Tables II–VI; complete thermochemistry, optimized parameters, and invariant inventories remain reviewable in the context records.

# Coughlin and O’Brien (1957) extraction audit

Source: *High Temperature Heat Contents of Calcium Orthosilicate*, Journal of Physical Chemistry 61(6), 767–769, DOI 10.1021/j150552a016.

## Coverage and cell review

- Table I (PDF page 2, journal page 768): 47 printed measured temperature/heat-content pairs. Its printed phase headings assign 18 gamma, 10 beta, 14 alpha-prime, and 5 alpha observations.
- Table II (PDF page 3, journal page 769): 20 printed temperature rows and 29 smoothed heat-content cells. Phase assignments follow the printed column headings. The gamma value at 1120 K is retained even though its equation’s stated range ends at 1100 K.
- The full table reader conversion and pre-screen were run from the seat copy. QA marked all 12 MinerU table blocks in the three-page chunk; Table I had 0.884 numeric agreement and Table II 0.923. The table’s multi-column layout and comma-grouped heat values made the emitted fragment unsuitable as final transcription; the extract was assembled from the conversion plus hand-resolved cells. Four `<details>` chart blocks were treated as invented figure tables and excluded.
- The compare output records 79 unmatched candidates. The comparison parser did not match the printed comma-grouped heat values to the parsed MinerU candidates, and the three transition enthalpies are in the discussion rather than a table block. Each mismatch was resolved against the rendered page, and the corrected cell values are in the extract. The machine comparison is retained at `coughlin-obrien-1957-calcium-orthosilicate-heat-content.compare.csv`.
- The audit TSV lists the 143 numeric temperature and heat-content cells in Tables I and II, plus 13 equation/discussion cells. The 29 printed entropy-increment cells in Table II were not extracted. Every listed cell was read from a render of its physical PDF page: page 2 for Table I and page 3 for Table II and the discussion. The full page layout was checked to confirm row and column assignments. The rendered readings settle discrepancies between MinerU and pdftotext, including comma-grouped values, signs/exponents, row assignments, and the two Table I footnote markers.
- Table I’s printed footnote `a` applies to 62,130 and 63,880 cal/mole and says “Shows pretransition effect.” Both markers and their text are retained.
- The four Table II equations, each printed mean deviation and temperature range, three transition temperatures, three transition enthalpies, and the three printed entropy/heat figures used to explain the indirect gamma transition heat are recorded in the extract. Equation and discussion cells are included in the audit TSV.
- Figure 1 is recorded as a figure-read candidate; no values were digitized from it.

## Conditions, lineage, and schema limits

- Table temperatures and heat contents are retained in the printed units and form. The paper defines the calorie as 4.1840 absolute joules; no converted value twins were added.
- The paper does not state experimental total pressure or standard pressure. No numerical pressure, ambient condition, or assumed pressure was added. Transition rows explicitly carry unknown total pressure because the source does not print it.
- The heat-content rows preserve the printed 298.15 K reference and reference polymorph: gamma references gamma, while beta, alpha-prime, and alpha reference beta. The extract records these printed references; the current migrator has no general mapping for this H-minus-H298 subtype, so migrated identity subtype remains unknown pending an owner-approved mapping.
- The paper gives alpha-prime as a phase label, but the closed polymorph schema has no alpha-prime token. The 14 Table I and 11 Table II alpha-prime heat-content observations retain their source phase labels and numeric values as typed-refused rows. The schema has no generic solid-solid polymorphic transition enthalpy token; the three transition enthalpies similarly retain their printed values and transition endpoints as typed-refused observations. No schema or migrator change was made.
- Table II lineage points from every smoothed row to the measured Table I rows of that phase. Each derivation relation names the printed phase equation; no smoothed Table II row is a parent.
- The gamma-to-alpha-prime transition enthalpy is described as indirect and based on E. G. King’s unpublished low-temperature heat-capacity measurements. Those inputs are not stored here, so the value is attributed rather than marked model-derived. It is not assigned a fabricated derivation parent.
- The source says measurements used previously described apparatus but does not specify a method token recognized by the migrator. The extract preserves that statement as a method note; the migration consequently queues method review instead of inventing a method.

## Isolated migration

The real literature migrator produced 82 observations, 0 hard issues, 28 unavailable values, 54 admitted values, and 28 pending values. It also reports 82 method-review queue entries because the source’s apparatus note does not map to a recognized method token; the method note remains in the extract. All 47 Table I and 29 Table II points map their printed temperature and its source locator. For representative gamma, beta, and Table II gamma rows, the required identity axes composition, total pressure, and fO2 are not applicable for this heat-content quantity, and temperature maps to the printed value. The standard-pressure identity axis remains unknown because the source does not state it. The 14 Table I and 11 Table II alpha-prime heat-content schema gaps plus three transition-enthalpy schema gaps account for the 28 unavailable values. No observation store was regenerated.

## Verification

The focused regression test pins four stored values to the rendered pages and checks the full table counts, including the footnote text. It also mutates a fidelity-pinned value and confirms that the fidelity matcher rejects the mutation. The isolated-migration test checks every tabular row’s temperature and source locator, the absent experimental pressure, the 82/0/28 counts, and representative identity axes.

The required repo-wide extraction suite has one existing failure unrelated to this extract: a validator test expects a repository-private provenance reference in nested prose to be accepted, but the unchanged prose validator rejects it. In the final run, 66 tests passed before `-x` stopped at this failure (235.55 seconds). The focused extraction tests and YAML comma-scalar guard passed together: 19 passed in 210.90 seconds.

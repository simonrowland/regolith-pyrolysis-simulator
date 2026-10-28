# Itoh & Yokokawa (1984) activity extraction audit

## Scope and row reconciliation

The requested observable is absolute Na2O activity. The paper reports one
absolute point in Eq. (5): `log alpha*_Na2O = -9.0`, pure solid Na2O reference,
`X_Na2O = 0.33`, at 1300 K. The extract has one absolute activity point,
represented by two linked observations. Following the controller ruling, the
extract stores a support row for the printed log activity (typed refusal because
no schema quantity represents that form) and a linked activity row storing
`10^(-9.0) = 1e-9`. The printed log value and locator remain on both rows. The
rounded one-decimal log value gives an activity half-width of
`1e-9 * ln(10) * 0.5 * 10^-1 = 1.1512925465e-10`; confidence level is not
stated.

Table 1 contains EMF observations for 27 listed compositions, all relative to
the 40Na2O·60SiO2 melt. Table 2 contains 28 rows of relative partial molar
quantities, also relative to that melt. Neither table prints absolute activity
values. Figures 1 and 5 contain figure-only activity/basicity information and
were not digitized. No table values were mislabeled as absolute activity.

## Cell audit

| Stored field | Source locator | Independent reads | Render check |
|---|---|---|---|
| `value_as_printed = -9.0`; activity `1e-9` | PDF p. 4 / printed p. 882, Eq. (5) | MinerU text and pdftotext both read `-9.0` | Render confirms minus sign, decimal, value, and Eq. (5) placement; linear activity is the controller-directed base-10 conversion |
| activity half-width `1.1512925465e-10` | derived from Eq. (5)'s one-decimal log print | Printed precision `d=1`; calculated as `a * ln(10) * 0.5 * 10^-d` | Confidence level not stated |
| `T_K = 1300` | PDF p. 4 / printed p. 882, Eq. (5) | MinerU text and pdftotext agree | Same page render |
| `X_Na2O = 0.33` | PDF p. 4 / printed p. 882, Eq. (5) | MinerU text and pdftotext agree | Same page render |
| `X_SiO2 = 0.67` | PDF p. 3 / printed p. 881, Eq. (3) | MinerU text and pdftotext agree | Render confirms the printed 0.67 coefficient in Eq. (3) |
| Pure solid Na2O standard | PDF p. 4 / printed p. 882, Eq. (5) | MinerU text and pdftotext agree | Render confirms “pure (solid) Na2O” |

The printed table captions and row/column layouts were checked against the
page renders: Table 1 is on PDF p. 2 and Table 2 on PDF p. 3. The table reader
produced duplicate blocks for two MinerU output paths; the duplicate is not a
second printed table. It also misclassified Table 1 composition headings as
headers and merged their cells. The tables are excluded from the absolute
activity values, so none of those raw EMF or relative-quantity cells is stored
as an activity observation.

## Evidence and conditions

The authors derive the absolute log activity from their concentration-cell
EMF measurements, Gibbs–Duhem integration, and the printed pure-oxide
thermochemical relation (Eqs. 1–5). Following the controller's ruling, the
printed log result is stored in a typed-refusal support observation because the
schema has no quantity token for the printed log form. The admitted linear
activity row is `measured_reduced`, related to its support row by
`a(Na2O) = 10^(log10 a(Na2O))`, with `derived_from` resolving to that sibling.
The propagated half-width is `1.1512925465e-10` from the one-decimal printed
log value; confidence level is not stated. The closed method token is
`emf_cell`, with the source
description retained in `method_as_printed`; the isolated migration binds it
to the experiment and has no unstated-method queue item. The paper does not
state total pressure or oxygen fugacity for this activity point; both remain
unknown. Their locators cite PDF p. 1 / published p. 879, the Experimental
section (not p. 2 / 880, which contains Table 1). The identity composition is
the printed binary point from Eqs. (3) and (5). The migrator's identity binding
reads `values.composition_mol`; the same composition is present under
`point_conditions.composition` with its state and locator.

## Tool audit

`mineru_table_draft.py convert` found six blocks across the two available
MinerU output paths: duplicate Table 1 pieces on PDF p. 2 and duplicate Table 2
blocks on PDF p. 3. `mineru_table_qa.py` showed poor numeric agreement with
pdftotext for the tables (0.028 / 0.024 and 0.066); the pdftotext page layout
does not carry the table cells. The page renders confirmed the table layouts;
this does not affect the typed absolute Eq. (5) point, independently read in
both machine texts and render-checked above.

`mineru_table_draft.py compare` returned 0 matched, 0 no-match, 0 mismatches,
and 0 total table points. The sole absolute activity is a located prose value,
not a table point, and is pinned by the focused fidelity test. The isolated
migration admits 1 activity observation with activity `1e-9`, method
`emf_cell`, and the expected identity conditions; the typed-refusal support row
preserves the printed log cell. Migration reports 0 hard issues.
Pressure and fO2 remain unknown.

The focused test pins the printed `-9.0`, converted activity `1e-9`, propagated
uncertainty, method, conditions, and source locators; it also mutates the
printed log value to prove the fidelity check detects a mismatch. It confirms
the isolated migration has 2 observations: 1 admitted activity point, 1
unavailable typed-refusal support row, and 0 hard issues. The single-file
extract validator passes with `--check-fidelity-match`; the YAML flow-scalar
guard passes all 17 tests. The repo-wide extraction suite reports 640 passed
and 2 failures at the configured 300-second timeout while loading the complete
corpus: `test_coverage_pending_stubs_not_found` and
`test_pilot_extract_count_exact_and_merge_smoke`. Their traceback is in
PyYAML parsing/loading at the timeout boundary, with no extract-specific
validation failure.

## Expected store delta

The expected store delta is one admitted numeric melt_activity observation
with activity `1e-9`, method `emf_cell`, composition and T, the Raoultian
pure-solid Na2O reference, and the propagated precision half-width. Pressure
and fO2 remain unknown. The typed-refusal support row is not admitted to the
store. No store regeneration is committed.

# Itoh & Yokokawa (1984) activity extraction audit

## Scope and row reconciliation

The requested observable is absolute Na2O activity. The paper reports one
absolute point in Eq. (5): `log alpha*_Na2O = -9.0`, pure solid Na2O reference,
`X_Na2O = 0.33`, at 1300 K. The extract has one observation row, matching the
one absolute point printed in the paper. The log value is retained as printed
and typed-refused because the current schema/migrator has no log10-activity
value token. No value was exponentiated.

Table 1 contains EMF observations for 27 listed compositions, all relative to
the 40Na2O·60SiO2 melt. Table 2 contains 28 rows of relative partial molar
quantities, also relative to that melt. Neither table prints absolute activity
values. Figures 1 and 5 contain figure-only activity/basicity information and
were not digitized. No table values were mislabeled as absolute activity.

## Cell audit

| Stored field | Source locator | Independent reads | Render check |
|---|---|---|---|
| `log10_activity_as_published = -9.0` | PDF p. 4 / printed p. 882, Eq. (5) | MinerU text and pdftotext both read `-9.0` | Render confirms minus sign, decimal, value, and Eq. (5) placement |
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
thermochemical relation (Eqs. 1–5). Since the current schema cannot store the
log form and the underlying EMF values cannot serve as stored activity
lineage-inputs, evidence is `quoted_attributed` under d-110. The printed method
is concentration-cell electromotive-force measurement. The paper does not
state the total pressure or oxygen fugacity for this activity point; both
remain unknown. The identity composition is the printed binary point from
Eqs. (3) and (5). The migrator's identity binding reads `values.composition_mol`;
the same composition is also present under `point_conditions.composition` with
its state and locator.

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
real migrator produced 1 observation, 0 hard issues, and 1 unavailable value.
The unavailable value is the expected typed refusal for the unhandled log10
activity form; the identity retains T, composition, pure-solid standard state,
and unknown pressure/fO2.

The focused migration/fidelity test passed (1 test). The combined required
suite run reported 655 passed and 4 failures caused by the configured 300-second
pytest timeout while loading the complete corpus: the comma/duplicate-key
loader guard, `test_repo_extracts_validate_green`, `test_coverage_pending_stubs_not_found`,
and `test_pilot_extract_count_exact_and_merge_smoke`. The traceback in each
failure was inside YAML parsing/loading at the timeout boundary; the run showed
no extract-specific validation failure.

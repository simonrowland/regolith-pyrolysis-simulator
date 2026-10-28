# Itoh & Yokokawa (1984) activity extraction audit

## Scope and row reconciliation

The requested observable is absolute Na2O activity. The paper reports one
absolute point in Eq. (5): `log alpha*_Na2O = -9.0`, pure solid Na2O reference,
`X_Na2O = 0.33`, at 1300 K. The extract has one observation row, matching the
one absolute point printed in the paper. Following the controller ruling, the
stored activity is `10^(-9.0) = 1e-9`, with the printed log value retained in
`value_as_printed`, `log10_activity_as_published`, and derivation lineage. The
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
thermochemical relation (Eqs. 1–5). The printed log cell is the measured-reduced
input to the base-10 conversion, and the row's method class is
`measured_reduced`. The closed method token is `emf_cell`, with the source
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
not a table point, and is pinned by the focused fidelity test. The real
migrator produced 1 admitted observation with activity `1e-9`, method
`emf_cell`, and the expected identity conditions. It currently reports 2 hard
conditional-field issues: `measured_reduced` requires resolved `derived_from`
lineage, and the schema separately records that `printed_unresolved` inputs do
not resolve to a record or asset. The only printed conversion input is Eq.
(5)'s log cell; the paper cannot be used as its own derivation input, and the
relative EMF table is outside this extract's requested absolute-activity observation.
Resolving this requires changing the derivation schema/validator or changing
the controller-directed evidence class. Both are outside this extraction's
authorized scope, so migration acceptance is blocked pending controller
ruling. Pressure and fO2 remain unknown.

The original focused migration/fidelity test passed before the review change.
After the FIX-FIRST changes, the YAML flow-scalar guard passed (17 tests). The
focused test pins the converted value, printed input, method, locators, and
both explained lineage issues described above. The single-file extract validator passes with
`--check-fidelity-match` (`OK: 1 extract file(s) valid`). The combined required
suite run reported 655 passed and 4 failures caused by the configured 300-second
pytest timeout while loading the complete corpus: the comma/duplicate-key
loader guard, `test_repo_extracts_validate_green`, `test_coverage_pending_stubs_not_found`,
and `test_pilot_extract_count_exact_and_merge_smoke`. The traceback in each
failure was inside YAML parsing/loading at the timeout boundary; the run showed
no extract-specific validation failure. Focused migration, fidelity and the
YAML flow-scalar guard were rerun after the review fixes.

## Expected store delta

If the lineage rule is resolved, the expected store delta is one admitted
numeric melt_activity observation with activity `1e-9`, method `emf_cell`,
composition and T, the Raoultian pure-solid Na2O reference, and the propagated
precision half-width. Pressure and fO2 remain unknown. No store regeneration
is committed while the hard issue remains.

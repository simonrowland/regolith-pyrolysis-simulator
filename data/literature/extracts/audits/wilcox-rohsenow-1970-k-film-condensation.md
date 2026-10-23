# Wilcox & Rohsenow (1970), Table 1 audit

Table 1 is printed on PDF page 6 (journal page 364) and contains 22 rows in
three series. The MinerU HTML table and Markdown provide a full 22-row table.
The pdftotext layout has several OCR substitutions; the page 6 render was used
for the five numeric disagreements below and to verify the table's row and
column assignments. The cells transcribed here are the four printed
temperatures, heat flux `q/A`, condensation coefficient `σ`, and both printed
pressures `P_v` and `P_s` (176 numeric cells total).

Cell codes: **a** = MinerU and pdftotext agree on the digits and the row/column
assignment was confirmed against the page render; **b** = the two machine reads
disagree and the page render confirms the transcription below. For each run,
the eight codes correspond in order to `T_v`, `T_s`, `T_w`, `T_cu-ni`, `q/A`,
`σ`, `P_v`, `P_s`.

| Series | Run | Cell codes |
|---:|---:|:---|
| 1 | 1 | b a a a a a a a |
| 1 | 2 | a a a a a a a a |
| 1 | 3 | a a a a a a a a |
| 1 | 4 | a a a a a a a a |
| 1 | 5 | a a a a a a a a |
| 1 | 6 | a a a a a a a a |
| 1 | 7 | a a a a a a a a |
| 1 | 8 | b a a a a a a a |
| 2 | 9 | a a a a a a a a |
| 2 | 10 | a a a a a a a a |
| 2 | 11 | a a a a a a a a |
| 2 | 12 | a a a a a a a a |
| 2 | 13 | a a a a a a a a |
| 2 | 14 | a a a a a b a a |
| 2 | 15 | a a a a a a a a |
| 2 | 16 | a a a a a a a a |
| 2 | 17 | a a a a a a a a |
| 3 | 18 | a a a a a a a a |
| 3 | 19 | a a a a a a a a |
| 3 | 20 | a a a a a a a a |
| 3 | 21 | b a a a a a a a |
| 3 | 22 | a a b a a a a a |

The rendered cells resolve the OCR discrepancies: run 1 `T_v = 853.76 °F`
(pdftotext has `853.7(5`); run 8 `T_v = 864.23 °F` (pdftotext has `S64.23`);
run 14 `σ = 0.975` (pdftotext has `0'.975`); run 21 `T_v = 989.38 °F`
(pdftotext has `9S9.38`); and run 22 `T_w = 1096.87 °F` (pdftotext has
`1006.87`). The render also resolves run-label OCR substitutions in pdftotext
(runs 6, 8, 11, and 18);
run labels are retained as strings and are not numeric measurement cells.

`mineru_table_draft.py compare` was run against the generated MinerU index; its
result was `matched=0 no-match=0 mismatch=0 presence-only=0 needs_mapping=0
total=0`. Its current numeric-point discovery does not inspect `values.alpha`
or these ancillary fields, so the output does not independently reconcile
this condensation-coefficient table. The full cell audit above is the
reconciliation record.

## Schema gap

The source prints temperatures in °F. The current condensation-coefficient
schema accepts `T_C`, `T_K`, or `T_range_K`, and the task rules prohibit
replacing source values with converted unit twins. All 22 rows therefore keep
their measured `σ` and printed temperatures, vapor pressures, and `q/A`, with
`status: typed_refusal` and a refusal reason. No Celsius/Kelvin conversion was
made. `P_v` and `P_s` are retained as printed pressure observations; neither is
asserted to be total system pressure. The identity's total pressure remains
unknown because the table does not print total pressure. Figure-read values
elsewhere in the paper are not included.

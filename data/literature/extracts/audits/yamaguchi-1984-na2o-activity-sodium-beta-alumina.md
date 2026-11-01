# Yamaguchi and Goto (1984) extraction audit

## Scope and source interpretation

The abstract reports one rounded activity point for the binary Na2O-P2O5 melt: about 10^-13 at XNa2O=0.5 and 1573 K. It explicitly defines the activity relative to pure liquid Na2O. The value is stored as the printed approximate activity, `1e-13`; it is not evaluated from a fit.

The authors determine activity from galvanic-cell EMF using beta-alumina as the solid electrolyte and a reference melt. The paper's Nernst relation is equation (1), printed page 44 (PDF page 2). The abstract value is a reduced result, but the EMF point inputs are only plotted, not tabulated. Under the evidence rule, this value is `quoted_attributed` because those inputs cannot be stored. The measured state is liquid melt (`l`). The binary complement is used only to bind the printed XNa2O=0.5 to a complete identity composition: Na2O=0.5, P2O5=0.5.

The abstract does not state a point-specific fO2 or total pressure; both remain unknown. The methods discuss different gas atmospheres for different experimental setups, so none is promoted to this abstract point.

## Full-cell audit

| Cell or series | Locator | Machine reads | Render / disposition |
|---|---|---|---|
| Abstract activity, `about 10^-13` | PDF p. 1 / printed p. 43 | pdftotext and MinerU agree on digits, sign, exponent, and approximation | Render confirms `10^-13`; stored as dimensionless activity `1e-13` |
| Abstract composition, `XNa2O=0.5` | PDF p. 1 / printed p. 43 | pdftotext and MinerU agree | Render confirms; binary complement retained for typed composition |
| Abstract temperature, `1573 K` | PDF p. 1 / printed p. 43 | pdftotext and MinerU agree | Render confirms |
| Equation (1), EMF/activity relation | PDF p. 2 / printed p. 44 | pdftotext and MinerU agree on the equation locator | Render confirms equation (1); retained as reduction provenance |
| Table 1, XNa2O=0.500 | PDF p. 3 / printed p. 45 | MinerU `0.500`; pdftotext table extraction mismatched | Render confirms `0.500`; typed-refused fit row |
| Table 1, 10^4 K A values, rows XNa2O=0.500 to 0.700 | PDF p. 3 / printed p. 45 | QA flagged all numeric cells as mismatches | Render confirms, in row order: 2.172, 2.047, 1.939, 1.958, 1.963, 1.683, 1.568; typed-refused fit rows |
| Table 1, 10^4 K delta_A values, rows XNa2O=0.500 to 0.700 | PDF p. 3 / printed p. 45 | QA flagged all numeric cells as mismatches | Render confirms, in row order: 0.027, 0.028, 0.043, 0.018, 0.040, 0.036, 0.129; typed-refused fit rows |
| Table 1, B values, rows XNa2O=0.500 to 0.700 | PDF p. 3 / printed p. 45 | QA flagged all numeric cells as mismatches | Render confirms, in row order: -0.733, -0.860, -0.834, -1.710, -2.848, -2.036, -2.517; typed-refused fit rows |
| Table 1, delta_B values, rows XNa2O=0.500 to 0.700 | PDF p. 3 / printed p. 45 | QA flagged all numeric cells as mismatches | Render confirms, in row order: 0.202, 0.205, 0.330, 0.140, 0.299, 0.277, 0.909; typed-refused fit rows |

Table 1 contains seven fitted coefficient rows for `-log a_Na2O=A/T+B`, not seven measured activity points. No closed schema token represents this fitted log-activity relation, so these rows are typed-refused and recorded in the mapping as a schema gap. The activity points shown in Figures 3-8 and the EMF points in Figure 2 are figure-read candidates and were not digitized. MinerU's chart blocks include invented plot-range tables; these were ignored.

The rendered Table 1 cells, in printed row and column order, are:

| XNa2O | 10^4 K A | 10^4 K delta_A | B | delta_B |
|---:|---:|---:|---:|---:|
| 0.500 | 2.172 | 0.027 | -0.733 | 0.202 |
| 0.533 | 2.047 | 0.028 | -0.860 | 0.205 |
| 0.567 | 1.939 | 0.043 | -0.834 | 0.330 |
| 0.600 | 1.958 | 0.018 | -1.710 | 0.140 |
| 0.633 | 1.963 | 0.040 | -2.848 | 0.299 |
| 0.667 | 1.683 | 0.036 | -2.036 | 0.277 |
| 0.700 | 1.568 | 0.129 | -2.517 | 0.909 |

Table row count reconciliation: the printed Table 1 has 7 body rows and 5 columns. It was checked against the PDF page 3 render; its rows are fit summaries, not standalone activity points. The extract contains 1 admitted activity observation, the abstract summary.

## Migration audit

The focused test migrates this extract alone, asserts one observation, and pins the abstract value, composition, temperature, pure-liquid standard state, the two source locators, and unknown fO2/total pressure. The test also rejects a one-decade mutation of the activity value. Isolated migration yielded 1 observation, 0 hard issues, 0 unavailable values, and no queued work; extract validation returned no errors. Identity axes are composition `{Na2O: 0.5, P2O5: 0.5}`, temperature 1573 K, unknown fO2, and unknown total pressure. The fO2 identity remains incomplete for scoring because the point does not print it; this is a typed validation issue, not a hard migration issue.

`tests/test_yaml_flow_scalar_commas.py -q -n 0` passed all 17 tests. `tests/test_literature_extracts.py -q -n 0` was started but interrupted under the 300-second command cap after 18 tests passed; the repo-wide suite did not finish in this seat.

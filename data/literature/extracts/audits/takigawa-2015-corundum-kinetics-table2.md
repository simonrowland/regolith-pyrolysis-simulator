# Takigawa et al. (2015), Table 2 extraction audit

Table 2 is on PDF page 10 (journal page 9). It prints six β rows and three αc columns (//c, //a, //m), for 18 coefficients. The printed Al pressures are the six location-specific columns u1–u3 and d1–d3, each headed `10^-9 atm`. Total pressure is not printed; none of the Al pressure fields is treated as total pressure. β and each pressure value are retained as printed on every corresponding coefficient observation.

## Cell audit

For every numeric cell below, MinerU's table HTML and the born-digital pdftotext layer agree on the digits, sign, exponent, and row/column assignment (method a). The one page render was used to confirm the table's row and column layout, including the β stub, six Al-pressure columns, and the //c, //a, //m αc columns. The PDF render corroborates the printed layout and the listed cells.

| PDF row | β | u1 | u2 | u3 | d1 | d2 | d3 | αc //c | αc //a | αc //m | Confirmation |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 3 | 0 | 3.32 | 1.80 | 1.13 | 2.99 | 1.47 | 0.80 | 0.032 | 0.046 | 0.099 | (a), render layout check |
| 4 | 0.1 | 3.11 | 1.58 | 0.92 | 2.77 | 1.25 | 0.58 | 0.036 | 0.053 | 0.11 | (a), render layout check |
| 5 | 0.3 | 2.85 | 1.33 | 0.66 | 2.51 | 0.99 | 0.33 | 0.043 | 0.063 | 0.13 | (a), render layout check |
| 6 | 0.5 | 2.71 | 1.19 | 0.52 | 2.37 | 0.85 | 0.18 | 0.048 | 0.070 | 0.15 | (a), render layout check |
| 7 | 0.7 | 2.61 | 1.09 | 0.43 | 2.28 | 0.76 | 0.09 | 0.052 | 0.076 | 0.16 | (a), render layout check |
| 8 | 1 | 2.52 | 1.00 | 0.34 | 2.19 | 0.67 | 0.00 | 0.057 | 0.083 | 0.18 | (a), render layout check |

Printed row count: 6 β rows; extracted coefficient count: 6 × 3 = 18. Table 1 evaporation fluxes were excluded. Figure 7 condensation-flux values are a figure-read candidate and were not extracted.

## Draft compare

`mineru_table_draft.py convert` produced the Table 2 draft at PDF page 10, with 6 body rows and 12 columns. The requested `compare` run reported `matched=0 no-match=0 mismatch=0 presence-only=0 needs_mapping=0 total=0`: this helper does not enumerate scalar `values.alpha` fields, so its zero candidates are not a claim that the extract has no numeric cells. The comparison result is committed alongside this audit. Numeric coverage is supplied by the row-level full-cell audit above and the focused fidelity test.

## Migration and conditions

The isolated real migration produced 18 point observations, 0 hard issues, and 0 unavailable values. All observations carry the printed 1575 °C through `T_C` and located `point_conditions`; the migrated temperature identity is 1848.15 K. The six location-specific Al pressures remain as printed values with their printed `10^-9 atm` header scale and u1–d3 labels. The total-pressure identity remains unknown because total pressure is not printed. The coefficient evidence class is `quoted_attributed`: the authors derive αc from experiment data, and the derivation inputs are not stored as this extraction excludes Figure/Table 1 measurements.

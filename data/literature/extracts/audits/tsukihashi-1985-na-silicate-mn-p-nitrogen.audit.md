# Tsukihashi et al. 1985 extraction audit

## Source and duplicates

The DOI, first author/year, title words, and source-id history were checked at
the seat base and refs including `review/main-car`. The title and DOI are not
already extracted under another id. `review/main-car` has a different
Tsukihashi 1985 paper (`ts1985`) and is not this source.

## Table transcription

Input order was MinerU table HTML/markdown, then `pdftotext-layout.txt`; PDF
renders settled the table discrepancies. Table 1 is the caption-numbered
table on PDF page 2 (printed page 824), with two printed rows. Its full
18 numeric cells and four dash cells were read against the render, including
each row and column assignment. The `Before equilibration` row is retained as
table context; the `After equilibration` P and Mn ratios are the two migrated
observations. The dash in the latter row's Na2CO3 column remains not printed.
The appendix interaction table is on PDF page 8 (printed page 830), has no
printed table number, and has three rows. All 21 numeric cells, footnote
references, signs, and exponents were checked against the render. Its Mn
activity coefficients concern carbon-saturated iron, not slag MnO activity.

The cell-level readings, render values, and decisions are in
`tsukihashi-1985-na-silicate-mn-p-nitrogen.audit.tsv`. Table QA reported
numeric agreement 0.000 on page 2 (four em-dash flags) and 0.605 on page 8
(13 LaTeX and 15 numeric-mismatch flags); these tables were therefore fully
render-checked. The table comparison tool reported zero candidates because
this extract stores scalar distribution ratios and table context, while that
tool only compares point-valued observation cells. The full printed grids
are independently pinned in the focused test.

## Other printed values and locators

- PDF page 2, Table 1 and Figure 1: the P result is the practical-slag point
  at 1200 °C. Its after-equilibration slag composition is bound per row.
- PDF page 4, section 3.1: the practical-slag oxygen partial pressure is
  estimated as 6.00e-18 atm at 1200 °C and 9.00e-17 atm at 1300 °C. The first
  value is attached to Table 1; the second is context only. This is the
  authors' estimated interface pO2, not total pressure.
- PDF page 4, section 3.1: the CO-at-1-atm graphite-crucible setup is printed
  in the practical-slag experiment discussion. 1 atm is converted to Pa by
  the migrator; no separate assumed pressure is supplied.
- PDF page 5, Figure 6: the Mn Table 1 point is at 1200 °C. This locator was
  checked against the render.
- PDF page 6, equations 20 and 23: the MnO activity-coefficient relation and
  Mn capacity definition are recorded as printed. No capacity quantity token
  exists, so capacity remains context with a schema-gap note. Figure 8's
  activity-coefficient values are deferred to calibrated digitisation.
- PDF page 4, equation 11: the P2O5 activity-coefficient relation and
  Raoultian reference state are retained. Figure 4 values are deferred.
- PDF page 7, section 3.3: the practical-slag nitrogen result is 2.1 wt% CN-
  at pN2=0.5 atm and 1200 °C. The passage gives no PCO for this assay and
  prints no complete slag composition, so it remains typed-refused context.

Figures 1–10 contain measured points not digitised in this extraction. Their
subjects and physical PDF pages are listed in the extract's figure-read
candidate context. No values from MinerU `<details>` blocks were used.

## Migration and checks

Isolated migration: 2 observations, 0 hard issues, 0 unavailable values,
0 queue entries, and 0 validation issues. For each observation, T=1473.15 K,
total pressure=101325 Pa, estimated pO2=6.0795e-13 Pa, the printed slag
composition, and the source locators reach the identity and point conditions.
The nitrogen result is not promoted to a row or assigned Table 1's composition.

The focused render/mutation and YAML flow-comma tests passed (19 passed). The
repo-wide extraction suite collected 641 tests; its run exceeded the 300 s
per-command limit before reporting a summary. Its focused Tsukihashi fidelity
sample and minimal-extract schema test passed (2 passed).

Expected store delta: +2 observations. No battery store was regenerated.

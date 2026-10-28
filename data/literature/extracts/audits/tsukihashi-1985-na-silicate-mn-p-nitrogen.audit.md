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
The text layer contains no Table 1 body cells; each Table 1 transcription is
therefore audited by render path (b), with MinerU retained as the independent
machine read.
The appendix interaction table is on PDF page 8 (printed page 830), has no
printed table number, and has three rows. All 21 numeric cells, footnote
references, signs, and exponents were checked against the render. Its Mn
activity coefficients concern carbon-saturated iron, not slag MnO activity.
For the Mn 1200 °C coefficient, the page render and MinerU table read 0.514;
the pdftotext prose reads 0.524. The render resolves that conflict in favor of
0.514. The other appendix cells have no table-cell text in the pdftotext
layout.

The cell-level readings, render values, and decisions are in
`tsukihashi-1985-na-silicate-mn-p-nitrogen.audit.tsv`. Table QA reported
numeric agreement 0.000 on page 2 (four em-dash flags) and 0.605 on page 8
(13 LaTeX and 15 numeric-mismatch flags); these tables were therefore fully
render-checked. The audit TSV records the raw MinerU cell, the pdftotext cell
value or its absence, the independently read render value, and the decision
for each of the 43 numeric/dash cells. The table comparison tool reported
zero candidates because
this extract stores scalar distribution ratios and table context, while that
tool only compares point-valued observation cells. The full printed grids
are independently pinned in the focused test.

## Other printed values and locators

- PDF page 2, Table 1 and Figure 1: the P result is the practical-slag point
  at 1200 °C. Its after-equilibration slag composition is bound per row.
- PDF page 4, section 3.1: the practical-slag oxygen partial pressure is
  estimated as 6.00e-18 atm at 1200 °C and 9.00e-17 atm at 1300 °C. The first
  value and the 1300 °C value remain labeled context in printed atm. The
  authors also print 8.42e-18 atm on Figures 1, 5, and 6 for the practical
  slag at 1200 °C. Since the prose estimate and figure annotation disagree,
  identity fO2 is left unknown; neither is promoted to a reconciled run
  condition.
- PDF page 3, section 3.1: the CO-at-1-atm graphite-crucible statement is
  printed here. The extract stores `total_pressure_atm: 1` and the migrator
  converts that source value to its Pa identity axis. It does not store a
  pre-converted 101325 Pa twin.
- PDF page 5, Figure 6: the Mn Table 1 point is at 1200 °C. This locator was
  checked against the render.
- PDF page 3, Figure 2: the phosphorus distribution fit is printed as
  `20180/T - 10.87`; equation 1 prints the rounded form `20200/T - 10.9`.
  The text also prints `f_P = 9.86` at 1200 °C, estimated with the stated
  zero-concentration approximation and regular-solution assumption.
- PDF page 5, equation 17: the manganese distribution fit is
  `6070/T - 2.74` over 1200–1350 °C.
- PDF page 4, after equation 15, the prose prints `a_Fe = 0.684` at 1200 °C.
- PDF page 5, Figure 5: practical-slag annotations print
  `gamma_FeO = 0.55` and `a_FeO = 0.00375`; the synopsis reports the FeO
  activity-coefficient range 0.2–1.0. The figure annotation has no stated
  reference state beside these numbers, so none is inferred.
- PDF page 1, synopsis: the phosphate capacity is reported from 10^26.7 to
  10^29 at 1200 °C. Its quantity remains context because the schema has no
  matching quantity token.
- PDF page 5, equation 20: the MnO activity-coefficient relation is recorded
  as printed. Figure 8's activity-coefficient values are deferred to
  calibrated digitisation.
- PDF page 6, equation 23: the Mn capacity definition is recorded as printed.
  No capacity quantity token exists, so capacity remains context with a
  schema-gap note.
- PDF page 4, equation 11: the P2O5 activity-coefficient relation and
  Raoultian reference state are retained. Figure 4 values are deferred.
- PDF page 7, section 3.3: the practical-slag nitrogen result is 2.1 wt% CN-
  at pN2=0.5 atm and 1200 °C. The passage gives no PCO for this assay and
  prints no complete slag composition, so it remains typed-refused context.

Figures 1–10 contain measured points not digitised in this extraction. Their
subjects and physical PDF pages are listed in the extract's figure-read
candidate context. No values from MinerU `<details>` blocks were used.

## Migration and checks

Isolated migration: 2 observations, 0 hard issues, 0 unavailable observable
values, 0 queue entries, and 2 explained incomplete-identity issues (one per
observation: required `fO2_Pa` is unknown). The stored experiment
conditions are printed `T_C: 1200` and `total_pressure_atm: 1`; the migrator
converts these to identity T=1473.15 K and total pressure=101325 Pa. The
printed slag composition reaches the identity and each point condition.
Identity fO2 is unknown because the figure and prose values conflict. The
nitrogen result remains refused context, with no Table 1 composition or CO
pressure assigned to it.

The focused render/mutation and YAML flow-comma tests passed (19 passed). The
repo-wide extraction suite collected 641 tests; its run exceeded the 300 s
per-command limit before reporting a summary. Its focused Tsukihashi fidelity
sample and minimal-extract schema test passed (2 passed).

Expected store delta: +2 measured_direct distribution-ratio observations.
No battery store was regenerated.

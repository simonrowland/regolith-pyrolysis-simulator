# Williams (1971) extraction audit

## Coverage and row reconciliation

The paper contains 174 printed data rows across Tables 1–11. The extraction
preserves all 82 experimental run rows from Table 1 (66) and Table 2 (16),
plus 92 rows from Tables 3–11. Table 1's printed subtable counts are A=8, B=13,
C=6, D=8, E=15, F=16. Table 2 has A=10 and B=6. The remaining printed counts
are: Table 3=6, Table 4=8, Table 5=10 plus 5 magnesium-free bounds, Table 6=8
plus 3 magnesium-free bounds, Table 7=10, Table 8=10, Table 9=22, Table 10=7,
and Table 11=3.

The 82 run rows preserve printed temperature, absolute log fO2 and its printed
uncertainty, pressure where the run-table subtable states P=1 atm, starting
material, product assemblage, and duration. The 109 printed O/P/W product
composition cells are retained as typed refusals because the current phase
composition schema has no solid-mineral role for olivine, orthopyroxene, or
magnesiowüstite. Values stay as printed mole percent with unit `%`; no division
by 100 was applied. Table 3's six cited activity-composition equations are
preserved as context. The 46 scalar log K points from Tables 4–8 migrate; the
inequality bounds, A/B/C fit tuples, literature comparisons, and paired log
fO2 entries remain typed context because those forms have no matching token.

## Conditions and evidence

The run tables print the temperatures and P=1 atm per subtable. Their log fO2
values are absolute relative to the pure-O2-at-1-atm reference stated in the
electrode method (PDF page 5, published page 338). The author reports direct
oxygen-electrode measurements and X-ray phase-composition measurements; the
composition values use cited X-ray calibration curves (PDF page 7, published
page 340). The reported fO2 widths are preserved as printed; their confidence
level is not stated.

The paper says the charges were rapidly withdrawn to quench, then examined
microscopically and crystalline phases measured by X-ray (PDF page 7,
published page 340). It also infers the synthesized pyroxenes are
orthopyroxenes (PDF page 8, published page 341). The extract therefore records
the measured products as crystalline solids and preserves the product
assemblage per run. Tables 4–8 supply temperature but do not state pressure or
fO2 in those rows; those conditions remain absent on the log K observations.

The log K values are the authors' calculated outputs from run measurements
and cited activity-composition relationships. Because the composition cells
cannot migrate under the current schema, the fitted/calculated points are
`quoted_attributed` under the extraction evidence rule; their attribution is
recorded in each observation. The paper does not supply a complete migratable
lineage for those calculations.

## Numeric cell audit

`williams-1971-fe-mgo-sio2-o2-reaction-constants.cell-audit.csv` lists 820
numeric tokens with physical page, printed table/subtable, source row and
column, raw source-cell text, and evidence. The two independent machine reads
agree on 785 tokens (audit class `(a)`). The remaining 35 tokens are class
`(b)` and were checked on their page renders. In particular, page 10 resolves
the Table 1(d)/(f) fO2/product/duration cells, page 15 resolves the Table 5
magnesium-free 5.850 bound, and page 21 resolves the comma-grouped and signed
Table 9 coefficients. Table row/column assignments were checked against
renders on PDF pages 9, 10, 11, 14, 15, 16, 21, and 22. Table 3's six rows
were manually assigned from its rendered equation table because MinerU labels
the equation rows as headers. The p009-t04 draft block spans printed Tables
1(c) and 1(d); the extract uses the printed caption and the physical page for
each run. The empty p010-t05 continuation block contributes no data.

The table QA pre-screen classified all 20 blocks as scanned input and skipped
its automatic numeric-agreement score. It flagged seven blocks for
letter-for-digit or LaTeX patterns; the flagged row labels and numeric cells
are represented in the cell audit and were checked against the renders. The
MinerU `details` chart blocks were excluded as invented figure data.

Rendered pins in the focused migration test are Table 4, row OPSI-3c,
`log K=-0.09` (PDF page 14); Table 5, row OWI-3c, `log K=5.443` (page 15);
and Table 7, row OPSI-3c, `log K=5.610` (page 16). The test also pins product
composition cells O(25), P(20), and W(12) to their printed rows and pages.

## Table-tool reconciliation

The committed mapping file describes the five scalar tables emitted by
`mineru_table_draft.py`; the other table forms are hand-assembled or retained
as context where the emitter cannot express their structure. The emitter
produced 46 observations with no skipped value cells.

The direct compare output has 46 `no-match` results because the compare command
interprets the extract's correct printed table numbers (4–8) as MinerU block
numbers (11, 12, 14, 16, 17). The companion `compare-draft-blocks.csv` uses a
temporary comparison copy with draft block, row, and column coordinates; it
matches all 46 values with no mismatch. No draft coordinates replace printed
table locators in the committed extract.

## Migration and scope gaps

An isolated migration produced 46 observations, 0 hard issues, and 0 queued
records. The focused test verifies that migrated log K identities receive the
printed temperature, while composition, pressure, and fO2 are not applicable
to this quantity profile and are absent from point conditions when the table
does not print them. Table 1 and Table 2 run contexts retain their printed
conditions separately.

Expected store delta without regeneration: 1 work, 83 experiments, and 46
observations.

The schema gaps are the solid-mineral component role; point-valued log K
inequalities; Table 9's A/B/C coefficient tuple and uncertainty form; Table
10's comparison coefficients and ranges; and Table 11's paired measured and
calculated log fO2 form. Figures 2–9 contain figure-read candidates only and
were not digitized. No schema, migrator, derived index, or battery store files
were changed.

# Asai and Yokokawa (1982) extraction audit

Status: complete. The extract contains all numerical rows from Tables 1 and 2.

## Duplicate check

No extract or matching history was found for DOI `10.2320/matertrans1960.23.571`, Asai and Yokokawa (1982), the title, or source id `asai-yokokawa-1982-nasoborosilicate-activity` in the seat or available refs. The distinct Itoh and Yokokawa (1984) NAS extract is not a duplicate.

## Machine reads and layout

The seven-page source has no numeric table rows in its pdftotext text layer. The original MinerU conversion contains Table 1 on PDF page 2 (`p002-t01`, duplicated as `p002-t05`); its continuation blocks are empty. Table 2 is present as `p004-t04` and `p004-t08`, with each numeric value and its ± width combined in one cell. The seat table converter was run over the MinerU content lists, and the table QA pre-screen was run against the corpus.

An OCR redo supplied `hybrid-high` and `pipeline-ocr` reads for PDF pages 3–4. Reading each of Table 1's four paired temperature/EMF bands vertically, and carrying each printed composition label down its own band, yields 374 pairs across 44 compositions: 185 pairs on PDF page 2, 164 on page 3, and 25 on page 4. The hybrid-high read contains all 189 page 3–4 pairs; pipeline-ocr yields eight, all agreeing with hybrid-high. Hybrid-high is the selected machine read for the remaining cells. All selected cells, signs, row/column assignments, and composition headings were checked against the page renders. Table 2 has 44 compositions and 132 values (three columns per composition); hybrid-high reads all 44 rows and pipeline-ocr reads none. All Table 2 value/uncertainty cells were checked against the PDF page 4 render.

The QA pre-screen reports numeric agreement 0.226 for the page-2 Table 1 blocks, 0.002 and 0.000 for the populated OCR-redo tables on page 3, and no numeric pdftotext table cells. These low rates reflect the absent text-layer table and the OCR disagreement; the full cell audit below resolves those cells against the renders. The page-2/3/4 renders were also used to verify every table's row and column layout. The OCR-redo table block indices are not printed table numbers; committed locators use printed Tables 1 and 2 and absolute PDF pages.

The table emitter produced the 374 Table 1 EMF rows. It cannot split Table 2's combined value±uncertainty cells, and MinerU marked that whole table as header rows, so Table 2 values and uncertainty widths were transcribed from its machine cells and confirmed against the render. The mapping and table-tool comparison are committed alongside the cell audit. The comparison matched 506 direct table values with zero mismatches: 374 EMF values and 132 Table 2 centers. The other 374 numeric observations are equation-derived activities, so have no direct table cell. For the comparison only, the scratch copy translated phase `l` to the comparator's accepted alias `liquid` and split Table 2 centers into its numeric comparison field; committed source values, locators, phases, and full ± cells were unchanged.

## Row and cell audit

| Printed table | Physical PDF pages | Printed row count | Numeric cells audited | Read selection and render check |
|---|---|---:|---:|---|
| 1 | 2–4 | 374 temperature/EMF pairs; 44 composition headings | 748 temperature and EMF cells | Page 2: MinerU `p002-t01` (duplicate `p002-t05`); pages 3–4: OCR redo `hybrid-high`. Pipeline-ocr agrees on eight page 3–4 pairs; the other 181 hybrid-high pairs were render-checked. All 374 row/column pairings and composition labels were checked on the page renders. |
| 2 | 4 | 44 compositions; 132 values in three numeric columns | 132 centers and 132 ± widths | MinerU `p004-t04` / duplicate `p004-t08` and OCR redo `hybrid-high` agree on complete cells; pipeline-ocr did not read the rows. All centers, signs, ± widths, composition assignments, and column assignments were checked on the page-4 render. |

The committed `*-cells.csv` has one entry per stored numeric source field: 748 Table 1 temperature/EMF fields and 264 Table 2 center/uncertainty fields. The audit distinguishes each physical PDF page, source table row and column, formula/composition, chosen machine read, and render check. Table 1 composition assignments follow each printed formula/m label, and all 44 Table 2 compositions match a Table 1 series. The four parallel bands are paired row-by-row; no value is assigned by flattening the printed layout.

The compare output records 506 `matched`, 374 `no-match`, and zero `mismatch` results. Its no-match rows are the 374 author-reduced activity values from Table 1 EMF using equation (3) on PDF page 1; their lineage points to the corresponding stored EMF row. The Table 2 values are author reductions from the full Table 1 temperature series for the same composition and are marked `measured_reduced`, with those stored EMF observations as parents.

## Conditions, reference state, and schema gaps

Every Table 1 and Table 2 observation carries its printed temperature and per-row melt composition as located conditions. Table 1's measured state is liquid. The reference state is the Na₂O·2B₂O₃ melt, and equation (3) is recorded with each reduced log activity and its EMF parent. No measurement-row total pressure or oxygen fugacity is printed, so neither is stored. The air/approximately 1300 K/>20 h statement on physical page 4 (printed p. 574) describes melt preparation only and is not promoted to an EMF condition.

The closed quantity schema has no measured concentration-cell EMF token; `EMF_CELL` is a method token, so the EMF rows preserve their printed mV values as typed refusals under the existing `activity_coefficient` observation family. The log-activity rows use that same family. Their printed log form is stored as-is and marked `measured_reduced`, but the mixture reference state has no closed `StandardState` token. Table 2 is represented with the existing `gibbs_table` family; its relative partial molar ΔG and ΔS forms lack exact quantity tokens, while ΔH has a quantity token whose mixture reference state is unsupported. Those rows preserve their centers, printed units, ± widths, `confidence level not stated`, and complete EMF lineage as typed refusals. No schema or migrator changes were made.

The concentration-cell notation, electrode reaction, and cell assembly description are on physical PDF page 1 (printed p. 571). The Pt wire and EMF stabilization details are on physical page 5 (printed p. 575). Melt preparation in air at around 1300 K for more than 20 h is on physical page 4 (printed p. 574), and is kept separate from measurement conditions. Figures 1 and 2 are on physical page 5 (printed p. 575); their values remain undigitized.

No battery store was regenerated. Isolated migration produced 880 observations, zero hard issues, zero registry issues, and 506 unavailable values. All 880 observations are rejected under the existing closed schema; 374 reduced activity observations still migrate as point values while retaining rejected admission status.

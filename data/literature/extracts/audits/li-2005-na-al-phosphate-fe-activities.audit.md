# Li et al. (2005), Tables 1–2 extraction audit

## Transcription and cell audit

The table tool was run on both OCR-redo variants, hybrid-high and pipeline-ocr; `pdftotext-layout.txt` remains the authoritative character layer but contains no Table 1 or 2 body cells. Every cell from both tool variants and the hand transcription was checked against the 200 dpi render of PDF page 3. `li-2005-na-al-phosphate-fe-activities.cells.tsv` lists all 16 printed columns for each row, including numeric and dash cells; every row is marked `render_checked_each_cell`. `li-2005-na-al-phosphate-fe-activities.mineru-cell-comparison.tsv` records all 400 table cells, the two variant readings, and their render resolution. Hybrid-high agrees with all 400 render readings; pipeline-ocr agrees on 148/400 and its remaining 252 cells are resolved to the render. The column layout and row/column assignments were checked against the render.

Table 1 has 11 rows; Table 2 has 14 rows; 25 rows total. Values remain in the printed form and units. Table 2's Al2O3 dashes are retained as dashes, not zeros. The focused migration test pins values from both tables and both ends of the tables to PDF page 3.

## Conditions and measured state

PDF page 2 (printed page 13) states equilibration at 1823 K and 1873 K. It prints CO2/CO ratios of 1/10 and 1/18, gas flow rates of 240 and 270 cm3/min, and oxygen partial pressures calculated by Eq. (1) of 6.40×10−5 Pa and 5.32×10−5 Pa. The rows carry the printed temperature, pO2, gas ratio, and slag composition. Gas flow is not a pressure; the experiment's total pressure is not printed and remains unknown. No ambient or room-temperature condition is inferred.

The same page states that samples were removed from the furnace and quenched with an argon stream. It does not state the post-quench phase, whether the quenched slag formed glass, or whether the measured chemistry was preserved by quenching. Composition and Fe valence records keep phase unknown and cite the quench statement at PDF page 2. Table values remain tied to their 1823/1873 K equilibration conditions.

The table gives Fe3+/Fe2+ values. The methods text says Fe3+ content was obtained from total Fe and Fe2+ contents; numerical total-Fe assay values are not tabulated, so Fe2O3 content rows retain that attribution and the Fe3+/Fe2+ rows are measured_reduced from the printed FeO and Fe2O3 values. Distribution ratios are measured_reduced from the printed slag P and equilibrium metal [P] values. The authors' gamma FeO and gamma FeO1.5 values are measured_reduced using Eqs. (21) and (22); their respective pure-liquid FeO and solid FeO1.5 reference states are specified in the text on PDF page 6 (printed page 17). The source does not name a solid FeO1.5 polymorph; the typed state records crystalline phase with unknown polymorph. Input composition rows are retained as derivation parents. The 25 phosphate-capacity rows preserve their printed Eq. (5) definition and row-specific P2O5/metal P lineage, but remain typed-refused because no capacity token exists.

## Scope limits and schema gap

There is no phosphate-capacity quantity token in this schema version. All 25 printed log10 capacity values and the printed definition are preserved as typed-refused rows; they do not create typed observations. The isolated migration emits 370 observations: 345 typed points (245 composition values, 50 activity coefficients, 25 distribution ratios, 25 valence ratios) and 25 unavailable capacity rows, with zero hard issues. Composition rows cover all numeric values in the eight slag-oxide columns and both metal [P] and [O] columns. Five printed Al2O3 dashes are preserved in the cell audit and do not become numeric observations. The focused test confirms every typed observation receives temperature, full composition, and absolute pO2 axes while total pressure remains unknown; three representative migrated identities were printed during isolated migration. No Na2O, MnO, or P2O5 activity coefficient was tabulated; no figure values were digitized.

Figures 4–8 and 10–11 are useful figure-read candidates for partition ratios, phosphate capacities, P2O5 activity, and iron oxide activity coefficients. They are listed only as candidates and were not digitized.

## Table draft comparison

`tools/mineru_table_draft.py compare` was run using a one-value-per-cell comparison candidate assembled from the 370 numeric printed cells. Hybrid-high reports `matched=370 no-match=0 mismatch=0 total=370`; pipeline-ocr reports `matched=123 no-match=0 mismatch=247 total=370`. The committed comparison outputs preserve those cell-level results. The per-cell reconciliation TSV also includes row labels and dash cells (400 cells total); hybrid-high agrees with the render throughout, and each pipeline-ocr disagreement was resolved against the render. The final typed extract preserves each of those checked numeric values in its mapped quantity rows.

# Brewer & Margrave (1952), Table 3 audit

- Printed table: Table 3, “Vaporization Data on Na2O”; printed page 14, absolute PDF page 17.
- Printed body row count: 7. All seven are Na2O runs (run numbers 2, 5, 7, 6, 4, 3, 1); the table heading identifies no other species.
- Row and column layout: checked against the page render because both redo variants have OCR layout errors. Hybrid-high transcribes the seven rows as recorded in the mapping. Pipeline-ocr shifts and merges cells in multiple rows. The render confirms the row ordering and every listed cell. The original pdftotext-layout output only partially read Table 3 and was not used as a second confirmation.

| Run | T (K) | Total loss (mg) | Time (s) | Hole area (cm²) | Cell audit |
|---:|---:|---:|---:|---:|---|
| 2 | 918 | 6.2 | 11,640 | 0.1156 | hybrid-high agrees; page render confirms all five cells and row placement |
| 5 | 943 | 7.8 | 64,980 | 0.0314 | hybrid-high agrees; page render confirms all five cells and row placement |
| 7 | 998 | 22.7 | 59,820 | 0.0314 | hybrid-high agrees; page render confirms all five cells and row placement |
| 6 | 1006 | 21.3 | 73,260 | 0.0314 | hybrid-high agrees; page render confirms all five cells and row placement |
| 4 | 1241 | 21.7 | 1,500 | 0.0855 | hybrid-high agrees; page render confirms all five cells and row placement |
| 3 | 1260 | 134.4 | 1,320 | 0.0855 | hybrid-high agrees; page render confirms all five cells and row placement |
| 1 | 1467 | 641.1 | 600 | 0.0804 | hybrid-high agrees; page render confirms all five cells and row placement |

The alternate pipeline-ocr variant is independently compared but is not treated as clean corroboration: it merges cells and misreads 134.4 as `13404`; the page render resolves these. No decimal, sign, exponent, or row/column uncertainty remains in the listed Table 3 cells.

## Schema / interpretation question

Table 3 directly reports total sample weight loss, duration, and effusion-hole area. The text says the Na/O ratio was not measured during Na2O vaporization and was assumed to be 2:1 in the authors’ calculations. It reports only an approximate high-temperature Na partial-pressure range (10⁻³–10⁻⁴ atm), not seven row-specific pressures. Existing `mass_loss_rate` is a supported quantity but does not distinguish Na from O2; obtaining a Na-specific pressure or flux from these rows requires adopting the stated but unchecked gas composition assumption and a Knudsen reduction. No row-level total pressure or quantitative starting composition is printed in Table 3. They must remain unknown unless another printed statement supplies them.

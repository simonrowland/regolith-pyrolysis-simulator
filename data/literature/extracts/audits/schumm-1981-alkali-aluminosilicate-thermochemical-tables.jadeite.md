# Schumm (1981), jadeite table audit

## jadeite (NaAlSi2O6), PDF p. 21 / printed p. 15

Printed temperature-row count: 11. Machine-read numeric agreement was poor in the whole-report QA pre-screen, so the rendered page was checked across all rows and four stored numeric columns, including signs and row/column assignments. MinerU table p021-t08 supplied the transcription; pdftotext layout was checked against the render. No unresolved differences.

Printed source annotations: S298: 53KEL/TOD, 65FUR/REI. H−H298 and S−S298: 65FUR/REI. ΔfH298: 53KRA/NEU, 51KRA/NEU. No separate Cp/ΔfG source annotation. S298=31.95 ± 0.30 cal/deg,mol and ΔfH298 uncertainty ±0.90 kcal/mol; confidence levels not stated. The S−S298 entropy increment has no exact token: `Quantity` in `simulator/battery/enums.py::Quantity` contains `S` but no increment token; `TYPE_QUANTITY` (`simulator/battery/migrate.py::TYPE_QUANTITY`) and `QUANTITY_ALIASES` (`simulator/battery/migrate.py::QUANTITY_ALIASES`) have no exact mapping. The column is not mislabeled as absolute S.

Jadeite remains without a polymorph token: `Polymorph` in `simulator/battery/enums.py::Polymorph` has no jadeite member, and `_EXTRACT_POLYMORPH_ALIASES` in `simulator/battery/migrate.py::_EXTRACT_POLYMORPH_ALIASES` has no jadeite extract alias; `polymorph_from_extract` reads the observation-level field at `simulator/battery/migrate.py::polymorph_from_extract`.

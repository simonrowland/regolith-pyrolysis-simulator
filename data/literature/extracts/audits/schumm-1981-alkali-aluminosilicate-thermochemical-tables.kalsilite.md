# Schumm (1981), kaliophilite table audit

## kaliophilite (KAlSiO4), PDF p. 28 / printed p. 22

Printed temperature-row count: 19. Machine-read numeric agreement was poor in the whole-report QA pre-screen, so the rendered page was checked across all rows and four stored numeric columns, including signs and row/column assignments. MinerU table p028-t15 supplied the transcription; pdftotext layout was checked against the render. No unresolved differences.

Printed source annotations: S298: 53KEL/TOD. H−H298 and S−S298: 68PAN. ΔfH298: 66BAR/ADA. No separate Cp/ΔfG source annotation. S298=31.85 ± 0.30 cal/deg,mol and ΔfH298 uncertainty ±0.50 kcal/mol; confidence levels not stated. The S−S298 entropy increment has no exact token: `Quantity` in `simulator/battery/enums.py:62-111` contains `S` but no increment token; `TYPE_QUANTITY` (`simulator/battery/migrate.py:435-444`) and `QUANTITY_ALIASES` (`migrate.py:453-547`) have no exact mapping. The column is not mislabeled as absolute S.

Polymorph follows the printed transition: α through the first 810 K row, then β from the second 810 K row through 1800 K. The printed Cp cells at 810 K are 42.68 and 42.50 cal/deg,mol (α and β, respectively); the migrated Cp values are 178.57312 J (α) and 177.8200 J (β). These use the existing `Polymorph.ALPHA` and `.BETA` members in `simulator/battery/enums.py:315-320` and the observation-level `condensed_form.polymorph` reader in `simulator/battery/migrate.py:8668-8700`.

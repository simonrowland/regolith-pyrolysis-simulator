# Shtenberg et al. (2020) extraction audit

## Table coverage

| Printed table | PDF page | Printed rows | Numeric cells | Stored observations | Context rows |
|---|---:|---:|---:|---:|---:|
| 1, reference heat capacities and comparisons | 3 | 24 | 120 | 24 | 24 |
| 2, reference formation enthalpies and comparisons | 4 | 24 | 111 | 21 | 24 |
| 3, calculated ternary thermodynamics | 6 | 16 | 64 | 0 | 16 |
| 4, potassium borogermanate comparison | 6 | 2 | 6 | 2 | 2 |
| **Total** | | **66** | **301** | **47** | **66** |

Table 2 has three em-dash reference cells and their three corresponding deviation cells; these are absent values, not numeric cells. Tables 1 and 2 each have 24 printed compound rows. The 47 observations are the 24 quoted heat capacities, 21 quoted formation enthalpies, and 2 Table 4 attributed reference values. Calculated, fitted, NKR/Zhuang-estimated values, and deviations remain in per-species context. The invented MinerU `<details>` block under Figure 3 was ignored.

## Cell audit

[`shtenberg-2020-alkaline-oxide-thermodynamic-regression-cells.csv`](shtenberg-2020-alkaline-oxide-thermodynamic-regression-cells.csv) records each numeric table cell by absolute PDF page, printed table, row, column, printed text, and verification route. Pages 3, 4, and 6 were rendered. The table row and column assignments on each page were checked against the render. Tables 1 and 2 had QA numeric mismatches because their reference cells combine a value and `±` uncertainty; every such cell was read from the render. The remaining numeric cells agree between MinerU and the born-digital pdftotext layer, with layout checked against the render. `mineru-compare.csv` retains the table tool's point-level results; its scalar comparison cannot match cells containing a combined value and uncertainty, which were render-verified.

The reported numeric agreements were Table 1: 0.940, Table 2: 0.882, Table 3: 1.000, Table 4: 1.000. The full-table render checks resolve the Table 1–2 mismatches, including all signs and uncertainty digits.

## Evidence and schema audit

- Table 1 reference heat capacities and Table 2 reference formation enthalpies are `quoted_attributed`; their columns cite `[28–30]` in the paper. Table 4's two reference cells retain `[16]` and `[17]` as cited.
- The printed units and identities are supported by the existing enums: `Quantity.CP = "cp"` and `Quantity.DELTA_FH = "delta_fH"` in `simulator/battery/enums.py:84-93`. The extract uses those exact tokens. The isolated migration produced no quantity-unknown observations.
- Observation types are from the closed `OBSERVATION_TYPES` set in `tools/validate_literature_extracts.py:79-98`: `heat_capacity_and_heat_content_series` for Cp and `gibbs_table` for formation enthalpy. The existing thermodynamic extracts use the same forms (e.g. `data/literature/extracts/beyer-1980-potassium-metasilicate-disilicate-ri8410.yaml:164-170` and `data/literature/extracts/pedley-marshall-1983-gaseous-monoxides.yaml:920-929`). Thus no observation-type schema gap is claimed.
- `Phase.CR = "cr"` is the closed crystalline phase token in `simulator/battery/enums.py:282-283`; the values are thermodynamic properties of the named crystalline compounds (Table 3 explicitly says “in the crystalline state”). The isolated migration produced no phase-unknown observations.
- No pressure, fO2, or bulk composition condition is printed for these standard thermodynamic reference rows. No such condition was supplied. The focused test pins pressure and fO2 as absent after migration.

## Isolated migration

The real `Migrator._migrate_extract` path produced 47 observations and 66 context rows, with 0 hard issues, 0 unavailable observations, 0 quantity-unknown observations, and 0 phase-unknown observations. Three pinned identities show composition, total pressure, and fO2 as not applicable to these quantity profiles; temperature is 298.15 K. All observations migrate as point values in crystalline phase. Printed uncertainty widths are retained verbatim, with confidence level `not_stated`. No store regeneration was run; expected observation-store delta is +47 observations and +66 source-context rows.

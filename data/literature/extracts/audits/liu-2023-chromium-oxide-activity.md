# Liu, Li & Kobayashi (2023): extraction audit

Source: DOI 10.2355/isijinternational.ISIJINT-2022-368. Primary machine reads were the MinerU HTML table blocks / markdown and the born-digital `pdftotext-layout.txt`. Per controller steer, the exact pdftotext characters are trusted; the audit risk was column and row assignment. Each table was checked once against its full page render: PDF p. 2 (published p. 314), p. 3 (published p. 315), and p. 5 (published p. 317). Both machine reads agree on all numeric cells; the renders confirm the table boundaries, columns, row order, and species assignment. No OCR redo was needed. The MinerU `<details>` chart blocks were excluded.

## Census and cell audit

The printed tables contain 4 initial-composition rows (Table 1), 12 post-equilibration rows (Table 2), and 12 activity rows (Table 3). The row counts match. Method (a) below means MinerU and pdftotext independently contain the same digits, sign, exponent, and decimal places for every listed numeric cell in that physical row. The render checked the row/column layout for the whole table on that page.

| PDF page / table / physical row | Printed `No.` | Table 2 crucible | Numeric cells covered | Proof |
| --- | ---: | --- | --- | --- |
| p. 2 / 1 / 1 | 1 | — | `No., CaO, CaF2, MgO, Al2O3, SiO2, MnO, Cr2O3, Total` | (a) |
| p. 2 / 1 / 2 | 2 | — | all Table 1 numeric columns above | (a) |
| p. 2 / 1 / 3 | 3 | — | all Table 1 numeric columns above | (a) |
| p. 2 / 1 / 4 | 4 | — | all Table 1 numeric columns above | (a) |
| p. 3 / 2 / 1 | 1 | Mo | `No., CO/CO2, PO2/atm, CaO, CaF2, MgO, Al2O3, SiO2, MnO, CrO1.5, CrO, MoO2, Total` | (a) |
| p. 3 / 2 / 2 | 1 | Mo | all Table 2 numeric columns above | (a) |
| p. 3 / 2 / 3 | 1 | Mo | all Table 2 numeric columns above | (a) |
| p. 3 / 2 / 4 | 2 | Mo | all Table 2 numeric columns above | (a) |
| p. 3 / 2 / 5 | 2 | Mo | all Table 2 numeric columns above | (a) |
| p. 3 / 2 / 6 | 2 | Mo | all Table 2 numeric columns above | (a) |
| p. 3 / 2 / 7 | 2 | Mo | all Table 2 numeric columns above | (a) |
| p. 3 / 2 / 8 | 3 | Mo | all Table 2 numeric columns above | (a) |
| p. 3 / 2 / 9 | 3 | Mo | all Table 2 numeric columns above | (a) |
| p. 3 / 2 / 10 | 3 | Mo | all Table 2 numeric columns above | (a) |
| p. 3 / 2 / 11 | 4 | MgO | all Table 2 numeric columns above | (a) |
| p. 3 / 2 / 12 | 4 | MgO | all Table 2 numeric columns above | (a) |
| p. 5 / 3 / 1 | 1 | Mo | `No., CO/CO2, PO2/atm, aCr, aCrO1.5, aCrO, aCrOx` | (a) |
| p. 5 / 3 / 2 | 1 | Mo | all Table 3 numeric columns above | (a) |
| p. 5 / 3 / 3 | 1 | Mo | all Table 3 numeric columns above | (a) |
| p. 5 / 3 / 4 | 2 | Mo | all Table 3 numeric columns above | (a) |
| p. 5 / 3 / 5 | 2 | Mo | all Table 3 numeric columns above | (a) |
| p. 5 / 3 / 6 | 2 | Mo | all Table 3 numeric columns above | (a) |
| p. 5 / 3 / 7 | 2 | Mo | all Table 3 numeric columns above | (a) |
| p. 5 / 3 / 8 | 3 | Mo | all Table 3 numeric columns above | (a) |
| p. 5 / 3 / 9 | 3 | Mo | all Table 3 numeric columns above | (a) |
| p. 5 / 3 / 10 | 3 | Mo | all Table 3 numeric columns above | (a) |
| p. 5 / 3 / 11 | 4 | MgO | all Table 3 numeric columns above | (a) |
| p. 5 / 3 / 12 | 4 | MgO | all Table 3 numeric columns above | (a) |

This accounts for 276 numeric table cells (36 + 156 + 84). Table 2's `CrO1.5` and `CrO` columns are separate printed mass percentages, not a measured valence ratio. They remain separate in the extract, with no ratio derived. The first column's repeated slag number is preserved as `slag_number_as_published`; `row` is the physical table-row locator.

## Compare output

Ran:

```sh
python tools/mineru_table_draft.py compare data/literature/extracts/liu-2023-chromium-oxide-activity.yaml <scratch>/tables/tables/mineru/index.yaml
```

Result: 72 matched values, 0 no-match, 0 mismatch. The committed CSV sidecar `liu-2023-chromium-oxide-activity-mineru-compare.csv` retains every matched row, page, table cell, path, and status. It omits the comparator's repeated full-page candidate list, which is identical across rows and not needed to verify the matched cell. The full raw stdout remains in the dispatch scratch table output. Table 1 composition cells are included in the cell audit above; the comparator only matches observation points.

## Science, conditions, and schema notes

- All experiments use 1823 K (1550 °C), not 1873 K. Table 3 oxygen partial pressures are stored in atm as printed alongside the CO/CO2 ratio. The existing migrator converts each printed oxygen partial pressure to the `fO2_Pa` identity axis; total pressure remains unknown because the paper does not report it.
- Table 2 crucibles are preserved per row: Mo on rows 1–10 and MgO on rows 11–12. MoO2 reaches 32.39 mass% in row 1. CaF2, MnO, and all printed composition values remain in the per-row maps.
- The source states that samples were removed quickly after 24 h and water-quenched immediately (published p. 314; PDF p. 2), but does not state quench preservation. Table 2 is bulk quenched-slag chemistry, not an asserted glass-only measurement. Table 3 activities describe the high-temperature liquid state (`l`). The structured pure-endmember reference states follow the explicitly printed thermodynamic reactions: Cr(s), CrO1.5(s), and CrO(l); solid polymorphs remain unknown.
- Table 3 aCr is `quoted_attributed` to the Kobayashi et al. (1995) Cr-in-copper activity coefficient. The row-matched aCrO1.5 and aCrO values are `model_derived` with Eq. (13)/(14), the matching stored aCr row, printed P_O2, and T. The printed 1.5519 and 3.2610 are unchanged.
- Table 3 aCrOx is typed-refused because variable-stoichiometry CrOx has no canonical species token. Table 2 CrO1.5 and CrO mass-percent rows are typed-refused because the schema has no quantity token for those separately printed oxide mass percentages as redox observations. The raw numeric cells and conditions remain present.
- Full slag maps include CaF2, CrO1.5, CrO, and MoO2, which the existing closed composition map cannot convert to the identity composition. No partial composition is promoted. The migration reports that explicit composition schema gap for the oxide-activity identities. The per-run Cu-Cr alloy composition used for aCr is not tabulated, so its identity composition is correctly unknown.
- The Cr2+/total-Cr mass ratio is present only in Fig. 3 and is listed as a figure-read candidate; it is not digitized. No invented MinerU chart-block values were used.

## Isolated migration

The real migrator emitted 72 observations: 36 numeric Table 3 activity values and 36 unavailable typed-refusal values (24 Table 2 oxide percentages plus 12 Table 3 CrOx values). There were 0 hard issues, 0 registry issues, and 39 queued soft issues: 36 unavailable-value notices, one unsupported quantity, one unrecognized bulk phase, and one unavailable CrOx reference state. All 72 observations carry located point conditions, including temperature.

Identity axes from the real migration for representative Table 3 rows:

| Species / row | Composition | Total pressure (Pa) | fO2 (Pa) | Temperature (K) |
| --- | --- | --- | --- | --- |
| Cr / row 1 | unknown: Cu-Cr alloy concentration not tabulated | unknown: total pressure not reported | 0.0064645350 | 1823 |
| CrO1.5 / row 1 | unknown: printed full slag map has unsupported components | unknown: total pressure not reported | 0.0064645350 | 1823 |
| CrO / row 1 | unknown: printed full slag map has unsupported components | unknown: total pressure not reported | 0.0064645350 | 1823 |

The unknown composition states preserve the source facts and the specific closed-map limitation; the full printed maps remain attached to their rows. No battery-store files were regenerated.

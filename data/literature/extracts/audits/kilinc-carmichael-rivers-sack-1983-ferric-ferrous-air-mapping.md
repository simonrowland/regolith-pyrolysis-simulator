# Kilinc et al. (1983) primary assay mapping audit

Primary artifact: `raw/kilinc-carmichael-rivers-sack-1983-ferric-ferrous-air/kilinc-carmichael-rivers-sack-1983-ferric-ferrous-air.pdf`, SHA-256 `c4d4cd7b87e5401c09a422e361ea4f06b326b46f58f86e0f46c6c40a12809a74`. The five-page PDF was rendered at 180 dpi; Tables 1–3 were inspected in rendered page crops. `pdftotext -layout` was used only as a transcription aid.

## Table coverage and mapping

| Printed table | Physical PDF pages (zero-based) | Published page | Extracted rows | Mapping |
|---|---:|---:|---:|---|
| Table 1, glass assays | 1–2 | 137–138 | 36 (sample numbers 1–36) | Identifier, all twelve printed oxide wt% columns, T, and duration. Each point row has a printed sample-number locator. |
| Table 2, duplicate assays | 3 | 139 | 10 | Same oxide columns, T and duration. The third sample number is printed `49`; it is retained as `49`, not silently corrected to `39`. |
| Table 3, equilibrium-time assays | 3 | 139 | 5 timed rows | All eleven oxide columns printed by Table 3, FeO, Fe2O3, total, T and duration. Cr2O3 is not printed and is absent in these rows. The `Initial` column has no temperature and is not migrated as a point; its composition is Table 3 context only: SiO2 58.60, TiO2 0.89, Al2O3 15.38, Fe2O3 2.22, FeO 6.71, MnO 0.18, MgO 3.22, CaO 7.02, Na2O 3.84, K2O 1.46, P2O5 0.25 wt%. |

All 51 temperature-bearing assay rows are reported as experiments in air. The paper does not print a numerical per-run log fO2; the extract records the printed atmosphere as air and leaves numerical log fO2 unknown. Table-cell values use string scalars. Temperatures are Celsius; `T_C` retains numeric digits for the reader and `T_C_as_printed` retains the printed thousands separator. Each row's locator gives the physical page, printed page, table, and record; `cell_locators` gives printed column names for fields whose extract key differs from the column label. For oxide compositions, the oxide key itself is the printed column name, paired with that row locator.

The authors state that FeO was measured by a volumetric/colorimetric procedure and Fe2O3 was calculated from total iron expressed as FeO (FeOt) and colorimetric FeO using `wt.% Fe2O3 = 1.1113 (FeOt - FeO)`. The extract retains the printed FeO and Fe2O3 results and method attribution; it does not calculate Fe3+/Fe2+, Fe2O3, or any oxide composition. Per-run uncertainty is not printed. The general analytical precision statement and BCR-1 check are not represented as per-row uncertainties.

The Borisov (2018) compilation contains recalculated Kilinc values expressed as mole fractions. This extract is a separate source record for Kilinc's primary printed oxide assays, composition, T, and air atmosphere. Cross-source duplicate handling is left to the migration owner.

## Render crop verification

These cells were checked directly against the rendered page crops `crop-table1a.png`, `crop-table1b.png`, and `crop-table23.png` made from physical PDF pages 1, 2, and 3 respectively. Values below are the printed table cells (sample number / field / value):

- Table 1, p. 137, PDF index 1: sample 1 identifier `CAM-49`; SiO2 `72.21`; Fe2O3 `1.13`; FeO `0.64`; MgO `0.27`; CaO `1.27`; P2O5 `0.04`; T `1,342` °C; Time `23` h.
- Table 1, p. 137, PDF index 1: sample 7 TiO2 `5.64`; Al2O3 `9.89`; Fe2O3 `11.66`; Na2O `2.27`; K2O `4.23`.
- Table 1, p. 138, PDF index 2: sample 9 Fe2O3 `7.02`; FeO `1.41`; sample 22 MgO `1.04`; sample 27 TiO2 `3.31`; sample 31 FeO `2.90`; sample 36 identifier `780-K-15`.
- Table 2, p. 139, PDF index 3: sample `49` identifier `94-8`; Fe2O3 `7.49`; FeO `1.37`; P2O5 `0.03`; T `1,344` °C; duration `24` h.
- Table 3, p. 139, PDF index 3: 5 h FeO `2.23`, Fe2O3 `7.04`, T `1,349` °C; 70 h FeO `2.58`, Fe2O3 `6.80`, T `1,359` °C; Initial T is an em dash.

## Static migration summary

In-memory `Migrator._migrate_extract` plus `finalize` produced 51 observations: 51 `fe3_fe2_ratio`, zero hard issues, and zero queue entries. Every migrated row has its T, printed composition, and air atmosphere; the numerical per-run `fO2_log` remains explicitly unknown. Migration derives the ratio from the printed Fe2O3/FeO pair; no such ratio is written into this extract.

All three samples map to the REDOX rail. The Fe-redox prediction path admits `internal-analytical`, `openimcc`, and the MELTS backends (including AlphaMELTS and ThermoEngine). No prediction engine was executed under the owner hold, so this lists the statically eligible path rather than an engine result. The typed gap for each is the absent numeric per-run log fO2; the source does state air.

- Table 1, sample 1 (`CAM-49`, 1342 °C): `fe3_fe2_ratio` → REDOX; typed gap: numerical fO2 unknown, atmosphere `air`.
- Table 2, sample 37 (`IC-2`, 1344 °C): `fe3_fe2_ratio` → REDOX; typed gap: numerical fO2 unknown, atmosphere `air`.
- Table 3, time 5 h (1349 °C): `fe3_fe2_ratio` → REDOX; typed gap: numerical fO2 unknown, atmosphere `air`.

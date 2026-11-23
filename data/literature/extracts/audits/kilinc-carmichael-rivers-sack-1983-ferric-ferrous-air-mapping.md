# Kilinc et al. (1983) primary assay mapping audit

Primary artifact: `raw/kilinc-carmichael-rivers-sack-1983-ferric-ferrous-air/kilinc-carmichael-rivers-sack-1983-ferric-ferrous-air.pdf`, SHA-256 `c4d4cd7b87e5401c09a422e361ea4f06b326b46f58f86e0f46c6c40a12809a74`. The five-page PDF was rendered at 180 dpi; Tables 1–3 were inspected in rendered page crops. `pdftotext -layout` was used only as a transcription aid.

## Table coverage and mapping

| Printed table | Physical PDF pages (zero-based) | Published page | Extracted rows | Mapping |
|---|---:|---:|---:|---|
| Table 1, glass assays | 1–2 | 137–138 | 36 (sample numbers 1–36) | Identifier, all twelve printed oxide wt% rows, T, and duration. Each assay occupies the column identified by its sample number. |
| Table 2, duplicate assays | 3 | 139 | 10 (37, 38, 49, 40–46) | Same oxide rows, T, and duration. The third column is identified by the printed sample number `49`. |
| Table 3, equilibrium-time assays | 3 | 139 | 5 timed runs | All eleven printed oxide rows, including FeO and Fe2O3, plus total, T, and duration. The `Initial` column has no temperature. Its oxide composition is context for Table 3 (wt%) — SiO2 58.60, TiO2 0.89, Al2O3 15.38, Fe2O3 2.22, FeO 6.71, MnO 0.18, MgO 3.22, CaO 7.02, Na2O 3.84, K2O 1.46, P2O5 0.25. |

The Abstract on p. 136 (PDF index 0, left column) identifies the 46 Table 1 and 2 experiments as conducted in air. Results and Discussion on p. 137 (index 1, right column) identifies the five timed Table 3 experiments and their air atmosphere. Per-run numerical log fO2 and total furnace pressure are unknown.

Each record locator gives the published page, zero-based PDF index, table, logical record ID, and physical column identified by the printed sample number or time. Each `cell_locators` entry gives the physical row label and column for its field. For oxide compositions, the oxide key is the physical row label and the record locator supplies the column. Table-cell values are strings. `T_C` uses numeric digits and `T_C_as_printed` preserves the printed thousands separator from the same cell.

Experimental Procedures continues on p. 137 (index 1, left column). It describes the Wilson (1960) volumetric and colorimetric FeO method and the authors' Fe2O3 calculation from total iron and colorimetric FeO. The five timed runs cite that method statement. The Table 3 note on wet chemical analysis applies only to `Initial`. The extract retains the printed oxide results and method attribution. The analytical precision statement and BCR-1 check describe general assay performance.

The Borisov (2018) compilation contains recalculated Kilinc values expressed as mole fractions. This extract is a separate source record for Kilinc's primary printed oxide assays, composition, T, and air atmosphere. Cross-source duplicate handling is left to the migration owner.

## Render crop verification

The rendered crops on pp. 137–139 (PDF indices 1–3) show the following cells, listed by physical column, physical row, and value.

- Table 1, p. 137, PDF index 1: sample 1 identifier `CAM-49`; SiO2 `72.21`; Fe2O3 `1.13`; FeO `0.64`; MgO `0.27`; CaO `1.27`; P2O5 `0.04`; T `1,342` °C; Time `23` h.
- Table 1, p. 137, PDF index 1: sample 7 TiO2 `5.64`; Al2O3 `9.89`; Fe2O3 `11.66`; Na2O `2.27`; K2O `4.23`.
- Table 1, p. 138, PDF index 2: sample 9 Fe2O3 `7.02`; FeO `1.41`; sample 31 FeO `2.90`; sample 36 identifier `780-K-15`.
- Table 1, p. 137, PDF index 1: sample 22 MgO `1.04`; sample 27 TiO2 `3.31`.
- Table 2, p. 139, PDF index 3: sample `49` identifier `94-8`; Fe2O3 `7.49`; FeO `1.37`; P2O5 `0.03`; T `1,344` °C; duration `24` h.
- Table 3, p. 139, PDF index 3. Column `5` has FeO `2.23`, Fe2O3 `7.04`, and T `1,349` °C. Column `70` has FeO `2.58`, Fe2O3 `6.80`, and T `1,359` °C. The physical column labels `5`, `12`, `25`, `48`, and `70` appear in the `Time (h)` row. The `Initial` column has an em dash in the temperature row.

## Static migration summary

In-memory `Migrator._migrate_extract` plus `finalize` produced 51 observations: 51 `fe3_fe2_ratio`, zero hard issues, and zero queue entries. Every migrated row has its T, printed composition, and air atmosphere; the numerical per-run `fO2_log` and total pressure remain unknown. Migration derives the ratio from the printed Fe2O3/FeO pair; no such ratio is written into this extract. The actual scored fields are now fidelity-pinned for the first Table 1 row, and equality with their composition copies is asserted for all 51 rows.

All 51 assays map to the REDOX rail. The canonical-buffer policy will make the printed-air rows callable with a typed default notice. The inventory is Table 1 samples 1–36, Table 2 samples 37, 38, 49, 40–46, and Table 3 times 5, 12, 25, 48, and 70 h.

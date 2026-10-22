# Wetzel, Pucci & Gail (2012) extraction audit

Source: `wetzel-pucci-gail-2012-sio-evaporation-coefficient`.
Machine inputs: corpus `pdftotext-layout.txt` and MinerU markdown/content-list table block for PDF pages 5 and 7. The paper is born digital; the pdftotext characters are exact. The MinerU table draft was generated with `/private/tmp/claude-501/main-review/tools/mineru_table_draft.py` (staged controller copy; not added to the branch).

## Table 2 cell audit

Printed count: 30 temperature-pressure pairs arranged as 10 physical rows by three side-by-side T/p column groups. All T, pressure, and uncertainty characters agree between pdftotext and the MinerU Markdown HTML. The p. 7 PDF render was checked once across the three column groups to confirm row/column assignment; it confirmed the assignments below. Every entry is therefore `(a)` two machine reads for characters plus `(b)` rendered layout check on p. 7. Cell coordinates below use the printed physical row and group.

| Extract observation | PDF row / group | T (K) | p_eq (Pa) ± printed width | Audit |
|---|---:|---:|---:|---|
| `wetzel_2012_table2_r01` | 1 / left | 1211.00 | 0.003 ± 0.002 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r02` | 2 / left | 1229.00 | 0.0016 ± 0.0007 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r03` | 3 / left | 1249.00 | 0.008 ± 0.003 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r04` | 4 / left | 1267.00 | 0.013 ± 0.005 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r05` | 5 / left | 1281.00 | 0.022 ± 0.009 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r06` | 6 / left | 1300.00 | 0.04 ± 0.02 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r07` | 7 / left | 1314.00 | 0.06 ± 0.02 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r08` | 8 / left | 1329.00 | 0.09 ± 0.03 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r09` | 9 / left | 1343.00 | 0.13 ± 0.05 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r10` | 10 / left | 1356.00 | 0.20 ± 0.07 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r11` | 1 / center | 1370.00 | 0.28 ± 0.10 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r12` | 2 / center | 1384.00 | 0.4 ± 0.1 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r13` | 3 / center | 1397.00 | 0.5 ± 0.2 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r14` | 4 / center | 1409.00 | 0.7 ± 0.2 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r15` | 5 / center | 1421.00 | 1.0 ± 0.3 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r16` | 6 / center | 1432.00 | 1.3 ± 0.4 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r17` | 7 / center | 1443.00 | 1.7 ± 0.5 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r18` | 8 / center | 1454.00 | 2.3 ± 0.6 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r19` | 9 / center | 1463.00 | 2.9 ± 0.8 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r20` | 10 / center | 1474.00 | 3.6 ± 1.0 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r21` | 1 / right | 1484.00 | 5 ± 1 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r22` | 2 / right | 1494.00 | 6 ± 1 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r23` | 3 / right | 1503.00 | 7 ± 2 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r24` | 4 / right | 1512.00 | 9 ± 2 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r25` | 5 / right | 1522.00 | 11 ± 2 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r26` | 6 / right | 1530.00 | 13 ± 3 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r27` | 7 / right | 1539.00 | 15 ± 3 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r28` | 8 / right | 1547.00 | 18 ± 4 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r29` | 9 / right | 1555.00 | 21 ± 4 | (a)+(b), p. 7 |
| `wetzel_2012_table2_r30` | 10 / right | 1565.00 | 24 ± 5 | (a)+(b), p. 7 |

The staged compare reports `matched=0 no-match=0 mismatch=30 presence-only=0 needs_mapping=0`. All 30 mismatches are pressure values: the draft parser treats each MinerU pressure cell (`value ± width`) as one nonnumeric candidate, so it cannot presence-match the numeric `pressure_Pa` value. The two machine reads and p. 7 render resolve every mismatch; the committed CSV preserves the tool output. There are no unresolved cell disagreements.

## Other stored numeric cells

| Stored cells | Printed location | Confirmation |
|---|---|---|
| Table 3: 318.28, 320.79; 398.21*, 410; 360 ± 4; 339.63*, 351 ± 11; 419 ± 20, 432 ± 20 kJ/mol | PDF p. 7 / published p. 1600, Table 3 | pdftotext and MinerU agree; p. 7 render confirms row/column assignment and asterisks. Five printed rows retained. |
| Alpha fit coefficients 0.20821, −3.12269×10⁻⁴, +1.35599×10⁻⁷ | PDF p. 5 / published p. 1598, Eq. 15 | pdftotext and MinerU Markdown agree; no flagged cell. Kept as the printed expression. |

The alpha expression has no supported `literature_extract.v1` value selector and is explicitly `typed_refusal`, with the expression retained. Table 3's second-law enthalpy quantity token exists, but the migrator has no selector for the printed enthalpy fields; all five rows are retained as typed refusals. The authors' own Table 3 row is `model_derived` from exactly the 30 stored Table 2 observations; the source itself is not listed as an input. The four other rows remain `quoted_attributed`. Table 1 Cu calibration pressures were optional and are not extracted. Numeric points read only from Figure 7 are figure-read candidates and excluded.

Table 2 reports amorphous SiO powder, QCM detection, and Knudsen effusion. UHV is qualitative only; no numeric total pressure or fO2 is stated. No oxygen or sweep gas is reported. `condensed_form` records amorphous solid; no unsupported crystalline phase is inferred.

Deduplication note: this is not `kems-011-wetzel-gail-2013` / `wetzel-gail-2013-sio-arrhenius` (A&A 553:A92, DOI 10.1051/0004-6361/201220803), which concerns the solid-SiO growth coefficient.

## Isolated migration

Using `tests.battery.factories.migrate_one_extract` on this extract and finalizing the migrator produced 36 observations: 30 point pressures and 6 unavailable values (the alpha expression and five Table 3 enthalpies), 2 experiment records, and 0 hard validation issues. All 30 pressure observations carry their printed temperature as a value plus page/table/physical-row/group locator. The p_sat identity profile requires temperature; for the first, middle, and final rows it maps quantity `p_sat`, gas species `SiO`, and the corresponding temperature. Total pressure and fO2 are unknown in the source and are recorded as such in the extract. The migration queue has 48 review entries: 30 pressure ancestry reminders because raw QCM inputs are not tabulated, 6 typed-refusal value notices, 5 thermochemistry method notices, 6 phase notices for alpha and Table 3 rows where a closed species-phase token is not stated, and 1 alpha temperature-range notice. No hard validation issue remains.

Expected committed store delta: +30 p_sat observations; no store regeneration committed.

# Sugawara (1999) extraction audit

## Source and table coverage

The source is a scan. Its PDF text layer is empty. The extraction used the MinerU content-list HTML tables and markdown, the supplied Tesseract OCR as an independent machine read, and page renders to confirm every numeric cell and each table's row and column layout. The MinerU converter produced 16 table blocks across four chunks; duplicate blocks and `<details>` figure reconstructions were excluded. The eight unique printed table segments are covered by the mapping and full-cell CSV.

Printed body-row counts are: Table 1, 8 starting compositions; Table 2, 40 run rows; Table 3, 40 run rows; Table 4, 1 regression-parameter row; Table 5, 5 apparatus configurations; Table 6 page 12, 18 runs and 4 starting-composition rows; Table 6 continuation page 13, 18 loop-analysis rows; Table 7, 3 regression rows. The p. 13 Table 6 rows align to the 18 run IDs on p. 12. Apparatus, loop, phase-proportion, regression, and alloy-analysis rows without a compatible measurement quantity remain context.

The cell audit contains 1,225 numeric cells. Each is marked `b` because the pdftotext layer has no text and the page render was the confirming read. It records page, table row, MinerU column, rendered column, printed spelling, and where the cell is stored. Table 6 p. 12 has 239 rendered numeric cells. MinerU omitted the printed `n°` column: thirteen of its counts appeared under draft column 7 (olivine), while five mixed-phase row counts were absent from the draft. The audit preserves that crosswalk; all 18 printed counts are stored separately as `analysis_count`, and blank olivine cells remain blank. The composition values' draft columns 8–13 correspond to printed columns 9–14; the total's draft column 14 corresponds to printed column 15.

The table-tool compare reports 118 matches and 166 mismatches, with no missing observations or unmapped values. Every mismatch is a Table 3 or Table 6 glass-analysis cell printed as a mean followed by a parenthetical 1-sigma value. The comparator does not split that form, so the scalar mean was hand-separated from the printed uncertainty and checked against the render; the raw cell is retained in `value_as_published`. There are no unresolved cell disagreements. The Table 6 Na2O loss `−233` is confirmed from the rendered minus sign. The QA pre-screen marked all 16 scan table blocks as numeric-check-skipped; its two p. 15 flags were the equation-style Table 7 transcription and a duplicated figure-derived block. Table 7 was checked against the page render; the invented figure-derived block was ignored.

## Extracted observations

- Table 2 contributes 40 runs and 80 FeO/Na2O loss observations. Its loss ratios are attributed to the author's least-squares mass-balance regression because the complete run-product inputs are not stored.
- Table 3 contributes 60 measured glass FeOt values. Parenthetical digits are recorded as 1-sigma uncertainties, per the table note.
- Table 6 contributes 18 runs, 36 author-calculated loss observations, and 108 measured quenched-glass oxide values.
- Total: 284 typed observations and 58 experiment records. The isolated migration emits 284 point values, 0 unavailable values, and 0 hard issues.

Every observation carries its printed temperature, log fO2, duration, starting composition, furnace atmosphere, and the total pressure. The title states “one atmosphere gas mixing furnace”; only that explicit statement supplies the 101325 Pa total-pressure value, with the title locator on each observation. The tables label the reported oxygen condition `log fO2`; the extract preserves each printed log and marks the 1 bar identity conversion basis as a nonprinted convention, not as an experimental pressure. The paper reports CO2/H2 as the gas pair but does not print a run-specific ratio; every observation retains an unknown ratio. Gas flow remains separate from pressure. The methods give a 40–60 mg charge range but no run-specific mass, so `sample_mass_kg` is absent.

All quenched-glass measurements carry `quench_state: not_stated`, citing p. 428: “No quench crystals were observed in either case.” This statement does not claim chemical preservation through quenching. Figures 2–9 are listed as figure-read candidates only and were not digitised.

The migrator queue contains 284 “source does not state method” entries because the closed method vocabulary has no gas-mixing-furnace token, and 116 “missing phase” entries for aggregate run loss ratios whose reported value is not assigned to one measured phase. These are queued metadata limitations, not hard migration issues.

## Identity mapping blocker

The loss observation `sugawara_1999_table2_m48413_feo_loss` maps composition, total pressure, fO2, and temperature to identity. For the printed glass-composition observations `sugawara_1999_table3_m48313_average_glass_feot` and `sugawara_1999_table6_m52701_sio2_quenched_glass`, composition, total pressure, and temperature map, but identity fO2 remains unknown even though each row carries its printed log fO2 in `point_conditions`.

The source mapping reaches the migrator's existing printed-oxygen decision path and produces a value. `fill_identity` then removes it: the `RESIDUE_COMPONENT_COMPOSITION` profile in `simulator/battery/identity.py` both requires `fO2_Pa` and lists it as permitted not-applicable, and `fill_identity` replaces permitted-axis values with not-applicable before converting the required axis back to unknown. Fixing this requires a migrator/profile behavior change, which this extract task explicitly forbids. No alternate quantity was used because it would misstate the measured glass compositions. This remains an acceptance blocker for the required identity binding.

## Expected data delta

Expected addition is one source/work record, 58 experiments, and 284 observations. The battery observation store was not regenerated or committed.

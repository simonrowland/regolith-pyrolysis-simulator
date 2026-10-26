# Frandsen (1933) extraction audit

Source: Frandsen, Bureau of Standards Journal of Research 10, RP514, pp. 35–58 (1933). The source itself confirms the page range. Input order was MinerU table HTML/markdown, pdftotext layout, then renders for table cells and flagged disagreements. MinerU chart reconstructions in details blocks were ignored.

## Printed row reconciliation

- Table 2: 13 P2O5 vapor determinations; all 13 vapor heat-content rows and all 13 printed solution-heat cells retained.
- Table 4: 20 crystalline P2O5 heat-content determinations.
- Table 5: 5 P2O5 solution determinations.
- The page renders confirm the row/column layout for each complete table. Every numeric cell retained in the extract is listed below and confirmed against its page render (b). No figure values were digitized.
- Table 2 is one MinerU block with separate upper run-input and lower result panels. The render confirms the lower panel's solution-heat and vapor-heat columns; the draft index's column headers describe the upper panel.
- MinerU table keys are p012-t04, p019-t06, and p023-t07. Their block indices 4/6/7 are not printed table numbers; extract locators use Tables 2/4/5.
- Figures 4 (vapor heat content), 6 (crystalline heat content), 7 (acid fraction versus concentration), and 8 (heat of solution versus concentration) are useful follow-up figure-read candidates; no plotted point was extracted.

## Evidence and lineage

- Table 2 vapor heat content is retained as `quoted_attributed`, with a note naming each run's calorimeter-input context row and its typed-refused heat-of-solution context row. The raw inputs have no battery quantity token and do not migrate as observations, so these author-calculated results are not `measured_reduced` lineage rows.
- Table 4 heat content is retained as `quoted_attributed`, with a note naming each run's calorimeter-input context row. Its raw inputs also remain in the context container.
- Table 5 heat of solution is retained as a `calorimetric_result` context row with `admission_status: typed_refusal` and `status: typed_refusal`. Its value, printed unit, uncertainty and locator remain intact; its note names the corresponding raw calorimeter-input context row.
- The two crystalline Cp points at 25 °C and 300 °C remain `heat_capacity_and_heat_content_series` observations. Their quote-attributed notes identify Equation (20); structured `supporting_context_row_ids` point to all 20 Table 4 calorimeter-input context rows used by the fit. The vapor Cp in Equation (17) is a mean over 360–1100 °C, so it is retained as a typed-refused `calorimetric_result` context row rather than a point Cp observation; its structured lineage points to all 13 Table 2 calorimeter-input rows.
- The Table 2 and Table 4 rows and the Equation (15)/(19) heat-content results retain `H_minus_H298` because the source prints `H_t − H_25`. The two equation results are at 358 °C, on PDF page 20 / published page 54; structured lineage links them to all 13 Table 2 and all 20 Table 4 raw input rows, respectively.
- Heat of sublimation (Eq. 21), heat of solution (Tables 2/5 and Eqs. 22/23/25/26), and heat of transformation (Eq. 24) have no battery quantity token. They remain in `species.P2O5.context` as `calorimetric_result` rows with both typed-refusal status fields and printed quantity/value/unit. Every equation result has a structured `supporting_context_row_ids` list resolving to retained context rows; the heat-solution equations also link the two intermediate Eq. (22)/(23) context results and the five raw Table 5 records.
- The 38 raw Table 2/4/5 calorimeter-run records remain in `species.P2O5.context` as `calorimeter_run_inputs`. The 24 unsupported heat results and one interval-mean Cp are context records too. Context evidence has no store-observation parent edges; structured context IDs retain the lineage as ruled by the controller (t-1526).
- Equation (25), PDF page 24 / published page 58, prints `P2O5(cr) + 3 H2O(l) → 2 H3PO4(aq)`; the extract records that coefficient.
- Printed uncertainties are stored verbatim with confidence level not stated.
- The 13 Table 2 vapor runs and their raw input records state an external pressure of one atmosphere on PDF page 11; Equation (15) is fitted to those runs. Equation (17) prints “1 atm” on PDF page 16, and the Equation (21) sublimation point is defined by vapor pressure equal to one atmosphere on PDF page 20. These statements ground the stored 101325 Pa conditions.
- Table 4's sealed-sample experiment and Table 2/Table 5 solution measurements do not print total pressure. Their pressure remains absent. Table 2 and Table 5 solution measurements state 25 °C.

## Migration and schema status

- The real isolated migration sees 37 admitted observations, 0 hard issues, 37 unavailable values, and 0 registry issues. The printed units `cal.15 mole−1` and `cal.15 mole−1 °C−1` have no exact migrator conversion, so all 35 heat-content and 2 point-Cp values remain unavailable. It also emits 74 non-hard queue items: 37 for the unsupported printed unit and 37 because the closed method vocabulary has no calorimetry token; each row's note retains its printed procedure. The remaining 63 rows migrate to the work record's context list and do not create scored observations.
- The extract validator accepts all 37 observations and 63 context rows. The two point-Cp results and 35 `H_minus_H298` results use existing observation types/tokens; unsupported heat results and the interval-mean Cp use the controller-ruled `calorimetric_result` context kind. No schema or migrator change was made.
- Expected store delta, with no battery store regeneration committed: +37 observations (all unavailable from the unsupported printed units `cal.15 mole−1` and `cal.15 mole−1 °C−1`) and +63 work-record context rows; 0 hard or registry issues.
- Running the committed base comparator with the committed mapping and a fresh seven-table draft reports `matched=0 no-match=35 mismatch=2 presence-only=0 needs_mapping=0 total=37`. Thirty-three table-row observations have no candidates because this version keys MinerU drafts by block indices (`p012-t04`, `p019-t06`) while the extract correctly records printed table numbers (`2`, `4`); the mapping does not translate those locator numbers. The other two no-match rows are the Equation (15)/(19) values on PDF page 20, where there are no table cells. The Equation (20) Cp points have equation locators on PDF page 19, which the green comparator cannot match to the printed equations; it falls back to 186 unrelated page-19 table candidates for each and reports the two mismatches. These are comparator-location mismatches, not source-cell disagreements; the equation values are checked separately against the rendered source page. The committed compare CSV is the unedited output of the green comparator; the table-tool owner is addressing table-index and equation-locator handling on t-1519.

## Full retained-cell audit

| PDF page | Printed table | Run | Stored cell | Printed value | Confirmation |
|---:|---:|---:|---|---:|---|
| 12 | 2 | Run 1 | furnace_temperature_C | 549 | render (b) |
| 12 | 2 | Run 1 | well_orifice_drop_C | 4 | render (b) |
| 12 | 2 | Run 1 | vapor_temperature_at_orifice_C | 545 | render (b) |
| 12 | 2 | Run 1 | experimental_period_min | 1.5 | render (b) |
| 12 | 2 | Run 1 | p2o5_weight_g_vac | 6.848 | render (b) |
| 12 | 2 | Run 1 | equivalent_steam_rate_g_min | 2.357 | render (b) |
| 12 | 2 | Run 1 | evaporated_water_g | 0.015 | render (b) |
| 12 | 2 | Run 1 | final_calorimeter_temperature_C | 23.19 | render (b) |
| 12 | 2 | Run 1 | total_heat_measured_in_calorimeter_cal15 | 3,710 | render (b) |
| 12 | 2 | Run 1 | correction_from_blank_experiments_cal15 | 254 | render (b) |
| 12 | 2 | Run 1 | increase_of_radiation_from_spout_cal15 | 3 | render (b) |
| 12 | 2 | Run 1 | heat_lost_from_neck_cal15 | 76 | render (b) |
| 12 | 2 | Run 1 | heat_lost_by_evaporation_of_water_cal15 | 9 | render (b) |
| 12 | 2 | Run 1 | correction_to_25C_cal15 | -2 | render (b) |
| 12 | 2 | Run 1 | Q_isoln | 45,950 | render (b) |
| 12 | 2 | Run 1 | H_t−H_25 | 27,400 | render (b) |
| 12 | 2 | Run 2 | furnace_temperature_C | 549 | render (b) |
| 12 | 2 | Run 2 | well_orifice_drop_C | 4 | render (b) |
| 12 | 2 | Run 2 | vapor_temperature_at_orifice_C | 545 | render (b) |
| 12 | 2 | Run 2 | experimental_period_min | 1.5 | render (b) |
| 12 | 2 | Run 2 | p2o5_weight_g_vac | 6.728 | render (b) |
| 12 | 2 | Run 2 | equivalent_steam_rate_g_min | 2.317 | render (b) |
| 12 | 2 | Run 2 | evaporated_water_g | .040 | render (b) |
| 12 | 2 | Run 2 | final_calorimeter_temperature_C | 23.08 | render (b) |
| 12 | 2 | Run 2 | total_heat_measured_in_calorimeter_cal15 | 3,590 | render (b) |
| 12 | 2 | Run 2 | correction_from_blank_experiments_cal15 | 254 | render (b) |
| 12 | 2 | Run 2 | increase_of_radiation_from_spout_cal15 | 3 | render (b) |
| 12 | 2 | Run 2 | heat_lost_from_neck_cal15 | 74 | render (b) |
| 12 | 2 | Run 2 | heat_lost_by_evaporation_of_water_cal15 | 25 | render (b) |
| 12 | 2 | Run 2 | correction_to_25C_cal15 | -2 | render (b) |
| 12 | 2 | Run 2 | Q_isoln | 45,910 | render (b) |
| 12 | 2 | Run 2 | H_t−H_25 | 26,490 | render (b) |
| 12 | 2 | Run 3 | furnace_temperature_C | 655 | render (b) |
| 12 | 2 | Run 3 | well_orifice_drop_C | 5 | render (b) |
| 12 | 2 | Run 3 | vapor_temperature_at_orifice_C | 650 | render (b) |
| 12 | 2 | Run 3 | experimental_period_min | 2.0 | render (b) |
| 12 | 2 | Run 3 | p2o5_weight_g_vac | 11.011 | render (b) |
| 12 | 2 | Run 3 | equivalent_steam_rate_g_min | 2,790 | render (b) |
| 12 | 2 | Run 3 | evaporated_water_g | .040 | render (b) |
| 12 | 2 | Run 3 | final_calorimeter_temperature_C | 25.29 | render (b) |
| 12 | 2 | Run 3 | total_heat_measured_in_calorimeter_cal15 | 6,052 | render (b) |
| 12 | 2 | Run 3 | correction_from_blank_experiments_cal15 | 357 | render (b) |
| 12 | 2 | Run 3 | increase_of_radiation_from_spout_cal15 | 7 | render (b) |
| 12 | 2 | Run 3 | heat_lost_from_neck_cal15 | 103 | render (b) |
| 12 | 2 | Run 3 | heat_lost_by_evaporation_of_water_cal15 | 25 | render (b) |
| 12 | 2 | Run 3 | correction_to_25C_cal15 | 0 | render (b) |
| 12 | 2 | Run 3 | Q_isoln | 47,020 | render (b) |
| 12 | 2 | Run 3 | H_t−H_25 | 28,000 | render (b) |
| 12 | 2 | Run 4 | furnace_temperature_C | 655 | render (b) |
| 12 | 2 | Run 4 | well_orifice_drop_C | 5 | render (b) |
| 12 | 2 | Run 4 | vapor_temperature_at_orifice_C | 650 | render (b) |
| 12 | 2 | Run 4 | experimental_period_min | 2.0 | render (b) |
| 12 | 2 | Run 4 | p2o5_weight_g_vac | 17.407 | render (b) |
| 12 | 2 | Run 4 | equivalent_steam_rate_g_min | 4.408 | render (b) |
| 12 | 2 | Run 4 | evaporated_water_g | .296 | render (b) |
| 12 | 2 | Run 4 | final_calorimeter_temperature_C | 26.69 | render (b) |
| 12 | 2 | Run 4 | total_heat_measured_in_calorimeter_cal15 | 9,358 | render (b) |
| 12 | 2 | Run 4 | correction_from_blank_experiments_cal15 | 256 | render (b) |
| 12 | 2 | Run 4 | increase_of_radiation_from_spout_cal15 | 7 | render (b) |
| 12 | 2 | Run 4 | heat_lost_from_neck_cal15 | 103 | render (b) |
| 12 | 2 | Run 4 | heat_lost_by_evaporation_of_water_cal15 | 182 | render (b) |
| 12 | 2 | Run 4 | correction_to_25C_cal15 | 5 | render (b) |
| 12 | 2 | Run 4 | Q_isoln | 48,220 | render (b) |
| 12 | 2 | Run 4 | H_t−H_25 | 28,350 | render (b) |
| 12 | 2 | Run 5 | furnace_temperature_C | 812 | render (b) |
| 12 | 2 | Run 5 | well_orifice_drop_C | 8 | render (b) |
| 12 | 2 | Run 5 | vapor_temperature_at_orifice_C | 804 | render (b) |
| 12 | 2 | Run 5 | experimental_period_min | 1.5 | render (b) |
| 12 | 2 | Run 5 | p2o5_weight_g_vac | 9.105 | render (b) |
| 12 | 2 | Run 5 | equivalent_steam_rate_g_min | 2.996 | render (b) |
| 12 | 2 | Run 5 | evaporated_water_g | .065 | render (b) |
| 12 | 2 | Run 5 | final_calorimeter_temperature_C | 26.45 | render (b) |
| 12 | 2 | Run 5 | total_heat_measured_in_calorimeter_cal15 | 5,631 | render (b) |
| 12 | 2 | Run 5 | correction_from_blank_experiments_cal15 | 326 | render (b) |
| 12 | 2 | Run 5 | increase_of_radiation_from_spout_cal15 | 10 | render (b) |
| 12 | 2 | Run 5 | heat_lost_from_neck_cal15 | 91 | render (b) |
| 12 | 2 | Run 5 | heat_lost_by_evaporation_of_water_cal15 | 40 | render (b) |
| 12 | 2 | Run 5 | correction_to_25C_cal15 | 2 | render (b) |
| 12 | 2 | Run 5 | Q_isoln | 46,560 | render (b) |
| 12 | 2 | Run 5 | H_t−H_25 | 38,130 | render (b) |
| 12 | 2 | Run 6 | furnace_temperature_C | 805 | render (b) |
| 12 | 2 | Run 6 | well_orifice_drop_C | 11 | render (b) |
| 12 | 2 | Run 6 | vapor_temperature_at_orifice_C | 794 | render (b) |
| 12 | 2 | Run 6 | experimental_period_min | 1.5 | render (b) |
| 12 | 2 | Run 6 | p2o5_weight_g_vac | 4.161 | render (b) |
| 12 | 2 | Run 6 | equivalent_steam_rate_g_min | 1.369 | render (b) |
| 12 | 2 | Run 6 | evaporated_water_g | .033 | render (b) |
| 12 | 2 | Run 6 | final_calorimeter_temperature_C | 24.35 | render (b) |
| 12 | 2 | Run 6 | total_heat_measured_in_calorimeter_cal15 | 2,677 | render (b) |
| 12 | 2 | Run 6 | correction_from_blank_experiments_cal15 | 344 | render (b) |
| 12 | 2 | Run 6 | increase_of_radiation_from_spout_cal15 | 10 | render (b) |
| 12 | 2 | Run 6 | heat_lost_from_neck_cal15 | 42 | render (b) |
| 12 | 2 | Run 6 | heat_lost_by_evaporation_of_water_cal15 | 20 | render (b) |
| 12 | 2 | Run 6 | correction_to_25C_cal15 | -1 | render (b) |
| 12 | 2 | Run 6 | Q_isoln | 45,110 | render (b) |
| 12 | 2 | Run 6 | H_t−H_25 | 36,300 | render (b) |
| 12 | 2 | Run 7 | furnace_temperature_C | 801 | render (b) |
| 12 | 2 | Run 7 | well_orifice_drop_C | 9 | render (b) |
| 12 | 2 | Run 7 | vapor_temperature_at_orifice_C | 792 | render (b) |
| 12 | 2 | Run 7 | experimental_period_min | 1.5 | render (b) |
| 12 | 2 | Run 7 | p2o5_weight_g_vac | 7.203 | render (b) |
| 12 | 2 | Run 7 | equivalent_steam_rate_g_min | 2.372 | render (b) |
| 12 | 2 | Run 7 | evaporated_water_g | .050 | render (b) |
| 12 | 2 | Run 7 | final_calorimeter_temperature_C | 25.45 | render (b) |
| 12 | 2 | Run 7 | total_heat_measured_in_calorimeter_cal15 | 4,496 | render (b) |
| 12 | 2 | Run 7 | correction_from_blank_experiments_cal15 | 296 | render (b) |
| 12 | 2 | Run 7 | increase_of_radiation_from_spout_cal15 | 10 | render (b) |
| 12 | 2 | Run 7 | heat_lost_from_neck_cal15 | 72 | render (b) |
| 12 | 2 | Run 7 | heat_lost_by_evaporation_of_water_cal15 | 31 | render (b) |
| 12 | 2 | Run 7 | correction_to_25C_cal15 | 1 | render (b) |
| 12 | 2 | Run 7 | Q_isoln | 46,060 | render (b) |
| 12 | 2 | Run 7 | H_t−H_25 | 38,600 | render (b) |
| 12 | 2 | Run 8 | furnace_temperature_C | 949 | render (b) |
| 12 | 2 | Run 8 | well_orifice_drop_C | 26 | render (b) |
| 12 | 2 | Run 8 | vapor_temperature_at_orifice_C | 923 | render (b) |
| 12 | 2 | Run 8 | experimental_period_min | 1.5 | render (b) |
| 12 | 2 | Run 8 | p2o5_weight_g_vac | 2.360 | render (b) |
| 12 | 2 | Run 8 | equivalent_steam_rate_g_min | .758 | render (b) |
| 12 | 2 | Run 8 | evaporated_water_g | .026 | render (b) |
| 12 | 2 | Run 8 | final_calorimeter_temperature_C | 21.88 | render (b) |
| 12 | 2 | Run 8 | total_heat_measured_in_calorimeter_cal15 | 1,998 | render (b) |
| 12 | 2 | Run 8 | correction_from_blank_experiments_cal15 | 538 | render (b) |
| 12 | 2 | Run 8 | increase_of_radiation_from_spout_cal15 | 18 | render (b) |
| 12 | 2 | Run 8 | heat_lost_from_neck_cal15 | 29 | render (b) |
| 12 | 2 | Run 8 | heat_lost_by_evaporation_of_water_cal15 | 16 | render (b) |
| 12 | 2 | Run 8 | correction_to_25C_cal15 | -1 | render (b) |
| 12 | 2 | Run 8 | Q_isoln | 44,460 | render (b) |
| 12 | 2 | Run 8 | H_t−H_25 | 44,990 | render (b) |
| 12 | 2 | Run 9 | furnace_temperature_C | 950 | render (b) |
| 12 | 2 | Run 9 | well_orifice_drop_C | 20 | render (b) |
| 12 | 2 | Run 9 | vapor_temperature_at_orifice_C | 930 | render (b) |
| 12 | 2 | Run 9 | experimental_period_min | 1.5 | render (b) |
| 12 | 2 | Run 9 | p2o5_weight_g_vac | 8.771 | render (b) |
| 12 | 2 | Run 9 | equivalent_steam_rate_g_min | 2.818 | render (b) |
| 12 | 2 | Run 9 | evaporated_water_g | .080 | render (b) |
| 12 | 2 | Run 9 | final_calorimeter_temperature_C | 24.50 | render (b) |
| 12 | 2 | Run 9 | total_heat_measured_in_calorimeter_cal15 | 5,766 | render (b) |
| 12 | 2 | Run 9 | correction_from_blank_experiments_cal15 | 506 | render (b) |
| 12 | 2 | Run 9 | increase_of_radiation_from_spout_cal15 | 18 | render (b) |
| 12 | 2 | Run 9 | heat_lost_from_neck_cal15 | 109 | render (b) |
| 12 | 2 | Run 9 | heat_lost_by_evaporation_of_water_cal15 | 49 | render (b) |
| 12 | 2 | Run 9 | correction_to_25C_cal15 | -1 | render (b) |
| 12 | 2 | Run 9 | Q_isoln | 46,490 | render (b) |
| 12 | 2 | Run 9 | H_t−H_25 | 40,950 | render (b) |
| 12 | 2 | Run 10 | furnace_temperature_C | 958 | render (b) |
| 12 | 2 | Run 10 | well_orifice_drop_C | 22 | render (b) |
| 12 | 2 | Run 10 | vapor_temperature_at_orifice_C | 936 | render (b) |
| 12 | 2 | Run 10 | experimental_period_min | 1.5 | render (b) |
| 12 | 2 | Run 10 | p2o5_weight_g_vac | 9.594 | render (b) |
| 12 | 2 | Run 10 | equivalent_steam_rate_g_min | 3.079 | render (b) |
| 12 | 2 | Run 10 | evaporated_water_g | .088 | render (b) |
| 12 | 2 | Run 10 | final_calorimeter_temperature_C | 24.70 | render (b) |
| 12 | 2 | Run 10 | total_heat_measured_in_calorimeter_cal15 | 6,261 | render (b) |
| 12 | 2 | Run 10 | correction_from_blank_experiments_cal15 | 494 | render (b) |
| 12 | 2 | Run 10 | increase_of_radiation_from_spout_cal15 | 18 | render (b) |
| 12 | 2 | Run 10 | heat_lost_from_neck_cal15 | 119 | render (b) |
| 12 | 2 | Run 10 | heat_lost_by_evaporation_of_water_cal15 | 54 | render (b) |
| 12 | 2 | Run 10 | correction_to_25C_cal15 | -1 | render (b) |
| 12 | 2 | Run 10 | Q_isoln | 46,700 | render (b) |
| 12 | 2 | Run 10 | H_t−H_25 | 40,970 | render (b) |
| 12 | 2 | Run 11 | furnace_temperature_C | 1,098 | render (b) |
| 12 | 2 | Run 11 | well_orifice_drop_C | 36 | render (b) |
| 12 | 2 | Run 11 | vapor_temperature_at_orifice_C | 1,062 | render (b) |
| 12 | 2 | Run 11 | experimental_period_min | 1.5 | render (b) |
| 12 | 2 | Run 11 | p2o5_weight_g_vac | 13.889 | render (b) |
| 12 | 2 | Run 11 | equivalent_steam_rate_g_min | 4.358 | render (b) |
| 12 | 2 | Run 11 | evaporated_water_g | .100 | render (b) |
| 12 | 2 | Run 11 | final_calorimeter_temperature_C | 26.63 | render (b) |
| 12 | 2 | Run 11 | total_heat_measured_in_calorimeter_cal15 | 9,222 | render (b) |
| 12 | 2 | Run 11 | correction_from_blank_experiments_cal15 | 508 | render (b) |
| 12 | 2 | Run 11 | increase_of_radiation_from_spout_cal15 | 25 | render (b) |
| 12 | 2 | Run 11 | heat_lost_from_neck_cal15 | 206 | render (b) |
| 12 | 2 | Run 11 | heat_lost_by_evaporation_of_water_cal15 | 61 | render (b) |
| 12 | 2 | Run 11 | correction_to_25C_cal15 | 4 | render (b) |
| 12 | 2 | Run 11 | Q_isoln | 47,630 | render (b) |
| 12 | 2 | Run 11 | H_t−H_25 | 44,000 | render (b) |
| 12 | 2 | Run 12 | furnace_temperature_C | 1,098 | render (b) |
| 12 | 2 | Run 12 | well_orifice_drop_C | 37 | render (b) |
| 12 | 2 | Run 12 | vapor_temperature_at_orifice_C | 1,061 | render (b) |
| 12 | 2 | Run 12 | experimental_period_min | 1.5 | render (b) |
| 12 | 2 | Run 12 | p2o5_weight_g_vac | 12.159 | render (b) |
| 12 | 2 | Run 12 | equivalent_steam_rate_g_min | 3.813 | render (b) |
| 12 | 2 | Run 12 | evaporated_water_g | .489 | render (b) |
| 12 | 2 | Run 12 | final_calorimeter_temperature_C | 25.97 | render (b) |
| 12 | 2 | Run 12 | total_heat_measured_in_calorimeter_cal15 | 8,015 | render (b) |
| 12 | 2 | Run 12 | correction_from_blank_experiments_cal15 | 520 | render (b) |
| 12 | 2 | Run 12 | increase_of_radiation_from_spout_cal15 | 25 | render (b) |
| 12 | 2 | Run 12 | heat_lost_from_neck_cal15 | 206 | render (b) |
| 12 | 2 | Run 12 | heat_lost_by_evaporation_of_water_cal15 | 301 | render (b) |
| 12 | 2 | Run 12 | correction_to_25C_cal15 | 2 | render (b) |
| 12 | 2 | Run 12 | Q_isoln | 47,280 | render (b) |
| 12 | 2 | Run 12 | H_t−H_25 | 45,930 | render (b) |
| 12 | 2 | Run 13 | furnace_temperature_C | 1,095 | render (b) |
| 12 | 2 | Run 13 | well_orifice_drop_C | 37 | render (b) |
| 12 | 2 | Run 13 | vapor_temperature_at_orifice_C | 1,058 | render (b) |
| 12 | 2 | Run 13 | experimental_period_min | 1.5 | render (b) |
| 12 | 2 | Run 13 | p2o5_weight_g_vac | 12.159 | render (b) |
| 12 | 2 | Run 13 | equivalent_steam_rate_g_min | 3.813 | render (b) |
| 12 | 2 | Run 13 | evaporated_water_g | .036 | render (b) |
| 12 | 2 | Run 13 | final_calorimeter_temperature_C | 25.81 | render (b) |
| 12 | 2 | Run 13 | total_heat_measured_in_calorimeter_cal15 | 8,045 | render (b) |
| 12 | 2 | Run 13 | correction_from_blank_experiments_cal15 | 528 | render (b) |
| 12 | 2 | Run 13 | increase_of_radiation_from_spout_cal15 | 25 | render (b) |
| 12 | 2 | Run 13 | heat_lost_from_neck_cal15 | 206 | render (b) |
| 12 | 2 | Run 13 | heat_lost_by_evaporation_of_water_cal15 | 22 | render (b) |
| 12 | 2 | Run 13 | correction_to_25C_cal15 | 2 | render (b) |
| 12 | 2 | Run 13 | Q_isoln | 47,280 | render (b) |
| 12 | 2 | Run 13 | H_t−H_25 | 42,930 | render (b) |
| 19 | 4 | Run 1 | bath_temperature_C | 34.90 | render (b) |
| 19 | 4 | Run 1 | p2o5_weight_g_vac | 12.7209 | render (b) |
| 19 | 4 | Run 1 | silica_weight_g_vac | 7.0476 | render (b) |
| 19 | 4 | Run 1 | final_calorimeter_temperature_C | 23.56 | render (b) |
| 19 | 4 | Run 1 | heat_transferred_to_calorimeter_cal15 | 38.59 | render (b) |
| 19 | 4 | Run 1 | heat_lost_in_transfer_cal15 | 0.2 | render (b) |
| 19 | 4 | Run 1 | heat_transferred_with_silica_cal15 | 14.45 | render (b) |
| 19 | 4 | Run 1 | correction_to_25C_cal15 | -3.11 | render (b) |
| 19 | 4 | Run 1 | H_t−H_25 | 237 | render (b) |
| 19 | 4 | Run 2 | bath_temperature_C | 76.57 | render (b) |
| 19 | 4 | Run 2 | p2o5_weight_g_vac | 12.7209 | render (b) |
| 19 | 4 | Run 2 | silica_weight_g_vac | 7.0476 | render (b) |
| 19 | 4 | Run 2 | final_calorimeter_temperature_C | 22.67 | render (b) |
| 19 | 4 | Run 2 | heat_transferred_to_calorimeter_cal15 | 189.62 | render (b) |
| 19 | 4 | Run 2 | heat_lost_in_transfer_cal15 | 1.4 | render (b) |
| 19 | 4 | Run 2 | heat_transferred_with_silica_cal15 | 70.55 | render (b) |
| 19 | 4 | Run 2 | correction_to_25C_cal15 | -5.04 | render (b) |
| 19 | 4 | Run 2 | H_t−H_25 | 1,289 | render (b) |
| 19 | 4 | Run 3 | bath_temperature_C | 131.2 | render (b) |
| 19 | 4 | Run 3 | p2o5_weight_g_vac | 12.7209 | render (b) |
| 19 | 4 | Run 3 | silica_weight_g_vac | 7.0476 | render (b) |
| 19 | 4 | Run 3 | final_calorimeter_temperature_C | 23.15 | render (b) |
| 19 | 4 | Run 3 | heat_transferred_to_calorimeter_cal15 | 423.2 | render (b) |
| 19 | 4 | Run 3 | heat_lost_in_transfer_cal15 | 3.0 | render (b) |
| 19 | 4 | Run 3 | heat_transferred_with_silica_cal15 | 146.7 | render (b) |
| 19 | 4 | Run 3 | correction_to_25C_cal15 | -4.00 | render (b) |
| 19 | 4 | Run 3 | H_t−H_25 | 3,076 | render (b) |
| 19 | 4 | Run 4 | bath_temperature_C | 183.3 | render (b) |
| 19 | 4 | Run 4 | p2o5_weight_g_vac | 12.7209 | render (b) |
| 19 | 4 | Run 4 | silica_weight_g_vac | 7.0476 | render (b) |
| 19 | 4 | Run 4 | final_calorimeter_temperature_C | 24.03 | render (b) |
| 19 | 4 | Run 4 | heat_transferred_to_calorimeter_cal15 | 630.8 | render (b) |
| 19 | 4 | Run 4 | heat_lost_in_transfer_cal15 | 6.0 | render (b) |
| 19 | 4 | Run 4 | heat_transferred_with_silica_cal15 | 225.0 | render (b) |
| 19 | 4 | Run 4 | correction_to_25C_cal15 | -2.10 | render (b) |
| 19 | 4 | Run 4 | H_t−H_25 | 4,575 | render (b) |
| 19 | 4 | Run 5 | bath_temperature_C | 238.0 | render (b) |
| 19 | 4 | Run 5 | p2o5_weight_g_vac | 12.7209 | render (b) |
| 19 | 4 | Run 5 | silica_weight_g_vac | 7.0476 | render (b) |
| 19 | 4 | Run 5 | final_calorimeter_temperature_C | 24.63 | render (b) |
| 19 | 4 | Run 5 | heat_transferred_to_calorimeter_cal15 | 889.4 | render (b) |
| 19 | 4 | Run 5 | heat_lost_in_transfer_cal15 | 9.4 | render (b) |
| 19 | 4 | Run 5 | heat_transferred_with_silica_cal15 | 312.7 | render (b) |
| 19 | 4 | Run 5 | correction_to_25C_cal15 | -.80 | render (b) |
| 19 | 4 | Run 5 | H_t−H_25 | 6,536 | render (b) |
| 19 | 4 | Run 6 | bath_temperature_C | 36.0 | render (b) |
| 19 | 4 | Run 6 | p2o5_weight_g_vac | 8.8274 | render (b) |
| 19 | 4 | Run 6 | silica_weight_g_vac | 6.8536 | render (b) |
| 19 | 4 | Run 6 | final_calorimeter_temperature_C | 24.25 | render (b) |
| 19 | 4 | Run 6 | heat_transferred_to_calorimeter_cal15 | 32.7 | render (b) |
| 19 | 4 | Run 6 | heat_lost_in_transfer_cal15 | .1 | render (b) |
| 19 | 4 | Run 6 | heat_transferred_with_silica_cal15 | 14.11 | render (b) |
| 19 | 4 | Run 6 | correction_to_25C_cal15 | -1.26 | render (b) |
| 19 | 4 | Run 6 | H_t−H_25 | 280 | render (b) |
| 19 | 4 | Run 7 | bath_temperature_C | 64.4 | render (b) |
| 19 | 4 | Run 7 | p2o5_weight_g_vac | 8.8274 | render (b) |
| 19 | 4 | Run 7 | silica_weight_g_vac | 6.8536 | render (b) |
| 19 | 4 | Run 7 | final_calorimeter_temperature_C | 25.48 | render (b) |
| 19 | 4 | Run 7 | heat_transferred_to_calorimeter_cal15 | 112.1 | render (b) |
| 19 | 4 | Run 7 | heat_lost_in_transfer_cal15 | .5 | render (b) |
| 19 | 4 | Run 7 | heat_transferred_with_silica_cal15 | 48.8 | render (b) |
| 19 | 4 | Run 7 | correction_to_25C_cal15 | .72 | render (b) |
| 19 | 4 | Run 7 | H_t−H_25 | 1,038 | render (b) |
| 19 | 4 | Run 8 | bath_temperature_C | 99.9 | render (b) |
| 19 | 4 | Run 8 | p2o5_weight_g_vac | 8.8274 | render (b) |
| 19 | 4 | Run 8 | silica_weight_g_vac | 6.8536 | render (b) |
| 19 | 4 | Run 8 | final_calorimeter_temperature_C | 25.60 | render (b) |
| 19 | 4 | Run 8 | heat_transferred_to_calorimeter_cal15 | 220.4 | render (b) |
| 19 | 4 | Run 8 | heat_lost_in_transfer_cal15 | 1.0 | render (b) |
| 19 | 4 | Run 8 | heat_transferred_with_silica_cal15 | 95.9 | render (b) |
| 19 | 4 | Run 8 | correction_to_25C_cal15 | .90 | render (b) |
| 19 | 4 | Run 8 | H_t−H_25 | 2,034 | render (b) |
| 19 | 4 | Run 9 | bath_temperature_C | 131.4 | render (b) |
| 19 | 4 | Run 9 | p2o5_weight_g_vac | 8.8274 | render (b) |
| 19 | 4 | Run 9 | silica_weight_g_vac | 6.8536 | render (b) |
| 19 | 4 | Run 9 | final_calorimeter_temperature_C | 25.52 | render (b) |
| 19 | 4 | Run 9 | heat_transferred_to_calorimeter_cal15 | 321.0 | render (b) |
| 19 | 4 | Run 9 | heat_lost_in_transfer_cal15 | 1.7 | render (b) |
| 19 | 4 | Run 9 | heat_transferred_with_silica_cal15 | 139.9 | render (b) |
| 19 | 4 | Run 9 | correction_to_25C_cal15 | .78 | render (b) |
| 19 | 4 | Run 9 | H_t−H_25 | 2,954 | render (b) |
| 19 | 4 | Run 10 | bath_temperature_C | 164.5 | render (b) |
| 19 | 4 | Run 10 | p2o5_weight_g_vac | 8.8274 | render (b) |
| 19 | 4 | Run 10 | silica_weight_g_vac | 6.8536 | render (b) |
| 19 | 4 | Run 10 | final_calorimeter_temperature_C | 25.64 | render (b) |
| 19 | 4 | Run 10 | heat_transferred_to_calorimeter_cal15 | 434.0 | render (b) |
| 19 | 4 | Run 10 | heat_lost_in_transfer_cal15 | 3.2 | render (b) |
| 19 | 4 | Run 10 | heat_transferred_with_silica_cal15 | 188.7 | render (b) |
| 19 | 4 | Run 10 | correction_to_25C_cal15 | .96 | render (b) |
| 19 | 4 | Run 10 | H_t−H_25 | 4,015 | render (b) |
| 19 | 4 | Run 11 | bath_temperature_C | 183.6 | render (b) |
| 19 | 4 | Run 11 | p2o5_weight_g_vac | 8.8274 | render (b) |
| 19 | 4 | Run 11 | silica_weight_g_vac | 6.8536 | render (b) |
| 19 | 4 | Run 11 | final_calorimeter_temperature_C | 26.42 | render (b) |
| 19 | 4 | Run 11 | heat_transferred_to_calorimeter_cal15 | 500.8 | render (b) |
| 19 | 4 | Run 11 | heat_lost_in_transfer_cal15 | 3.8 | render (b) |
| 19 | 4 | Run 11 | heat_transferred_with_silica_cal15 | 216.9 | render (b) |
| 19 | 4 | Run 11 | correction_to_25C_cal15 | 2.13 | render (b) |
| 19 | 4 | Run 11 | H_t−H_25 | 4,663 | render (b) |
| 19 | 4 | Run 12 | bath_temperature_C | 210.2 | render (b) |
| 19 | 4 | Run 12 | p2o5_weight_g_vac | 8.8274 | render (b) |
| 19 | 4 | Run 12 | silica_weight_g_vac | 6.8536 | render (b) |
| 19 | 4 | Run 12 | final_calorimeter_temperature_C | 26.93 | render (b) |
| 19 | 4 | Run 12 | heat_transferred_to_calorimeter_cal15 | 595.4 | render (b) |
| 19 | 4 | Run 12 | heat_lost_in_transfer_cal15 | 5.0 | render (b) |
| 19 | 4 | Run 12 | heat_transferred_with_silica_cal15 | 257.3 | render (b) |
| 19 | 4 | Run 12 | correction_to_25C_cal15 | 2.90 | render (b) |
| 19 | 4 | Run 12 | H_t−H_25 | 5,568 | render (b) |
| 19 | 4 | Run 13 | bath_temperature_C | 76.57 | render (b) |
| 19 | 4 | Run 13 | p2o5_weight_g_vac | 3.2269 | render (b) |
| 19 | 4 | Run 13 | silica_weight_g_vac | 10.1655 | render (b) |
| 19 | 4 | Run 13 | final_calorimeter_temperature_C | 22.84 | render (b) |
| 19 | 4 | Run 13 | heat_transferred_to_calorimeter_cal15 | 131.1 | render (b) |
| 19 | 4 | Run 13 | heat_lost_in_transfer_cal15 | 1.4 | render (b) |
| 19 | 4 | Run 13 | heat_transferred_with_silica_cal15 | 101.45 | render (b) |
| 19 | 4 | Run 13 | correction_to_25C_cal15 | -1.19 | render (b) |
| 19 | 4 | Run 13 | H_t−H_25 | 1,314 | render (b) |
| 19 | 4 | Run 14 | bath_temperature_C | 131.2 | render (b) |
| 19 | 4 | Run 14 | p2o5_weight_g_vac | 3.2269 | render (b) |
| 19 | 4 | Run 14 | silica_weight_g_vac | 10.1655 | render (b) |
| 19 | 4 | Run 14 | final_calorimeter_temperature_C | 23.89 | render (b) |
| 19 | 4 | Run 14 | heat_transferred_to_calorimeter_cal15 | 277.7 | render (b) |
| 19 | 4 | Run 14 | heat_lost_in_transfer_cal15 | 3.3 | render (b) |
| 19 | 4 | Run 14 | heat_transferred_with_silica_cal15 | 210.2 | render (b) |
| 19 | 4 | Run 14 | correction_to_25C_cal15 | -.61 | render (b) |
| 19 | 4 | Run 14 | H_t−H_25 | 3,089 | render (b) |
| 19 | 4 | Run 15 | bath_temperature_C | 183.3 | render (b) |
| 19 | 4 | Run 15 | p2o5_weight_g_vac | 3.2269 | render (b) |
| 19 | 4 | Run 15 | silica_weight_g_vac | 10.1655 | render (b) |
| 19 | 4 | Run 15 | final_calorimeter_temperature_C | 24.98 | render (b) |
| 19 | 4 | Run 15 | heat_transferred_to_calorimeter_cal15 | 416.2 | render (b) |
| 19 | 4 | Run 15 | heat_lost_in_transfer_cal15 | 6.4 | render (b) |
| 19 | 4 | Run 15 | heat_transferred_with_silica_cal15 | 322.5 | render (b) |
| 19 | 4 | Run 15 | correction_to_25C_cal15 | -.01 | render (b) |
| 19 | 4 | Run 15 | H_t−H_25 | 4,406 | render (b) |
| 19 | 4 | Run 16 | bath_temperature_C | 239.0 | render (b) |
| 19 | 4 | Run 16 | p2o5_weight_g_vac | 3.2269 | render (b) |
| 19 | 4 | Run 16 | silica_weight_g_vac | 10.1655 | render (b) |
| 19 | 4 | Run 16 | final_calorimeter_temperature_C | 24.09 | render (b) |
| 19 | 4 | Run 16 | heat_transferred_to_calorimeter_cal15 | 590.4 | render (b) |
| 19 | 4 | Run 16 | heat_lost_in_transfer_cal15 | 10.4 | render (b) |
| 19 | 4 | Run 16 | heat_transferred_with_silica_cal15 | 453.8 | render (b) |
| 19 | 4 | Run 16 | correction_to_25C_cal15 | -.50 | render (b) |
| 19 | 4 | Run 16 | H_t−H_25 | 6,449 | render (b) |
| 19 | 4 | Run 17 | bath_temperature_C | 288.0 | render (b) |
| 19 | 4 | Run 17 | p2o5_weight_g_vac | 3.2269 | render (b) |
| 19 | 4 | Run 17 | silica_weight_g_vac | 10.1655 | render (b) |
| 19 | 4 | Run 17 | final_calorimeter_temperature_C | 24.69 | render (b) |
| 19 | 4 | Run 17 | heat_transferred_to_calorimeter_cal15 | 758.5 | render (b) |
| 19 | 4 | Run 17 | heat_lost_in_transfer_cal15 | 15.7 | render (b) |
| 19 | 4 | Run 17 | heat_transferred_with_silica_cal15 | 570.9 | render (b) |
| 19 | 4 | Run 17 | correction_to_25C_cal15 | -.17 | render (b) |
| 19 | 4 | Run 17 | correction_for_vaporization_cal15 | 0.29 | render (b) |
| 19 | 4 | Run 17 | H_t−H_25 | 8,928 | render (b) |
| 19 | 4 | Run 18 | bath_temperature_C | 288.0 | render (b) |
| 19 | 4 | Run 18 | p2o5_weight_g_vac | 3.2269 | render (b) |
| 19 | 4 | Run 18 | silica_weight_g_vac | 10.1655 | render (b) |
| 19 | 4 | Run 18 | final_calorimeter_temperature_C | 25.04 | render (b) |
| 19 | 4 | Run 18 | heat_transferred_to_calorimeter_cal15 | 752.4 | render (b) |
| 19 | 4 | Run 18 | heat_lost_in_transfer_cal15 | 15.7 | render (b) |
| 19 | 4 | Run 18 | heat_transferred_with_silica_cal15 | 570.0 | render (b) |
| 19 | 4 | Run 18 | correction_to_25C_cal15 | .02 | render (b) |
| 19 | 4 | Run 18 | correction_for_vaporization_cal15 | .29 | render (b) |
| 19 | 4 | Run 18 | H_t−H_25 | 8,708 | render (b) |
| 19 | 4 | Run 19 | bath_temperature_C | 335.0 | render (b) |
| 19 | 4 | Run 19 | p2o5_weight_g_vac | 3.2269 | render (b) |
| 19 | 4 | Run 19 | silica_weight_g_vac | 10.1655 | render (b) |
| 19 | 4 | Run 19 | final_calorimeter_temperature_C | 24.23 | render (b) |
| 19 | 4 | Run 19 | heat_transferred_to_calorimeter_cal15 | 886.6 | render (b) |
| 19 | 4 | Run 19 | heat_lost_in_transfer_cal15 | 22.2 | render (b) |
| 19 | 4 | Run 19 | heat_transferred_with_silica_cal15 | 690.1 | render (b) |
| 19 | 4 | Run 19 | correction_to_25C_cal15 | -.42 | render (b) |
| 19 | 4 | Run 19 | correction_for_vaporization_cal15 | 1.49 | render (b) |
| 19 | 4 | Run 19 | H_t−H_25 | 9,543 | render (b) |
| 19 | 4 | Run 20 | bath_temperature_C | 335.0 | render (b) |
| 19 | 4 | Run 20 | p2o5_weight_g_vac | 3.2269 | render (b) |
| 19 | 4 | Run 20 | silica_weight_g_vac | 10.1655 | render (b) |
| 19 | 4 | Run 20 | final_calorimeter_temperature_C | 24.53 | render (b) |
| 19 | 4 | Run 20 | heat_transferred_to_calorimeter_cal15 | 888.0 | render (b) |
| 19 | 4 | Run 20 | heat_lost_in_transfer_cal15 | 22.2 | render (b) |
| 19 | 4 | Run 20 | heat_transferred_with_silica_cal15 | 689.8 | render (b) |
| 19 | 4 | Run 20 | correction_to_25C_cal15 | -.26 | render (b) |
| 19 | 4 | Run 20 | correction_for_vaporization_cal15 | 1.49 | render (b) |
| 19 | 4 | Run 20 | H_t−H_25 | 9,623 | render (b) |
| 23 | 5 | Run 1 | p2o5_weight_g_vac | 1.4351 | render (b) |
| 23 | 5 | Run 1 | p2o5_millimoles_per_1000g_vac_h2o | 6.74 | render (b) |
| 23 | 5 | Run 1 | final_calorimeter_temperature_C | 26.4 | render (b) |
| 23 | 5 | Run 1 | heat_evolved_in_calorimeter_cal15 | 443.5 | render (b) |
| 23 | 5 | Run 1 | mole_fraction_HPO3 | 0.748 | render (b) |
| 23 | 5 | Run 1 | Heat of solution | 43,890 | render (b) |
| 23 | 5 | Run 2 | p2o5_weight_g_vac | 2.3318 | render (b) |
| 23 | 5 | Run 2 | p2o5_millimoles_per_1000g_vac_h2o | 10.94 | render (b) |
| 23 | 5 | Run 2 | final_calorimeter_temperature_C | 26.4 | render (b) |
| 23 | 5 | Run 2 | heat_evolved_in_calorimeter_cal15 | 735.8 | render (b) |
| 23 | 5 | Run 2 | mole_fraction_HPO3 | .823 | render (b) |
| 23 | 5 | Run 2 | Heat of solution | 44,820 | render (b) |
| 23 | 5 | Run 3 | p2o5_weight_g_vac | 9.3891 | render (b) |
| 23 | 5 | Run 3 | p2o5_millimoles_per_1000g_vac_h2o | 43.98 | render (b) |
| 23 | 5 | Run 3 | final_calorimeter_temperature_C | 27.5 | render (b) |
| 23 | 5 | Run 3 | heat_evolved_in_calorimeter_cal15 | 3,040.2 | render (b) |
| 23 | 5 | Run 3 | mole_fraction_HPO3 | .722 | render (b) |
| 23 | 5 | Run 3 | Heat of solution | 45,990 | render (b) |
| 23 | 5 | Run 4 | p2o5_weight_g_vac | 12.2138 | render (b) |
| 23 | 5 | Run 4 | p2o5_millimoles_per_1000g_vac_h2o | 57.10 | render (b) |
| 23 | 5 | Run 4 | final_calorimeter_temperature_C | 28.2 | render (b) |
| 23 | 5 | Run 4 | heat_evolved_in_calorimeter_cal15 | 4,145.8 | render (b) |
| 23 | 5 | Run 4 | mole_fraction_HPO3 | .519 | render (b) |
| 23 | 5 | Run 4 | Heat of solution | 48,210 | render (b) |
| 23 | 5 | Run 5 | p2o5_weight_g_vac | 15.1624 | render (b) |
| 23 | 5 | Run 5 | p2o5_millimoles_per_1000g_vac_h2o | 71.18 | render (b) |
| 23 | 5 | Run 5 | final_calorimeter_temperature_C | 29.0 | render (b) |
| 23 | 5 | Run 5 | heat_evolved_in_calorimeter_cal15 | 5,068.1 | render (b) |
| 23 | 5 | Run 5 | mole_fraction_HPO3 | .567 | render (b) |
| 23 | 5 | Run 5 | Heat of solution | 47,480 | render (b) |

## Equation result cells

| PDF page | Published page | Equation | Printed result | Confirmation |
|---:|---:|---:|---|---|
| 16 | 50 | 17 | 36.8 ± 1.0 cal.15 mole−1 °C−1 | MinerU + pdftotext (a) |
| 19 | 53 | 20 | 24.07 ± 0.38 cal.15 mole−1 °C−1 | render (b) |
| 19 | 53 | 20 | 38.08 ± 0.60 cal.15 mole−1 °C−1 | render (b) |
| 20 | 54 | 15 | 19,900 ± 1,300 cal.15 mole−1 | render (b) |
| 20 | 54 | 19 | 11,070 ± 170 cal.15 mole−1 | render (b) |
| 21 | 55 | 21 | 8,800 ± 1,300 cal.15 mole−1 | render (b) |
| 23 | 57 | 22 | 44,500 ± 800 cal.15 mole−1 | render (b) |
| 23 | 57 | 23 | 48,200 ± 800 cal.15 mole−1 | render (b) |
| 23 | 57 | 24 | 6,200 ± 1,900 cal.15 mole−1 | render (b) |
| 24 | 58 | 25 | 54,400 ± 2,100 cal.15 mole−1 | render (b) |
| 24 | 58 | 26 | 42,000 ± 2,100 cal.15 mole−1 | render (b) |

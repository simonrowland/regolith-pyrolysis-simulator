# Shornikov and Yakovlev (2025), CAI evaporation

Source: `shornikov-2025-ca-al-inclusion-evaporation`, DOI 10.1134/S0016702925600178.

## Extraction boundary

Table 1 is the only table in the paper. It reports compositions attributed to earlier literature, not run-specific bulk analyses of the Efremovka charges. Its four Murchison cells are not repeated because the same source values are in `shornikov-2023-cai-melt-evaporation`; this paper labels them mol %, while that extract preserves the earlier paper's printed wt % label. The difference is recorded without conversion.

The requested CAI KEMS partial-pressure and residue-composition data appear as curves in Figures 4 and 5. The wave rule excludes values read only from figures, so these remain figure-read candidates. Figure 4 plots Ca, Mg, Al, and SiO over type A and B CAI melts. Na, K, and Fe are named in the mass-spectra ion list, but this paper does not give partial-pressure curves or numeric pressure values for them. Figure 5 plots CaO, MgO, Al2O3, and SiO2 against temperature, not weight loss. No weight-loss coordinates are printed in that figure.

The existing `shornikov-2025-ca-al-inclusion-kems` extract holds digitized Ca, Mg, Al, and SiO partial-pressure points from the related 2025 CAI KEMS publication. The 2023 extracts `shornikov-2023-cai-melt-evaporation` and `shornikov-2023-natural-cai-evaporation` record earlier CAI data and figure contexts. The present extract adds no duplicate pressure or plotted-residue rows. Figure 3's Murchison comparison is attributed by this paper to Yakovlev et al. (1987), not to the present experiments.

## Machine-read and render audit

| Table | PDF page | Printed page | Printed body rows | Table tool result | Render check |
|---|---:|---:|---:|---|---|
| 1 | 2 | 552 | 4 | MinerU Table QA numeric agreement 1.000; no numeric mismatches | 600 dpi page render confirms all 32 table cells, row headings, column assignments, and range dashes |

All data cells agree between the MinerU HTML table and pdftotext. The 16 range cells contain en dashes; the page render confirms each is a range separator. The full 32-cell audit is in `shornikov-2025-ca-al-inclusion-evaporation.cell-audit.csv`. Twenty-eight nonduplicate cells are stored as quoted context values. The four Murchison cells are marked as duplicate and omitted.

The MinerU table draft is `p002-t01.csv` in the dispatch scratch tables. The table compare command was run on the assembled extract. It emits only its header because the retained table cells are nested context data, while that command compares numeric observation points. The cell audit above is the verification record for this context table.

## Text-stated facts and evidence classes

The article states that residual-melt MgO decreases to zero at 2000°C and that low silica remains detectable to 2300°C (PDF p. 5, printed p. 555). It separately says the melt contains approximately 20 mol % SiO2 for type A and 16.5 mol % SiO2 for type B when Mg evaporates completely (PDF p. 6, printed p. 556). These are retained as attributed prose summaries of the Figure 5 calculation, not as direct numeric measurement rows. The article also states that O2/O decreases by one order of magnitude between 1800 and 2000°C (PDF p. 9, printed p. 559).

| Figure | PDF page / printed page | Content | Evidence class | Treatment |
|---|---|---|---|---|
| 3 | 4 / 554 | Murchison gas pressures and residue composition | quoted_attributed | Attributed to Yakovlev et al. (1987); no plotted values stored |
| 4 | 5 / 555 | Ca, Mg, Al, and SiO partial pressures over CAIs A and B | measured_reduced | Equation (1) reduces KEMS ion currents; no plotted values stored |
| 5 | 5 / 555 | CaO, MgO, Al2O3, and SiO2 residue composition versus T | model_derived | Paper calls these calculations based on experimental data; no plotted values stored |
| 6 | 7 / 557 | Gehlenite and anorthite end-member activities | model_derived | Figure candidate only |
| 7 | 8 / 558 | Spinel, akermanite, and gehlenite activities and ratios | model_derived | Figure candidate only |
| 8 | 9 / 559 | Oxygen pressure and O2/O | measured_reduced | KEMS-derived quantities; Murchison comparison is attributed; no plotted values stored |

The relation printed as Equation (1) derives partial pressure from measured ion-current ratios and a gold standard, so Figure 4 values would be measured_reduced if later digitized. Figure 5 and Figures 6–7 are calculation outputs. No evidence class is changed to bypass a measurement gate.

## Conditions and migration

The paper states an experiment temperature range of 1000–2400°C, 10–15 mg samples, a tungsten cylindrical cell with a rhenium liner, and Knudsen effusion. It says total vapor pressure over the melt stayed below 10^-4 atm; this is an output bound used for the ideal-gas assumption, not the cell's total-pressure condition. The paper prints no imposed fO2, sweep gas, or run-specific charge composition. Table 1's cited typical compositions are not assigned as experiment conditions.

Isolated migration retains nine context records and zero observation rows: observations 0, hard issues 0, unavailable observation values 0, and one typed-absence queue record (`extract yielded no observations`). The queue record is the migrator's required representation for a zero-observation extract; there are no three numeric quantity observations to bind or print identity axes for. No unknown quantity or phase observation is emitted. The focused test checks the migrated table values and locators on PDF pages 2, 5, and 6. Expected generated-store delta is one source work with nine context records and zero observations; no generated battery store was committed.

Verification: the focused extract test passed (1), the YAML scalar-comma guard passed (17), and 891 of 893 collected cases in `tests/test_literature_extracts.py` passed in bounded selection batches, including `test_repo_extracts_validate_green`. `test_coverage_pending_stubs_not_found` and `test_pilot_extract_count_exact_and_merge_smoke` are UNVERIFIED-local: each exceeded the 299-second local cap.

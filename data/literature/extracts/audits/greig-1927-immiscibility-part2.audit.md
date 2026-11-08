# Greig 1927, Part II: extraction audit

## Scope and source reads

The extract covers the printed FeO-SiO2 work in the early prose and Table VIII; every row of Table IX and Table X; the four binary composition pairs and equilibrium temperatures in the unnumbered summary on physical p. 21; the reported CaO-SiO2 and BaO-SiO2 control bracket for the FeO transition; and numerical B2O3-SiO2 results quoted from earlier work. Printed values are retained as printed. No composition complement, pressure, or oxygen fugacity is supplied where the paper does not print it.

The scan has no usable `pdftotext` text layer. The independent reads were MinerU table HTML/Markdown and the Tesseract OCR text. The full numeric-cell ledger is [greig-1927-immiscibility-part2.cells.csv](greig-1927-immiscibility-part2.cells.csv), with page, printed table or prose locator, cell coordinate, both reads, confirmation method, and destination in the extract. It contains 104 scientific numeric cells: 63 confirmed by matching MinerU and Tesseract digits and 41 checked against page renders. Each table's row/column assignment was checked against its render.

## Table and prose reconciliation

| Physical page | Printed source | Printed body rows | Stored content | Cell reconciliation |
|---|---|---:|---|---|
| 2 | FeO-SiO2 small-charge prose | prose | 40% and 5% FeO outcomes at 1700°; water quench; silica amounts left unstated | MinerU and Tesseract agree; page layout checked on render |
| 3 | Table VIII | 9 | Charge compositions and times; six A/B FeO-rich assays; C charge analysis as context | 18 numeric cells; render corrects C FeO `2.94` (Tesseract `2.04`) and C Fe2O3 `.13` (Tesseract `13`); row assignments checked |
| 4 | FeO-SiO2 prose | prose | 3.7/97 C charge vs Table VIII's 3/97; 5/95 earlier charge; 3/97 and 40/60 tentative limits; FeO:Fe2O3 about 1:1; control bracket 1679°–1698° and 1689° ± 10° estimate | Both OCR reads agree on digits; render restores the ± glyph in `1689° ± 10°` |
| 5 | Table IX | 9 | Nine charge/run-result rows, including approximate run temperatures, microscopy outcomes, and treatment for the PbO glass | 26 numeric cells; row/column assignment checked; render resolves `PbSiO3¹` (MinerU and Tesseract fuse/misread the subscript and footnote) |
| 6 | B2O3-SiO2 prose and cited results | prose | Six Dimbleby compositions and treatments, 41.85% SiO2 analysis, Greig's `>50%` comparison, and Bleininger/Teetor's 28/72 endpoint | Both OCR reads agree; render confirms the located paragraph and values |
| 7 | Table X | 4 | Four B2O3-SiO2 charge, treatment, and outcome rows; `<0.1%` quartz note | 17 numeric cells; the table is rotated and Tesseract's row read is unusable, so every numeric cell was checked against the render |
| 14 | CaO-SiO2 calibration prose | prose | Repeated 1698° ± 5° point and 15CaO, 85SiO2 calibration mixture | Both OCR reads agree; render confirms the locator |
| 21 | Unnumbered summary | 4 | CaO, MgO, SrO, and FeO conjugate-limb compositions and four transition temperatures with widths | 20 numeric cells; Tesseract has digit/layout errors, so all were checked against the render. This is not assigned a printed table number. |

Table VIII prints nine body rows. Its two iron-rich liquid analyses A and B are stored as measured glass compositions with the rapid-cristobalite quench alteration statement. The C assay is retained as a charge analysis because the prose says the charge was almost entirely siliceous phase with sparse iron-rich globules. Table IX and Table X are run-product context rather than liquid-composition points. The Table IX PbSiO3 footnote marker is not part of the subscript. The Table X layout is rotated, so cell assignment follows the render rather than OCR reading order.

The p. 21 summary prints four rows: CaO 0.6/99.4 and 27.5/72.5 at 1698° ± 5°; MgO 0.8/99.2 and 31/70 at 1695° ± 5°; SrO 2.4/97.6 and 30/69 at 1693° ± 5°; FeO 3/97 and 40/60 at 1689° ± 15°. The MgO-rich pair totals 101 wt% as printed and the SrO-rich pair totals 99 wt%; neither was normalized. On p. 4, the prose C charge value 3.7FeO, 97SiO2 is retained separately from Table VIII's printed 3FeO, 97SiO2.

The five printed temperature widths are stored on their observations as symmetric widths with confidence `not_stated`: ±5° for CaO, MgO, and SrO; ±15° for the p. 21 FeO summary; and ±10° for the p. 4 FeO estimate. The last estimate is `measured_reduced`: the authors report it from the 1698° and 1679° complete-crystal-disappearance controls. Its derivation records both control mixtures and both temperatures. No monotectic temperature is printed as such. Figure-only values are deferred: Fig. 27 (physical p. 11), Fig. 28 (p. 12), Fig. 29 (p. 15), and Fig. 30 (p. 20).

## Locator and compare checks

The distinct quantitative locators are p. 2 prose; p. 3 Table VIII; p. 4 prose; p. 5 Table IX; p. 6 prose; p. 7 Table X; p. 14 prose; and the unnumbered summary on p. 21. Every page/table or page/prose locator was checked against its page render. The mapping file maps the two composition tables (Table VIII's A/B cells and the p. 21 inline composition columns). Tables IX and X remain context-only and are fully cell-audited above rather than falsely mapped to a scorable composition quantity.

The `mineru_table_draft.py compare` output is kept in [greig-1927-immiscibility-part2.compare.csv](greig-1927-immiscibility-part2.compare.csv). It reports four mismatches on p. 21 because the compared cell combines a temperature and `±` width, one p. 4 no-match because that value is printed in prose, and no matched values. The p. 21 mismatches were confirmed against the render and the p. 4 prose value is independently present in the prose-cell ledger. The compare helper's numeric-point extraction does not include `component_amount` (`tools/mineru_table_draft.py::_numeric_points`), so the focused migration test independently pins the composition values and their identity conversion.

## Migration, schema, and conditions

The isolated migrator emits 27 observations, 0 unavailable values, and no quantity-unknown or phase-unknown rows. It reports four conditional hard issues and three queue entries, all on the reduced p. 4 FeO estimate: printed control input text does not resolve to observation IDs, `derived_from` is consequently absent, and the evidence-class ancestry remains queued. This is the supported unresolved-lineage case in schema gap t-1563. The BaO-SiO2 control's 15 wt% BaO composition cannot become a valid parent identity through the existing mass-percent composition conversion: `simulator/battery/migrate.py::_COMPOSITION_COMPONENTS` and `simulator/battery/migrate.py::_mass_percent_composition_state` leave it unknown as an unsupported major oxide. The extract retains both controls, their charge compositions, temperatures, and exact relationship in the reduced observation's source fields; no migrator or schema code was changed. The measured reduction is not relabelled as direct or attributed evidence.

All observation source rows carry located `T_C`, composition, pressure, and fO2 condition states. Where no total pressure or fO2 is printed, the source state is explicitly unknown; no ambient pressure, vacuum, or fO2 is assumed. For composition observations, the real migration maps the printed wt% component amount unchanged and maps the printed mass-percent composition into the existing mole-fraction identity. Three migrated rows are pinned for identity composition and Kelvin temperature, with total pressure and fO2 unknown. Transition-temperature observations store temperature as the measured value, so their profile does not duplicate it on `identity.temperature_K`.

Glass-phase observations record the paper's quench statement and page. Less-siliceous p. 21 compositions and Table VIII A/B are marked `asserted_altered`; the silica-rich summary limbs remain `not_stated`. The extract uses the existing `equilibrium_phase_composition`, `transition_temperature`, `wt_percent`, `glass`, and `l` tokens; there is no quantity or phase schema gap.

## Verification and expected store effect

The focused test pins four render values and the row counts, detects a deliberate value mutation, and checks migration identities, conditions, phases, uncertainty, and the four explained hard issues. Required local tests and their results are reported in the worker final message. No battery-store regeneration is committed; the expected store delta is 27 observation rows, 0 unavailable rows, and the documented three lineage queue entries.

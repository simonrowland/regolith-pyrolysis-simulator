# Wang and Seetharaman (2010), Cr oxidation audit

Source: `text/wang-seetharaman-2010-cr-oxidation-original/pdftotext-layout.txt` and the corresponding MinerU content-list HTML/Markdown. Pages below are absolute PDF pages; the MinerU chunk starts at page 2, so its zero-based `page_idx` was offset by 2. The born-digital text layer and MinerU agree on the numeric table cells. MinerU table QA reported 1.000 digit agreement for each of the five tables. Table row/column assignments were checked against rendered pages 2, 4, and 5; Table IV exponent cells were also checked in a 600 dpi crop of page 5.

## Printed tables and stored rows

| Printed table | Page | Printed body rows | Stored numeric cells | Use |
| --- | ---: | ---: | ---: | --- |
| I | 2 | 4 starting mixtures | 12 | Starting-charge wt% composition retained as `initial_charge_only` context for the matching Table III rows. |
| II | 4 | 10 slag samples | 60 | Weighed-in oxide mole fractions retained as `initial_charge_only` context for the matching Table IV rows; `Fe_nO` is preserved as printed. |
| III | 5 | 9 experiments | 36 | Temperature, absolute pO2, basicity, and the printed CrO/CrO1.5 ratio for each XANES row. |
| IV | 5 | 10 samples | 30 | CrO and CrO2 partial pressures and the paper-calculated oxygen partial pressure for each sample. |
| V | 6 | 2 reaction rows | 0 | Thermochemical functions, not measured redox observations; not extracted. |

The cell ledger has 138 unique printed numeric cells. Its row numbers include the two header rows represented in the MinerU drafts. Reused Table I/II cells are listed once by source cell and identify the observation rows that retain them. Table III contains nine XANES observations. Table IV contains 20 measured-reduced gas-pressure observations and 10 Cr(II)/Cr(III) ratios calculated from those pressures with Eq. (7); the calculated ratios are not printed in Table IV.

## Cell audit and compare resolution

See `wang-seetharaman-2010-cr-oxidation-original.cells.csv` for each stored source cell, its page/table/row/column, the stored field, and confirmation route. The printed table labels are used in extract locators; draft table indices 3 and 4 are only tool inputs for Tables III and IV.

The compare output contains 20 matches, 9 mismatches, and 10 unverifiable values. All 20 Table IV CrO and CrO2 pressure cells matched their row and column cells. The nine Table III ratio rows are reported as mismatches because the compare helper's phase-column filter supports gas/liquid/solid but rejects the correct `glass` observation phase. Their exact column 5 row values were checked against both machine reads and the rendered Table III: 08D row 3 = 1.38; 10B row 4 = 5.25; 10C row 5 = 9.00; 12A row 6 = 1.56; 12D row 7 = 4.26; 21A row 8 = 1.50; 21B row 9 = 3.76; 21C row 10 = 4.26; 21D row 11 = 1.78. No compare-helper or simulator code was changed under the extraction-only scope.

The ten Eq. (7) ratios are `unverifiable` in compare because their values are derived rather than printed cells. Each has exactly two stored parents: the same sample's CrO and CrO2 pressure observations. The printed relation is `XCrO/XCrO1.5 = [p(CrO) − 2p(CrO2)]/[2p(CrO2)]`. The isolated migration test pins sample 1 to 1.42 and verifies the exact parent IDs.

## Conditions, phase, and evidence

Table III's ratios are Cr(II)/Cr(III) valence ratios (`valence_basis`: numerator Cr(II), denominator Cr(III), printed ratio form), reduced from the authors' XANES end-member mixture fits. The measured specimens are quenched glass. Page 3 states that samples were quenched with argon and solidified as glass; it does not say whether Cr valence was preserved or altered, nor state an oxidation direction. The extract records `quench_state: not_stated` and does not assign a typed quench direction. No room-temperature condition is added.

Table IV's pressure rows are gas-phase CrO and CrO2 partial pressures measured by Knudsen-cell mass spectrometry and reduced from ion-current ratios with Eq. (6). The ten Eq. (7) valence ratios are `measured_reduced`, with only their two stored pressure observations as parents. These Table IV ratios describe the high-temperature liquid melt (`phase: l`) at the caption's 2000 K. Its oxygen-pressure column is the paper's calculated pO2 from Eq. (10), including tungsten-crucible interactions; it is retained as printed and labeled as calculated, not imposed or measured. The source reports only qualitative “extreme vacuum,” so no numeric total pressure is recorded.

Table I and II compositions are initial charges, not measured equilibrated-melt compositions. The source reports chromium dissolving into the Pt crucible for the Table III series and iron oxides evaporating before chromium-bearing vapor was detected in the Table IV series. The extract retains these printed compositions in structured `point_conditions.composition` records with `proxy_flag: initial_charge_only`. The migrator leaves the actual melt composition unknown in both identity and migrated point conditions through `simulator/battery/migrate.py::_initial_charge_only_missing_composition`. No condition is promoted from an unrelated table row.

Table III row 08D prints pO2 = 2.83 × 10^-3 Pa, while the abstract states a study range of 10^-4 to 10^-5 Pa. The table cell is retained as printed and the discrepancy is noted on that row. Table IV's temperature comes from its caption. No numeric total pressure, per-run Ar-CO-CO2 ratio, or current melt composition is invented.

## Figure-only candidates

No plotted values were digitized. Figures 3–9 are figure-read candidates: XANES spectra (3), molecular vapor partial pressures over sample 2 (4), literature ratios replotted against basicity (5), oxygen-pressure effect (6), temperature effect (7), basicity effect (8), and experimental versus estimated ratios (9). Table IV's numeric pressure cells are extracted from the table, not Figure 4.

## Verification

The focused test isolated the real migrator: 39 observations, 0 hard issues, 0 unavailable values, and no quantity-unknown or phase-unknown observations. It asserts that required temperatures and printed pO2 values reach identity; total pressure stays absent; initial-charge composition stays out of identity; the Table III rows migrate as glass with quench preservation `not_stated`; and Table IV rows migrate as gas/liquid with Eq. (7) parent lineage. It also pins rendered values and row counts independently of the compare CSV.

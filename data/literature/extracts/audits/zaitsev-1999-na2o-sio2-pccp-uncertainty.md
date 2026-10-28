# Zaitsev et al. (1999): activity uncertainty audit

## Source and scope

The existing extract contains 102 numeric activity observations: Table 1 has 29 Na2O and 29 SiO2 experimental values; Table 4 has 44 Na2O activity values (19 Present, 3 Present model extrapolations, and 22 Ref. 16 values). The isolated migration returns 116 observations total, with 0 hard issues and 0 unavailable values. No activity values were changed.

The born-digital `pdftotext-layout.txt` is the exact machine text available for this source. The MinerU content-list directory was absent; `mineru_table_draft.py convert` found no content-list JSON and the table QA tool returned no table rows. The Table 1 row and column assignments were checked against the 600 dpi render of PDF page 3 (printed p. 1901); the Table 4 cells and column assignments were checked against PDF page 6 (printed p. 1904). The existing focused test pins rendered Table 4 cells and Table 1 endpoints; the updated test also checks migrated values and locators at both tables.

## Printed uncertainty

Table 1 compares experimental component activities with the associated-solution model. On PDF page 5 (printed p. 1903), the authors state: “The differences between the two-type values do not exceed the experimental error (1–3%).” This table-level experimental-error range applies to Table 1's experimental `exp.` cells, not its `model` cells. The extraction attaches the upper endpoint, 3%, to each of the two experimental series as a conservative printed width; the confidence level is not stated. The exact sentence and its page/table locator remain in each series' `uncertainty` mapping.

The separate statement on PDF page 4 (printed p. 1902) that the model approximates the experimental data with a precision “not worse than the experimental one (2–3%)” describes fit precision. It is not attached as a measurement uncertainty. Table 4 prints no per-cell, column, or table-wide uncertainty for its 44 activity values. Its values therefore retain absent uncertainty. The general statement on PDF page 3 (printed p. 1900) that divergence “never exceeded a few per cent” is not quantified and is not used as a numeric uncertainty.

`simulator/battery/migrate.py::uncertainty_for` reads the source `uncertainty` field. `simulator/battery/score.py::_printed_uncertainty_band` parses its printed width and `simulator/battery/score.py::decision_band_for` applies that width to the residual metric.

| Printed table / series | Numeric activity points | Uncertainty recovery | Locator |
| --- | ---: | --- | --- |
| Table 1, experimental Na2O, rows 1–29 | 29 | 3% upper endpoint of the printed table-level 1–3% experimental error | PDF p. 5, printed p. 1903, Table 1 |
| Table 1, experimental SiO2, rows 1–29 | 29 | 3% upper endpoint of the printed table-level 1–3% experimental error | PDF p. 5, printed p. 1903, Table 1 |
| Table 4, Present | 19 | Not printed; absent | PDF p. 6, printed p. 1904, Table 4 |
| Table 4, Present model extrapolation | 3 | Not printed; absent | PDF p. 6, printed p. 1904, Table 4 |
| Table 4, Ref. 16 | 22 | Not printed in this paper; absent | PDF p. 6, printed p. 1904, Table 4 |
| **Total** | **102** | **58 receive printed experimental uncertainty; 44 remain absent** | |

The row counts and inherited uncertainty scope are asserted after isolated migration in `tests/test_zaitsev_1999_na2o_sio2_pccp.py`. All 58 Table 1 observations migrate with printed uncertainty; all 44 Table 4 observations migrate with no uncertainty. No new hard validation issues, unavailable values, quantity-unknown observations, or phase-unknown observations were introduced.

## Migrated identity axes

Three Table 1 Na2O observations were inspected after isolated migration. Their printed temperature and binary composition reach `Identity`; fO2 and total pressure remain unknown because the source does not print them for these table rows.

| Table 1 row | Identity temperature | Identity composition (mole fraction) | Identity fO2 | Identity total pressure |
| ---: | ---: | --- | --- | --- |
| 1 | 1473 K | Na2O 0.195; SiO2 0.805 | unknown, not printed | unknown, not printed |
| 2 | 1573 K | Na2O 0.195; SiO2 0.805 | unknown, not printed | unknown, not printed |
| 3 | 1273 K | Na2O 0.247; SiO2 0.753 | unknown, not printed | unknown, not printed |

## Ten-row score comparison

The ten scored references are Table 1 experimental Na2O rows 1–10. The declared OpenIMCC 66ec4a3 Python-source tree hash was `308672269d6e6090023f4a86c4fc0d83c4c24688`. Without the extracted uncertainty, all ten residuals had no decision band. With it, the uncertainty parser returns a 0.013228265733755148… dex band for each row, and `metric_uncertainty` is populated. The final scorer still strips the decision band: these temperatures are below OpenIMCC's 1700 K certified lower bound, and the scorer also reports unverified apparatus / unknown method. The residual values do not change. Since every absolute residual exceeds the parsed width, every row would be outside that band if the scorer admitted it; the final scored verdict remains `NO_BAND`.

| Table 1 row | Residual (dex) | Before: final band / status | After: parsed width | After: final band / status | Parsed-width comparison |
| ---: | ---: | --- | ---: | --- | --- |
| 1 | -0.1506588789959905 | none / NO_BAND | 0.0132282657 dex | none / NO_BAND | outside |
| 2 | -0.062061337819317525 | none / NO_BAND | 0.0132282657 dex | none / NO_BAND | outside |
| 3 | -0.25399178895108715 | none / NO_BAND | 0.0132282657 dex | none / NO_BAND | outside |
| 4 | -0.06911999455382294 | none / NO_BAND | 0.0132282657 dex | none / NO_BAND | outside |
| 5 | -0.15186714085485986 | none / NO_BAND | 0.0132282657 dex | none / NO_BAND | outside |
| 6 | -0.1923019222130582 | none / NO_BAND | 0.0132282657 dex | none / NO_BAND | outside |
| 7 | -0.026185642683864304 | none / NO_BAND | 0.0132282657 dex | none / NO_BAND | outside |
| 8 | 0.10749199009484237 | none / NO_BAND | 0.0132282657 dex | none / NO_BAND | outside |
| 9 | -0.23284277466330064 | none / NO_BAND | 0.0132282657 dex | none / NO_BAND | outside |
| 10 | -0.040996046044244956 | none / NO_BAND | 0.0132282657 dex | none / NO_BAND | outside |

| Band count | Before uncertainty recovery | After uncertainty recovery |
| --- | ---: | ---: |
| Table 1 points with a parsed printed band | 0 / 58 | 58 / 58 |
| Table 4 points with a printed band | 0 / 44 | 0 / 44 |
| Ten scored rows with a final decision band | 0 / 10 | 0 / 10 |

Making final scored bands appear for these rows would require changing qualification behavior for values below the selected engine's certified temperature range, beyond this extraction-only scope. No scorer tuning or code changes were made.

# Gail et al. (2013), SiO nucleation and vapour pressure: extraction audit

## Scope and source reads

The extraction uses the paper's born-digital machine text and MinerU chunk `p002-p011`. The PDF is used only as a read-only backup, rendered for equation coefficients, exponent and uncertainty notation, and Table 1 row/column assignments. The paper's measured SiO vaporisation-coefficient fit (equation 3) and equilibrium vapour-pressure fit (equation 4) are stored as `measured_reduced`; their numerical Figure 2 and Figure 3 points have `figure_only` evidence and were not digitized. The paper's re-evaluated Nuth & Donn points are in Figures 5 and 6 and were not digitized. Figures 4, 7, 8, and 9 contain calculated stability/wind results and comparison data; none were digitized. These remain figure-read candidates.

The authors identify the vapour-pressure experiment as the Knudsen method: cell mass loss measured with a quartz crystal microbalance, cell heated by an electron-beam evaporator, and two orifice diameters of 0.50 mm and 0.79 mm. Equation (3) is their Arrhenius fit to the measured vaporisation coefficient; equation (4) is their fit to the measured equilibrium vapour-pressure data after reduction through equations (1)–(3). Both are `measured_reduced`, with located `raw_inputs: not_printed` declarations because the numeric points are only plotted in Figures 2 and 3. The two fits retain the printed temperature domain 1275–1525 K. The coefficient uncertainties are preserved as printed; confidence level is not stated. The equation (4) source row declares an unknown reservoir and records that the paper prints solid silicon monoxide, while `Phase.CR` would assert crystal. The production migrator currently replaces this reason with its generic missing-reservoir reason for a `p_sat` row; the required source-specific migrated reason needs an existing migrator path that accepts the `reservoir` field for this quantity. Detailed run conditions and sample information are attributed by the paper to Wetzel et al. (2012a), and no unprinted pressure, temperature, or composition is supplied here.

For comparison, equation (6) reports the Ferguson & Nuth (2008) vapour-pressure coefficients `T_v = 40 850 ± 1 270 K` and `S_v = 32.93 ± 0.90`; equation (7) quotes Schick's `pvap = exp(26.66 − 25670/T)`. Both are stored as `quoted_attributed` relations in dyn cm−2. The comparison equations are not recast as Gail et al.'s measurements.

The Nuth & Donn prose gives a smoke-onset criterion (1% extinction at 200 nm) and a crude onset-rate interval of `10^8` to `10^10` particles cm−3 s−1 (absolute PDF page 4, section 4.1). The paper separately states `J_av = 10^(9±1)` particles cm−3 s−1 for the Nuth & Donn experiments (page 5, section 4.1). Both are retained as separate quoted rows because they are two distinct printed statements; neither has a prose temperature or a single total pressure. The fixed-rate notation's ±1 is in the exponent. Neither row is converted to invented point values.

Equation (16) is Gail et al.'s empirical fit to re-evaluated Nuth & Donn data. It is `quoted_attributed`, not `model_derived`, because its numeric fit inputs are only plotted in Figures 5 and 6 and are not stored. Equation (15)'s coefficients, equation (16)'s exponent constant, the formula, and their printed uncertainties are retained verbatim. The caption to Figure 5 states two input series at `pH2 = 50 Torr` (black dots) and `pH2 = 35 Torr` (crosses); these are preserved as fit-series provenance and are not promoted to a single point condition. No numeric plotted points were extracted.

Figure 2 and Figure 3 each have a `figure_only` observation record with its printed caption and deferred digitisation status; neither record contains digitized coordinates. Figure 4 is also not digitized. Table 1 contains 13 body rows across eight model columns. All 13 rows and all 104 numeric body cells are transcribed as printed in `context`; the three printed powers of ten in the row units are retained with those units. The authors describe the table as stationary dust-driven wind model results based on equation (16), so its context record uses `method_class: model_derived`. The mixed table has no single exact observation quantity or observation type and is kept out of the scored observation list. The p(rc) model-output row is not an experimental total-pressure condition. Printed percentages and scale factors are not rescaled.

The support-file directory for this source was checked and contained no listed files.

## Schema notes

`rate_series` is a closed observation type, but it defaults to `Quantity.MASS_LOSS_RATE` in `simulator/battery/migrate.py::TYPE_QUANTITY`. `Quantity.EVAPORATION_RATE` and `Quantity.MASS_LOSS_RATE` do not mean nucleation rate in particles cm−3 s−1, and `simulator/battery/migrate.py::QUANTITY_ALIASES` has no exact nucleation-rate token. The three rate observations therefore use explicit descriptive non-token quantities and migrate as quantity-unknown unavailable values. The focused test verifies that all three rate rows remain quantity-unknown, preventing inference of `mass_loss_rate`. The related existing extract `arxiv-2509-sio-flame-nucleation.yaml` also uses descriptive nucleation-rate labels for its model equations; it does not supply a distinct store token.

The closed `tools/validate_literature_extracts.py::OBSERVATION_TYPES` has no mixed wind-model output type, and `simulator/battery/enums.py::Quantity` has no exact quantity for a table combining stellar parameters, sonic-point conditions/results, condensation fractions, terminal velocity, and optical depths. Table 1 is retained as one model-output context record, following the existing context pattern for non-scoreable model parameters and table data. Its `derived_from` link names the stored equation (16) row cited in the caption; the paper itself is not a derivation input.

## Table 1 full-cell audit

For the table body, (a) means MinerU and pdftotext read the same digits in every listed cell. The page 7 render was checked once for the row/column layout and confirmed the numeric assignments shown below. The numeric agreement pre-screen reported 1.000 across the 107 numeric cells. The 104 body values below are all preserved; the other three numeric cells are the powers of ten in the printed unit cells for Mass-loss, Luminosity, and Pressure.

| PDF page / table row | Printed quantity and unit | Values across model columns 3–10 | Audit |
| --- | --- | --- | --- |
| 7 / 1, row 1 | Mass-loss; 10−5 M⊙ yr−1 | 1, 2, 3, 5, 10, 20, 30, 50 | (a), plus rendered column assignment |
| 7 / 1, row 2 | Mass; M⊙ | 1, 1, 1, 1, 1, 1, 1, 1 | (a), plus rendered column assignment |
| 7 / 1, row 3 | Luminosity; 10³ L⊙ | 5, 5, 5, 5, 5, 10, 10, 10 | (a), plus rendered column assignment |
| 7 / 1, row 4 | Temperature; K | 2700, 2700, 2700, 2700, 2700, 2700, 2700, 2700 | (a), plus rendered column assignment |
| 7 / 1, row 5 | Radius; R⊙ | 323, 323, 323, 323, 323, 456, 456, 456 | (a), plus rendered column assignment |
| 7 / 1, row 6 | Position; R* | 9.21, 7.98, 7.50, 7.05, 6.60, 6.42, 6.20, 5.93 | (a), plus rendered column assignment |
| 7 / 1, row 7 | Temperature; K | 629.1, 675.9, 697.1, 719.0, 743.2, 753.4, 766.9, 783.7 | (a), plus rendered column assignment |
| 7 / 1, row 8 | Pressure; 10−10 bar | 1.75, 4.85, 8.35, 16.0, 37.1, 39.5, 64.2, 118 | (a), plus rendered column assignment |
| 7 / 1, row 9 | Degree of condensation; % | 13.4, 14.06, 14.5, 15.4, 17.6, 6.37, 6.15, 6.77 | (a), plus rendered column assignment |
| 7 / 1, row 10 | Terminal velocity; km s−1 | 4.42, 5.42, 6.08, 7.01, 8.67, 10.71, 11.81, 13.10 | (a), plus rendered column assignment |
| 7 / 1, row 11 | Terminal condensation; % | 19.4, 26.9, 33.9, 47.2, 71.4, 62.7, 75.8, 88.3 | (a), plus rendered column assignment |
| 7 / 1, row 12 | Rosseland optical depth; blank unit cell | 0.36, 0.75, 1.03, 2.37, 6.21, 5.26, 9.31, 18.02 | (a), plus rendered column assignment |
| 7 / 1, row 13 | Visual optical depth; blank unit cell | 0.97, 2.01, 3.26, 6.39, 16.75, 14.19, 25.10, 48.60 | (a), plus rendered column assignment |

There are 13 printed body rows × 8 model columns = 104 numeric body cells. Adding the three numeric powers in the printed unit cells gives 107 numeric cells. Table row labels were checked against the rendered first column; the two optical-depth unit cells are blank in print.

## Equation and prose cell audit

| PDF page / locator | Stored numeric content | Machine-read and render check |
| --- | --- | --- |
| 3 / equation 3 | `α(T) = α0 exp(−T0/T)`; `α0 = 0.521 ± 0.156`; `T0 = 3 686 ± 428`; domain 1275–1525 K | MinerU and pdftotext agree on the coefficients; page 3 render confirms coefficient grouping, ± signs, and equation form (b). |
| 3 / equation 4 | `T_v = 49 520 ± 1 400 K`; `S_v = 32.52 ± 0.97`; equation `P = exp(−T_v/T + S_v)` | MinerU and pdftotext agree on digits and signs; page 3 render confirmed coefficient grouping, ± signs, and equation form (b). |
| 4 / equation 6 | `T_v = 40 850 ± 1 270 K`; `S_v = 32.93 ± 0.90`; dyn cm−2 | MinerU and pdftotext agree on digits; page 4 render confirms coefficient grouping, ± signs, and units (b). |
| 4 / equation 7 | `pvap = exp(26.66 − 25670/T)`; dyn cm−2 | MinerU and pdftotext agree on digits and sign; page 4 render confirms the exponent, minus sign, and units (b). |
| 4 / section 4.1 | smoke onset at 1% extinction for a 200 nm beam; rate interval `10^8`–`10^10` particles cm−3 s−1 | MinerU/pdftotext exponent formatting is ambiguous in linear text; page 4 render confirms both powers and their limits (b). |
| 5 / section 4.1 | `J_av = 10^(9±1)` particles cm−3 s−1 | MinerU/pdftotext do not preserve the exponent grouping identically; page 5 600 dpi render confirms ±1 belongs inside the exponent (b). |
| 5 / equation 15 | `a = (4.40 ± 0.61) × 10^12`; `b = 22.05 ± 3.14` | MinerU and pdftotext agree on the digits; page 5 render confirms signs, decimal places, and exponent (b). |
| 5 / equation 16 | `J = n1^2 exp[(1.33 ± 3.1) − ((4.40 ± 0.61) × 10^12)/(T^3 (ln S)^2)]` | MinerU and pdftotext agree on the numeric terms; page 5 render confirms signs, grouping, and formula layout (b). |

## Migration and compare disposition

The isolated migrator emitted nine observation records, zero hard issues, and nine unavailable values: two measured fit records, two undigitized figure records, two quoted comparison vapour-pressure relations, and three nucleation-rate typed refusals. The three rate rows have quantity unknown. All phases map to gas. Every non-figure row carries unknown temperature and fO2 point conditions with locators. Unprinted total-pressure conditions are removed by the migrator; the p_sat identities have no numeric total-pressure condition, and the rate rows have no quantity-specific identity axes. Table 1 migrates as one context record and contributes no Observation.

The identity-axis check printed three representative rows after migration:

| Observation | Quantity state | Reservoir | Composition | Total pressure (Pa) | fO2 (Pa) | Temperature (K) |
| --- | --- | --- | --- | --- | --- | --- |
| `gail_2013_sio_vapor_pressure_fit_eq4` | `p_sat` | unknown: `no reservoir mapped from source` | not applicable | not applicable | not applicable | unknown |
| `gail_2013_nuth_donn_smoke_onset_rate_range` | unknown | not materialized | not materialized | not materialized | not materialized | not materialized |
| `gail_2013_sio_nucleation_rate_fit_eq16` | unknown | not materialized | not materialized | not materialized | not materialized | not materialized |

For the two quantity-unknown rows, the migrator retains `point_conditions.temperature_K` and `point_conditions.fO2_Pa` as unknown, while dropping unprinted total pressure. No numeric identity value is asserted for either row.

The extract's located `values.reservoir` unknown for equation (4) names the printed solid silicon monoxide and explains why `Phase.CR` would assert crystal. However, `simulator/battery/migrate.py::_pressure_reservoir_from_payload` is only called by `_partial_pressure_identity_fields`, and the normal observation path calls that helper only for `Quantity.P_PARTIAL`. This row is `Quantity.P_SAT`, so migration returns the fallback reason `no reservoir mapped from source`. The review requires that source-specific reason in the migrated identity; that requirement cannot be completed without adding a `p_sat` reservoir mapping path to the migrator, which the task forbids editing. The focused test asserts the required result and therefore fails on this verified gap.

`mineru_table_draft.py compare` returned `matched=0`, `no-match=0`, `mismatch=0`, `presence-only=0`, `needs_mapping=0`, `locator-error=0`, `unverifiable=0`, `total=0`. This mixed model-output table is carried under `context`, so there are no Table 1 observation rows for compare to match. The output is in `gail-2013-sio-nucleation-silicate-dust.compare.csv`; its absence of mapped Table 1 observations is intentional and resolved by this row-by-row context audit above, not by dropping any table values.

## Verification and unresolved requirement

The focused source test validates the extract, pins the equation (3) and (4) measured-reduced classes and located `not_printed` lineage in the migrated records, pins both comparison rows as quoted `p_sat`, and verifies nine observations with zero hard issues. It fails at the required migrated reservoir-reason assertion: the identity is unknown, but its reason is `no reservoir mapped from source` instead of preserving the printed-solid explanation. The source test therefore remains red until the migrator supports reading `reservoir` for a `p_sat` row.

The task named `tests/battery/test_literature_index.py`, which is absent from this checkout. The checked-in counterpart `tests/test_literature_index.py` was run in the foreground with a 590-second cap; it emitted one failure marker and hit the cap before printing a summary. Its result is UNVERIFIED-local (failure observed; no final diagnostic available).

No generated store files are committed. A later store regeneration should add five observations for equation (3), the undigitized Figure 2 and Figure 3 records, and comparison equations (6) and (7); the existing equation (4), three rate rows, and one Table 1 context record remain.

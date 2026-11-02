# Sanders and Haller (1977) extraction audit

Source: Sanders and Haller, “Effect of Water Vapor on Sodium Vaporization from Two Silica-Based Glasses,” *Journal of the American Ceramic Society* 60(3–4), 138–141. The text layer and MinerU table draft were read first. PDF pages 1–3 were rendered only to resolve table layout, QA flags, and the Eq. (13) and footnote locators.

## Table I cell audit

Table I (PDF page 2, printed page 139) contains ten measurement pairs: four for the ternary 74% SiO2 + 16% Na2O + 10% CaO glass and six for the binary 75% SiO2 + 25% Na2O glass. The vapor-density column identifies Na vapor density, rho_t, and Eq. (12) defines it as the sum of rho_NaOH and rho_Na. The table also prints the H2O partial pressure for each pair, grouped temperatures, grouped oxygen pressure, both compositions, and five fit cells for each glass.

The companion `sanders-1977-water-vapour-na-vaporization.cells.csv` records all 39 printed numeric cells in Table I by PDF page, printed table, grid row, and column. Every cell is method (b), confirmed against the page 2 render. It includes the ten H2O pressure cells, ten rho_t cells, five composition cells, two temperatures, two P_O2 cells, and ten fit cells. The printed table caption's ten measurement pairs are counted separately from the fit cells.

MinerU table QA reported numeric agreement 0.943, six numeric-mismatch flags, and 33 LaTeX flags. Its draft classifies the first composition and K2/K4 cells as header rows, and it does not classify the parameter column as numeric. The table layout and all cells were checked against the render; the rendered row and column assignments agree with the extract. The QA summary is recorded in the mapping file.

The committed compare output is in `sanders-1977-water-vapour-na-vaporization.compare.txt`. It reports ten density and two b comparisons as mismatches, and ten Eq. (13) values as unverifiable because their locators are equations rather than Table I cells. For the density rows, the comparison helper does not accept the extract's required phase token `g` in its gas/liquid/solid column filter; for b, the cells are labeled LaTeX expressions in a column the draft marks nonnumeric. These are comparison-tool limitations, not unresolved source differences: each of the 12 reported Table I values is explicitly transcribed in the cell audit and agrees with the page 2 render. Eq. (13) and the attributed pressure-range footnote were checked against the page 3 render.

The `*` in the printed “Parameters*” header applies to every K2, K4, b, m, and r cell. The marker is retained on each parameter record. The table footnote calls m an intercept, while Eq. (1) and the caption define it as the coefficient on sqrt(P_H2O); both statements are retained without reconciling the source's wording.

## Numeric rows and lineage

- Ten rho_t measurements are stored as `measured_direct`, with the printed `g/STP ml` notation and canonical `g/cm3` unit. Their standard-volume definition (mass of Na in a milliliter of ideal gas at 273.15 K and 1 atm) is retained separately from the experiment total pressure.
- Ten Eq. (13) P_t values were calculated by `sanders-1977-water-vapour-na-vaporization-derive.py` using Decimal precision 50 and the printed relation `P_t = 82.06 × 273.15 × rho_t / 22.99 atm`. Each row has exactly its corresponding Table I rho_t row as its parent. This is a `measured_reduced` result from the authors' printed reduction of their measurement. The output is the total pressure of all species containing Na, as printed; no species-specific Na or NaOH pressure is inferred from it.
- The two b intercepts are `model_derived` density values with parents from all printed rho_t rows in the corresponding series. The least-squares relation and the printed coefficients are recorded.
- K2 and K4 are the four printed linear equilibrium-constant values. Their units are unstated and their numerical inputs are not tabulated, so the rows are retained as `author_derived` typed refusals. m and r are retained as `model_derived` fit values with the printed input rows as parents, but typed refusals because no exact quantity token represents a pressure-dependent slope or correlation coefficient.
- The page 3 footnote quotes `P_NaOH` “in the range 10^-5 atm,” attributed to the JANAF Thermochemical Tables (Ref. 11) and Ref. 12. It gives no endpoints or temperature, so it remains a `quoted_attributed` typed refusal with those conditions unknown.

Eq. (13) has no exact store quantity for total pressure across multiple Na-bearing vapor species. The extract leaves the descriptive quantity `total_pressure_of_Na_bearing_vapor_species` unknown and typed-refused. `simulator/battery/enums.py::Quantity.P_PARTIAL` is species-specific, `simulator/battery/migrate.py::QUANTITY_ALIASES` maps `partial_pressure` to `p_partial`, and `simulator/battery/migrate.py::TYPE_QUANTITY` has no aggregate-pressure quantity. `tools/validate_literature_extracts.py::OBSERVATION_TYPES` admits `partial_pressure` as an observation type but does not define an aggregate-pressure quantity. No near-match quantity was used.

The other typed schema gaps are the four linear K2/K4 values, the two slope values m, and the two correlation coefficients r. Their exact quantities are not represented by `simulator/battery/enums.py::Quantity`; the linear constants also cannot use `simulator/battery/enums.py::Quantity.LOG10_K_REACTION` without changing their printed form. The focused migration test asserts that the quantity-unknown IDs are exactly these eight rows plus the ten Eq. (13) rows.

The Eq. (13) factor values 82.06, 273.15, and 22.99 are printed at equation 13 on PDF page 3 and are preserved with equation locators in the derivation records. The procedure's 350 ± 100 ppm H2O in dry gas, ±0.1°C saturation-system control, “at least 4” measurement count, and 3-standard-deviation uncertainty statement are retained in the page 1 context record; the table does not print error-bar widths.

## Conditions, standard states, and omitted figure reads

The paper states an experiment total pressure of 1 atm throughout, converted by the migrator to 101325 Pa. Table I prints 1570 K and 1332 K for the two groups, the row-wise H2O partial pressures in atm, and P_O2 = 2 × 10^-4 atm for the rows covered by the merged cells. The procedure also states 200 ± 50 ppm O2 throughout. Nitrogen and argon are both named as carrier gases, but the paper does not assign either gas to the rows; the row-level carrier gas stays unknown. The source reports P_O2 and oxygen concentration, not fO2, so fO2 stays unknown.

The Table I composition and condition assignments use the printed group rows and are propagated to each associated observation with locators. The test pins migrated temperature, total pressure, composition, oxygen conditions, optional P_O2 cells, and the unknown fO2/carrier conditions. The identity values for composition, total pressure, and temperature are also checked on migrated density rows.

The experiment measures gas-phase vapor above a glass melt; the gas observations use phase `g`. Equations (4) and (9) define the activity standard state as pure liquid Na2O at the same temperature. The printed equations, standard-state wording, transpiration method, and species assumptions are retained in context.

Figures 2 and 3 are listed as figure-read candidates only. The plotted activity, rho_t trend, P_Na, P_NaOH, and their sum are not digitized. No numeric flux/vaporization rate or ion intensity is reported in the tabulated text, and the paper reports no potassium-bearing vapor species. The measured Na/NaOH vapor results are the sodium hit for the openimcc-species lane; there is no K hit.

## Migration and tests

The focused test migrates all 31 observation rows: 12 numeric points (ten measured rho_t values and two b intercepts) and 19 unavailable values (eight schema-gap fit values, ten Eq. (13) P_t values, and the quoted P_NaOH range). The ten full-precision Eq. (13) values and their lineage remain in the extract but migrate as unavailable typed refusals because the schema has no aggregate Na-bearing vapor-pressure quantity. The focused test pins those raw outputs and every condition/locator, then mutates an interior Table I density cell and requires the full set of value and condition assertions to fail.

Isolated migration identity axes (composition, total pressure, fO2, temperature):

| Observation | Composition (mole fraction) | Total pressure (Pa) | fO2 | Temperature (K) |
|---|---|---:|---|---:|
| `sanders_1977_table1_ternary_density_01` | SiO2 0.738338485528496350450492919; Na2O 0.1547583344665764421510426487; CaO 0.1069031800049272073984644325 | 101325 | not applicable (density profile does not use fO2) | 1570 |
| `sanders_1977_table1_binary_density_01` | SiO2 0.7557786436854403282636025429; Na2O 0.2442213563145596717363974569 | 101325 | not applicable (density profile does not use fO2) | 1332 |
| `sanders_1977_table1_ternary_b` | SiO2 0.738338485528496350450492919; Na2O 0.1547583344665764421510426487; CaO 0.1069031800049272073984644325 | 101325 | not applicable (density profile does not use fO2) | 1570 |

Expected observations-v2/store delta: 31 observations; 12 numeric points and 19 unavailable values; no store regeneration was performed.

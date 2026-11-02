# Rego, Sigworth, and Philbrook (1985) extraction audit

## Table I census and cell audit

Printed Table I on PDF page 4 (published page 316) contains 31 experimental runs: runs 1–15 at 1300 °C and 16–31 at 1400 °C. Each run has six printed fields: run number, Na₂O mol%, CO flow (mL/min), total pressure (atm), Na mass in leached solution (mg), and collected CO amount. The two grouped temperature headings are part of the row assignment. The experiment is binary Na₂O–SiO₂; the row-specific SiO₂ amount is retained as the binary remainder of the printed Na₂O mol%.

The cell audit has 189 rows: 186 body cells (31 × 6), two grouped temperature headings, and the numeric CO scale exponent. The `tools/mineru_table_qa.py` pre-screen reports 0.980 numeric agreement, six comma/dot flags, six multiple-number flags, and four numeric mismatches. The layout and all 31 row assignments were checked against the page 4 render. For 168 body cells, MinerU and pdftotext agree in digits; 18 body cells were resolved from the render. The two temperature headings and the multiplier exponent were also render-checked. The corrected cells, raw machine reads, and cell-by-cell method (`a` or `b`) are in `rego-1985-na2o-sio2-transpiration-cells.csv`.

The four numeric mismatches reported by the QA pre-screen are run 6 column 2 (`30.94`), run 11 column 2 (`37.11`), run 22 column 3 (`12.9`), and run 23 column 3 (`15.7`); the page 4 render verifies each. The full 18 body-cell render checks are runs 6 and 8 (Na₂O mol%); run 10 (Na₂O mol%, CO flow, and mg Na); run 11 (Na₂O mol%); run 16 (mg Na and collected CO); run 17 (mg Na); run 18 (Na₂O mol% and collected CO); run 19 (Na₂O mol%); run 20 (Na₂O mol% and mg Na); runs 22–23 (CO flow); and runs 24–25 (collected CO). In those cells the text layer used commas, tildes, split digits, or `I`/`l` glyphs where the rendered printed value is decimal punctuation or a digit. The CSV records each raw read and corrected value separately.

The sixth-column heading is printed as `Moles CO Collected (× 10²)`. MinerU reads `× 10²`, pdftotext reads `(• 102)`, and the 600 dpi render confirms superscript 2 with no minus sign. The column reports the mole amount multiplied by 100, so Eq. [4] uses each printed cell multiplied by 10⁻² mol. No Table I uncertainty is printed.

## Stored rows, reductions, and fit lineage

Each of the 31 Table I runs is represented by three observations linked to one per-run experiment: a typed-refused bundle preserving all six printed input cells, a Na partial pressure reduced with Eq. [4], and a Na₂O activity reduced with Eq. [6]. Neither the per-run pNa nor activity is printed as a table value; the activity plot in Figure 4 was not digitized. Both numeric series are `measured_reduced`: they are calculated from the authors’ measured Table I inputs using the paper’s printed relations.

`rego-1985-na2o-sio2-transpiration-reductions.py` reproduces all 31 reductions from those stored Table I inputs. It converts Na mass with 22.98976928 g mol⁻¹ and sets `nCO = printed CO cell × 10⁻² mol`, because the printed column is `Moles CO Collected (× 10²)`. For Eq. [4], it evaluates `pNa = (nNa × P) / (2.5 nNa + nCO)` and then `pCO = P − pNa`, with P in atm. For Eq. [6], it evaluates `log₁₀(aNa₂O) = log₁₀[(pNa)² × pCO] − log K`, using the printed `log K` values, 3.279 at 1300 °C and 4.089 at 1400 °C. Decimal arithmetic runs at 50-digit precision; unrounded pNa and pCO feed Eq. [6]. Only the final pNa, pCO, and activity fields are rounded to the least significant precision of the printed pressure, Na mass, and CO amount. The `pressure_atm` field is rounded in atm; the migrator converts it to Pa.

Run 18 sanity check: full-precision Eq. [4] gives approximately `pNa = 0.01154700528187146574600734209 atm` and `pCO = 0.9774529947181285342539926579 atm`; Eq. [6] gives approximately `aNa₂O = 1.061780170890613583602458526 × 10⁻⁸`. The stored values are `pNa = 1.15 × 10⁻² atm` (`1165.2375 Pa` after unit conversion), `pCO = 9.77 × 10⁻¹ atm`, and `aNa₂O = 1.06 × 10⁻⁸`. The CO-column conversion is therefore consistent with the printed exponent; the discrepancy was intermediate rounding before Eq. [6].

The fitted equations are retained as located context on PDF page 3 (published page 315):

- Eq. [7], 1300 °C: `log[(pNa)^2 · pCO] = −6.58 + 6.22 XNa2O + 6.75(XNa2O)^2`, R² = 0.97, for `0.18 < XNa2O < 0.46`. Its 13 stored pressure-product rows are runs 2–14.
- Eq. [8], 1400 °C: `log[(pNa)^2 · pCO] = −4.92 + 2.44 XNa2O + 11.80(XNa2O)^2`, R² = 0.99, for `0.22 < XNa2O < 0.41`. Its 15 stored pressure-product rows are runs 16 and 18–31.
- Eq. [9], 1300 °C: `log aNa2O = −9.86 + 6.22 XNa2O + 6.75(XNa2O)^2`.
- Eq. [10], 1400 °C: `log aNa2O = −9.01 + 2.44 XNa2O + 11.8(XNa2O)^2`.

Equations [9] and [10] are retained as printed fit context derived from Eqs. [7]/[8] through Eq. [6]; they are not stored as measured activity rows. Figures 2–4 contain plotted pressure/product/activity values and were not digitized. Figure 5’s extrapolation and comparison values were not digitized. They are figure-read candidates for a later figure pass.

## Conditions and reference states

The activity standard state is exactly as printed in the abstract: **“pure liquid as standard state”** (PDF page 1, published page 313). The activity observations carry the corresponding pure-liquid Na₂O reference. Eq. [6] is on PDF page 2 (published page 314); its adjoining text says: **“If the standard state for C is chosen as graphite, its activity is unity.”**

Each run has its own temperature heading, composition, and printed `P (atm)`. Pressure is converted from that run’s Table I value to Pa for the identity, with the Table I locator retained in its per-run experiment. The paper also has the general method sentence “under a system pressure of one atmosphere”; that sentence was not promoted to a uniform row value. The per-run table values, including 0.977–1.015 atm, are used. Pure CO carrier gas is recorded from the apparatus description on PDF page 2 (published page 314), and the Table I CO flow stays in its printed mL/min unit.

The paper states no numeric O₂ or fO₂ value and no printed buffer offset. The experiments’ pure-CO/graphite atmosphere is preserved as printed; no numerical or buffer-relative fO₂ is inferred. Thus `fO2_log` is a located unknown condition, and the migrated fO₂ identity axis is unknown on the pressure and activity rows. The run measurements are in-situ liquid-melt values; the described air cooling occurs after the measured run and is not a quenched-glass measurement.

## Schema and duplicate review

The 31 raw run bundles preserve composition, flow, total pressure, captured Na mass, and CO amount together. No single token in `tools/validate_literature_extracts.py::OBSERVATION_TYPES`, `simulator/battery/migrate.py::QUANTITY_ALIASES`, `simulator/battery/migrate.py::TYPE_QUANTITY`, or `simulator/battery/enums.py::Quantity` describes that multi-field bundle. The input observations therefore use a descriptive non-token quantity and `admission_status: typed_refusal`; the actual Na partial pressure and Na₂O activity have the exact `partial_pressure` and `activity` tokens. `gas` and `l` are mapped phases in `simulator/battery/migrate.py::PHASE_MAP`; there is no phase schema gap.

The existing Rego 1988 extract contains 85 Na₂O observations and no binary Na₂O–SiO₂ composition row. The 1985 rows are distinct and were not merged into it.

## Compare and isolated migration

`rego-1985-na2o-sio2-transpiration-mineru-compare.csv` records the required row-aware compare. It reports 62 `unverifiable` results and no mismatches: the stored pressure and activity values are located at Eqs. [4] and [6], not as numeric cells in Table I. The 189-cell Table I audit independently covers the printed table inputs.

The real isolated migrator produced 93 observations and 31 per-run experiments, with 0 hard issues and 31 unavailable values. Those 31 unavailable records are exactly the typed-refused raw run bundles; the 62 reduced pressure/activity rows have numeric point values. There are no unknown-phase observations. The only unknown quantities are the exact 31 typed-refused bundle rows. The migrator queues only those 31 bundles, each for quantity, method, and value. It queues no numeric pressure or activity row for fO₂: the fixed-valence Na₂O–SiO₂ activity identity does not require oxygen, while scoring the gas partial pressures would require a grounded fO₂ value, which the paper does not print.

Identity-axis examples after migration:

- Run 1 Na pressure: `T = 1573.15 K`, composition Na₂O/SiO₂ = `0.1789/0.8211`, total pressure = `102844.875 Pa`, fO₂ unknown; migrated pNa = `226.968 Pa`.
- Run 1 Na₂O activity: same printed temperature, composition, and total pressure; fO₂ unknown; migrated activity = `2.67e-9`; reference state is pure-liquid Na₂O.
- Run 31 Na pressure: `T = 1673.15 K`, composition Na₂O/SiO₂ = `0.4066/0.5934`, total pressure = `101628.975 Pa`, fO₂ unknown; migrated pNa = `9382.695 Pa`.

The focused test pins the complete 31-run render grid, all 62 migrated pressure and activity values, and every row’s temperature, composition, atmosphere, total pressure, and unknown fO₂ condition. Its mutation checks confirm that changing a rendered mg-Na input or multiplying run 18’s migrated activity by ten fails an assertion. No battery store was regenerated; expected addition is 31 per-run experiments and 93 observations (62 numeric measured-reduced values and 31 unavailable typed-refused input bundles).

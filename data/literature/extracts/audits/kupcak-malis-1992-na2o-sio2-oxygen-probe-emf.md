# Kupčák and Mališ (1992): extraction audit

## Scope and source order

Machine text was used first: MinerU tables and markdown, then `pdftotext-layout.txt`. The MinerU table QA reported numeric agreement of 1.000 for Table I and Table II. Table I was flagged for 15 comma/dot decimal candidates; the render showed all six printed composition cells use decimal points (`0.45`, `0.50`, `0.55`, `0.65`, `0.70`, `0.75`). The QA flagged one Table III numeric mismatch; its PDF render confirms the cell is `2.08 × 10^-11` Pa. The Table III measurement is outside the requested melt-activity extraction and is not stored.

Rendered PDF pages 3, 4, and 5 were used to verify table layout and printed locators. The Table I table has seven columns (`x(SiO2)`, then three `T/K` and `EMF/mV` pairs), six composition groups, and 40 measurement pairs. Table II has seven composition rows and three property columns (21 property values). Both tables' row and column assignments agree with the render. Table III has five rows and four columns, and is excluded because it reports cell-II EMF, calculated oxygen partial pressure, and electrolyte transfer numbers rather than Na2O activity.

The table tool's `compare` pass emitted zero point candidates because all Table II values are typed refusals in the source-specific `value_as_printed` form and Table I is in the context container; `compare` reads only numeric entries under observation `values.points`. It reported no mismatches or locator errors. The full table-by-cell verification above uses the two machine reads and the page renders instead.

## Full-cell audit

`(a)` means MinerU and pdftotext agree on digits/sign/exponent; `(b)` means the rendered page confirms the cell. Table I was independently read by both machine sources and layout checked against page 3; its flagged composition decimals were render checked. Table II was read identically by both machine sources and layout checked against page 4. The sole Table III numeric mismatch was checked against page 5.

| Printed cell group | PDF page / row | Confirmation | Result |
|---|---:|:---:|---|
| Table I x(SiO2) = 0.45; all eight T/EMF pairs | 3 / composition group 0.45 | (a), (b) | 1581/−305; 1573/−285; 1523/−278; 1473/−280; 1425/−278; 1423/−280; 1378/−280; 1363/−265 |
| Table I x(SiO2) = 0.50; all five T/EMF pairs | 3 / composition group 0.50 | (a), (b) | 1523/−210; 1516/−190; 1473/−180; 1423/−175; 1418/−170 |
| Table I x(SiO2) = 0.55; all eight T/EMF pairs | 3 / composition group 0.55 | (a), (b) | 1578/−70; 1581/−72; 1518/−80; 1529/−82; 1478/−80; 1473/−80; 1432/−79; 1383/−74 |
| Table I x(SiO2) = 0.65; all seven T/EMF pairs | 3 / composition group 0.65 | (a), (b) | 1563/85; 1573/90; 1531/100; 1480/90; 1463/88; 1413/105; 1598/75 |
| Table I x(SiO2) = 0.70; all six T/EMF pairs | 3 / composition group 0.70 | (a), (b) | 1573/110; 1528/125; 1491/140; 1483/120; 1433/135; 1478/128 |
| Table I x(SiO2) = 0.75; all six T/EMF pairs | 3 / composition group 0.75 | (a), (b) | 1571/153; 1526/160; 1478/170; 1573/150; 1471/180; 1423/187 |
| Table II ΔG(Na2O) central values and printed ± widths | 4 / rows x = 0.45–0.75 | (a), (b) | 54.4±3.8ᵃ; 35.8±5.1; 15.0±2.1; 0; −18.3±3.7; −25.2±4.4; −33.8±1.7 |
| Table II ΔS(Na2O) central values and printed ± widths | 4 / rows x = 0.45–0.75 | (a), (b) | −19±17; −55±52; 4±12; 0; −20±22; −31±41; −47±13 |
| Table II ΔH(Na2O) central values and printed ± widths | 4 / rows x = 0.45–0.75 | (a), (b) | 26.4±28.7; −44.7±81.3; 20.8±19.5; 0; −47.2±37.3; −71.5±65.0; −103.0±21.0 |
| Table II footnote `a`, confidence interval at 95% | 4 / footnote | (a), (b) | Confirms the ± widths are confidence intervals at the 95% significance level. |
| Table III pO2 x(SiO2)=0.50 | 5 / row 4 | (b) | Confirms the OCR mismatch as `2.08 × 10^-11` Pa; cell excluded from this rail. |

The Table II values are relative to the 0.40 Na2O–0.60 SiO2 reference melt. Table I EMF stays in the `context` container with its cell description and Eq. (3); no numeric a(Na2O)/a0(Na2O) is printed. Absolute activities need the reference activity taken from Yamaguchi S., Imai A., Goto K.S., *Scand. J. Metall.* 11, 263 (1982); this cross-source derivation is not part of this extract. Figure 4 is only a figure-read candidate: horizontal axis x(Na2O) 0.1–0.6, left axis a(SiO2) 0–1, right axis log a(Na2O) −4 to −10, four line series, and no discrete data-point symbols. No figure values were read.

## Refusal and schema audit

The Table II values are the authors' own tabulated reductions of Table I EMF measurements: the source says the EMF temperature dependence was fit by least squares and the partial molar quantities were calculated at 1473 K. Their `method_class` is `measured_tabulated`, following the Kim 2004 precedent for an author's tabulated reduction; this class does not claim a direct instrument reading or a linked derived parent. Each cell is typed-refused because the quantities are relative to the reference melt. Each row's `derivation_note` records the path through Eq. (3), Gibbs-Duhem integration, and the Table I context row id `kupcak_1992_table1_emf_context`. The EMF cells remain in context: the `Quantity` enum (`simulator/battery/enums.py:62-110`) has no EMF quantity, and `MethodToken.EMF_CELL` (`enums.py:424-434`) is a method token, not a measured quantity. No context id is used as `derived_from`. No Table I numeric a/a0 refusal is fabricated because the paper prints EMF cells and Eq. (3), not numeric ratios.

The closed observation family symbol is `OBSERVATION_TYPES` (`tools/validate_literature_extracts.py:79-100`); `gibbs_table` is available but there is no partial-molar-specific observation type. The migrator's `TYPE_QUANTITY` maps `gibbs_table` to `Quantity.DELTA_FG` (`simulator/battery/migrate.py:435-444`), which would be wrong here: `Quantity.DELTA_FG` is formation Gibbs energy (`simulator/battery/enums.py:85`), not the printed partial molar Gibbs energy relative to a reference. `Quantity.PARTIAL_MOLAR_ENTHALPY` exists (`enums.py:89`) and is used for ΔH. `Quantity.S` (`enums.py:93`) is standard molar entropy, not partial molar entropy. The closed `QUANTITY_ALIASES` mapping (`migrate.py:453-548`) has no partial-molar Gibbs or entropy token. Therefore those ΔG and ΔS rows retain their names in `quantity_label`, are left quantity-unbound, and migrate as the explicit schema-gap exception directed by the controller; the focused test pins exactly those 14 IDs as quantity-unknown. No schema or migrator code was changed.

Phase checks found the required state token `Phase.L = "l"` (`simulator/battery/enums.py:282-288`) and role `PhaseRole.MELT` (`enums.py:301-310`); the observations concern melts, not quenched glass. `PHASE_BASIS_TOKENS` at `enums.py:663` is `{wt_percent, mole_fraction, ppm_mass}`; the printed x values are represented as mole fractions, so no new basis is claimed. Existing partial-molar records were checked: Shornikov 1997 uses `partial_molar_enthalpy` for enthalpy and `S` for partial entropy, but this extract follows the code-specific ruling that `S` means standard molar entropy and leaves partial entropy unbound. Ueshima 1984's generic `partial_molar_quantities` label is not a recognized token and is not copied.

The source prints a furnace-atmosphere pO2 elsewhere, but it is not an observation-specific melt condition for every Table II row. Table II prints only temperature and composition; total pressure and melt fO2 are not assigned. No room temperature, ambient pressure, standard pressure, or assumed fO2 is added.

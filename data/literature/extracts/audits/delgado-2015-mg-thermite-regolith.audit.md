# Delgado et al. 2015 extraction audit

## Scope and result

The paper reports thermite combustion and thermal-analysis experiments, not a pyrolysis-yield campaign. Table 1 supplies quoted initial simulant oxide values. The only quantitative reaction temperature stated in prose is an approximate c-DTA exothermic peak of about 560 °C for 26 wt% Mg with 74 wt% JSC-1A. Figure 4 labels Mg and MgO; the prose reports phase changes at the three XRD heating stops. The extract retains the full Table 1 cell matrix, the printed reaction-peak wording, reactant splits and printed conditions. Figure-derived curves remain undigitised.

The table contains seven printed oxide rows and three named simulant columns: 21 numeric cells, with no printed total row. All 21 are retained as printed strings and have individual fidelity samples. Each cell is a one-point observation so its own named simulant composition can reach its identity. The prose peak is retained as an approximate typed refusal because no quantity token exactly names an exothermic thermite reaction peak. The actual C6 table total is seven rows; the MinerU draft contains nine body rows including its two header rows.

The required isolated migration produced 22 observations (21 table-cell points plus the prose peak), zero hard issues, and 22 unavailable values; none migrated as a scored numeric value. All 22 quantities are unknown because neither the quoted feedstock component wt% nor the thermite reaction peak has an exact quantity token. No phase is unknown. Each Table 1 row has a known identity composition normalized by the existing migration path; the peak's named-material mixture remains an accepted identity-composition gap. Conditions for temperature, total pressure, and fO2 remain unknown where not printed.

## Table 1 full-cell audit

The two machine reads (MinerU HTML table and born-digital pdftotext) agree on the digits in every cell. The page 3 render checked the row and column assignment for the whole table. No comma decimals, footnote fusions, minus signs, exponents, stacked cells or uncertainties occur in Table 1. The `[9]` and `[10]` citation suffixes printed in the column headers are preserved in each point label. The table has no printed footnote and no ± column heading.

| Table 1 row (species) | JSC-1A [9] | JSC-Mars-1A [10] | Mojave Mars [10] | PDF page / row | Cell evidence |
|---|---:|---:|---:|---|---|
| SiO2 | 45.7 | 43.48 | 49.4 | 3 / 3 | (a), row/column placement checked on render |
| Al2O3 | 16.2 | 22.09 | 17.10 | 3 / 4 | (a), row/column placement checked on render |
| Fe2O3 | 12.4 | 16.08 | 10.87 | 3 / 5 | (a), row/column placement checked on render |
| CaO | 10.0 | 6.05 | 10.45 | 3 / 6 | (a), row/column placement checked on render |
| MgO | 8.7 | 4.22 | 6.08 | 3 / 7 | (a), row/column placement checked on render |
| Na2O | 3.2 | 2.34 | 3.28 | 3 / 8 | (a), row/column placement checked on render |
| TiO2 | 1.9 | 3.62 | 1.09 | 3 / 9 | (a), row/column placement checked on render |

Evidence key: (a) MinerU and pdftotext read the same digits; the render confirms cell assignment. Table number is the printed Table 1, not the MinerU block index. Absolute PDF page 3 is printed page 3.

The committed compare output reports 21 matched cells and no mismatches. The compare helper's phase matcher, `tools/mineru_table_draft.py::_phase_matches_column`, does not accept the canonical migrator token `condensed_solid`; it only accepts `gas`, `liquid`, or `solid` in this check. To keep the extract's valid phase token unchanged, the comparison used a temporary copy with only the observation-level `phase: condensed_solid` lines omitted. The committed extract was migrated separately; its phase maps through `simulator/battery/migrate.py::PHASE_MAP` and all 22 migrated observations have a known phase.

Table 1 values are quoted-attributed data, not measurements made in this paper: JSC-1A is marked `[9]`, and JSC-Mars-1A and Mojave Mars are marked `[10]`. The bibliography identifies [9] as Ray et al. (2010), “JSC-1A Lunar Soil Simulant: Characterization, Glass Formation, and Selected Glass Properties”; [10] as Peters et al. (2008), “Mojave Mars Simulant – Characterization of a New Geologic Mars Analog.” The paper does not provide another value for any oxide row. The printed seven oxide values for each simulant remain unchanged in every cell observation and in its point-condition composition. Their totals are below 100 wt%; no remainder oxide or printed total is added. The migration path normalizes the seven printed oxide inputs to mole fractions for `Identity.composition`, using `simulator/battery/migrate.py::_composition_from_plain`; the focused test checks that mapped result against `wt_pct_to_mole_fraction` and separately pins the unnormalized printed inputs.

## Other stored numeric and qualitative facts

The following prose values were read from pdftotext and checked against the page renders. Locators below are absolute PDF page and printed table/figure/equation labels.

| PDF page / printed page | Printed item retained | Source and audit disposition |
|---|---|---|
| 1 / 1, Eqs. 1–2 | SiO2 + 2 Mg → Si + 2 MgO; ΔH°rxn = −146 kJ per mol Mg. Fe2O3 + 3 Mg → 2 Fe + 3 MgO; ΔH°rxn = −328 kJ per mol Mg. | pdftotext and page 1 render agree; equation context only, not an observation or a new reduction. |
| 2 / 2, Experimental | Simulant sources as printed: JSC-Mars-1A and JSC-1A from ORBITEC; Mojave Mars from NASA. Milling at 1100 rpm; four cycles, each 10 min milling and 60 min cooling; 1.5 mm zirconia balls; balls-mixture ratio 4:1. | pdftotext and page 2 render agree. The ratio is retained verbatim without imposing a basis. |
| 2 / 2, Experimental | Mg powder −325 mesh (<44 µm), 99.8% pure; mixing for 1 h; pellets 13 mm diameter and 21-24 mm height. | pdftotext and page 2 render agree. |
| 2 / 2, Experimental | Combustion chamber 30 cm diameter × 40 cm height; argon at 1 atm for combustion; tungsten-wire top ignition, with no ignition temperature or electrical input printed; K-type thermocouple wire 254 µm. | pdftotext and page 2 render agree. Argon at 1 atm is scoped to combustion, not thermal analysis. |
| 2 / 2, Experimental | TGA 209 F1 Iris and DSC 404 F1 Pegasus; alumina crucibles; argon flow 20 mL/min; heating rates 5 °C/min or 10 °C/min depending on test. | pdftotext and page 2 render agree. The paper gives no total pressure for these thermal-analysis runs. |
| 3 / 3, Results and Discussion | THERMO calculations use Mg concentration from 0 to 100% and pressure 1 atm. Figure 1's x-axis prints wt%. | Prose and rendered axis agree. These are calculation inputs, not combustion experiment conditions; plotted model temperatures are figure-only and undigitised. |
| 4 / 4, Fig. 2 | Experimental maximum combustion temperatures for JSC-Mars-1A and Mojave Mars; Mg mixture concentration range 20 to 60 wt%. | Prose and rendered figure agree on the stated range. Point values are figure-only and undigitised; the methods state combustion argon at 1 atm. |
| 4 / 4, Fig. 3 | 26 wt% Mg and 74 wt% JSC-1A; TGA at 10 °C/min; distinct exothermic peak “about 560°C”; initial endothermic peak associated with water vaporization; reaction occurs below the printed Mg melting point of 650°C. | Prose and rendered page agree. The reaction peak is stored as a measured-direct typed refusal, not a thermodynamic phase-transition temperature. The plotted c-DTA curve is not digitised. |
| 4 / 4, Fig. 4 | Figure labels Mg and MgO. XRD heating stops at 500, 550, and 590 °C at 5 °C/min; no MgO at 500 °C, partial Mg-to-MgO conversion at 550 °C, and all Mg converted to MgO at 590 °C. Cooled in argon; source says Mg is still solid during reaction. | Text and rendered Figure 4 labels agree. These are qualitative phase outcomes; no phase fraction, composition, yield or uncertainty is printed. No other phases are named in the XRD text or pattern labels. |
| 4 / 4, Fig. 5 | JSC-Mars-1A and Mojave Mars with 26 wt% Mg were heated to 650 °C at 10 °C/min; JSC-1A/Mg is the comparison. JSC-Mars-1A has a higher-temperature peak, Mojave Mars the lowest. | Prose and rendered plot agree. Peak values are figure-only and undigitised. Thermal total pressure is not stated. |
| 5 / 5, Fig. 6 | Binary Fe2O3/Mg and SiO2/Mg. Fe2O3: >2 µm, 99%, Sigma-Aldrich. SiO2: −400 mesh (<37 µm), 99.5%, Alpha Aesar. | Prose and rendered plot agree. “Under the same conditions” refers to the preceding DSC run: 650 °C at 10 °C/min. No Mg:oxide ratio or total pressure is printed; plotted peak values are undigitised. |
| 5 / 5, Fig. 7 | Mixture #1: 71 wt% Mg/SiO2 thermite and 29 wt% Mg/Fe2O3 thermite; #2: 55/45 wt%; #3: 38/62 wt%. | Prose, bullet suffixes and rendered plot agree. These are percentages of thermite components, not Mg:regolith ratios. The paper gives no combined Mg wt%, run temperature, heating rate or total pressure; plotted peak values are undigitised. |

## Schema gaps and migration expectations

`composition_series` is in `tools/validate_literature_extracts.py::OBSERVATION_TYPES`. The exact quantity for a quoted component wt% of an initial regolith simulant is absent from `simulator/battery/migrate.py::QUANTITY_ALIASES`, `simulator/battery/migrate.py::TYPE_QUANTITY`, and `simulator/battery/enums.py::Quantity`. The existing `equilibrium_phase_composition` quantity is explicitly for an equilibrium phase; its basis set `simulator/battery/enums.py::PHASE_BASIS_TOKENS` includes wt percent but does not change that meaning. `simulator/battery/enums.py::PhaseRole` distinguishes phase roles, none of which turns a feedstock analysis into an equilibrium-phase composition. `simulator/battery/enums.py::Quantity.RESIDUE_COMPONENT_COMPOSITION` is for residue output and is not a starting simulant composition. The extract therefore leaves all 21 migrated table-cell observations quantity-unknown with a descriptive non-token quantity and `typed_refusal` status. The phase token `condensed_solid` maps through `simulator/battery/migrate.py::PHASE_MAP`.

The analogous initial-composition row in `data/literature/extracts/kems-026-markova-1984.yaml` also uses a descriptive quantity because no exact quantity token is available. For Delgado, each of the 21 cell observations records one oxide result plus the same simulant's full seven-element composition as a printed mass-percent point condition. This per-cell composition binding lets the existing `simulator/battery/migrate.py::_composition_from_plain` path normalize recognized oxide mass-percent vectors for the identity without changing the stored values or closing the printed percentages to 100. The test checks all 21 cells and compositions, including exact column labels and row/column coordinates.

The Figure 3 sample composition is printed as 26 wt% Mg and 74 wt% JSC-1A and is retained verbatim in `delgado_2015_jsc1a_cdta_reaction_peak`. Per the controller ruling, this remains a binding gap: `simulator/battery/migrate.py::_mass_percent_components` rejects the mixture because JSC-1A is a named simulant, not a supported chemical component. The migrated row has no `Identity.composition`, and the point-condition composition records the migrator's unsupported-component refusal. JSC-1A is not expanded into Table 1 oxides.

`thermite_exothermic_reaction_peak_temperature` is also descriptive rather than a token. `phase_transition` is an allowed observation type, but the measured exothermic thermite reaction peak is not a thermodynamic transition temperature. The row stays typed-refused with the printed approximate value and units. The table and reaction-peak rows are quoted-attributed/measured-direct respectively; no evidence class was changed to bypass a gate.

## Scope audit and unavailable outputs

The printed reactant data found are the Table 1 oxide values, the 20–60 wt% Mg combustion range, the 26 wt% Mg / 74 wt% simulant samples, binary oxide pairs with no printed Mg:oxide ratio, and the three ternary thermite-component splits. The paper does not state an ignition temperature or energy, and it gives no numeric binary Mg:oxide ratio. Table 1's `[9]` and `[10]` citations are retained as printed.

The only experiment pressure stated is 1 atm in argon for combustion. The separate 1 atm statement applies to THERMO calculations only. Thermal-analysis methods state argon flow at 20 mL/min, not total pressure; fO2 and a gas ratio are not stated. No argon pressure or room-temperature condition is inferred for XRD samples.

No numerical metal yield or recovery, mass balance, uncertainty, product or residue assay, pyrolysis yield, or residue composition is printed. The XRD outcomes identify Mg and MgO qualitatively but provide no phase mass fractions. There are no supplementary files under the corpus supporting directory for this source. No figure-only series values are digitised. `tests/test_yaml_flow_scalar_commas.py` remains part of acceptance.

The 21 Table 1 cell observations hold 21 numeric printed cells; the c-DTA typed-refusal source row holds one approximate numeric prose result. The isolated migration produces 21 quantity-unknown table-cell records and one quantity-unknown reaction-peak record, all unavailable rather than scored numeric points. Context-only apparatus, schedule, pressure, ratio, and thermochemical entries are retained separately. The expected store change is one work, 22 unavailable observations, and no numeric scored observations; no store regeneration is committed.

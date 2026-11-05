# Extraction audit: Colson, Floden & Haugen (2005)

Source id: `colson-floden-haugen-2005-nio-feo-o2-activities`

DOI: 10.1016/j.gca.2005.02.005

## Cell census and verification

| Printed table | PDF page | Printed rows | Numeric cells | Blank cells | Scope and verification |
|---|---:|---:|---:|---:|---|
| Table 1 | 3 | 16 | 80 | 0 | Four oxide wt% columns and NBO. Every row and column assignment was checked against the page render. |
| Table 2 | 4 | 17 | 14 | 20 | FeO experiment temperature and Epk columns only: seven paired rows and ten paired blanks. The `ggᵃ` marker and row/column layout were checked against the page render. |
| Table 3 | 7 | 16 | 8 | 8 | FeO activity-coefficient column. The row/column layout and the `0.42ᵇ` cell were checked against the page render. |
| **Total** |  |  | **102** | **28** | Cell-level records are in `colson-floden-haugen-2005-nio-feo-o2-activities.cells.csv`. |

Input order was MinerU table drafts and markdown, then the born-digital
`pdftotext-layout.txt` transcription. The MinerU QA pre-screen reported exact
numeric-digit agreement with pdftotext for all numeric cells in each table:
Table 1, 80/80; Table 2, 44/44 across all five columns; Table 3, 24/24 across
all three measured columns. Invented `<details>` chart tables were ignored.
The scoped FeO-cell counts above include the corresponding machine-read blanks.

The compare output is preserved in
`colson-floden-haugen-2005-nio-feo-o2-activities.compare.csv`. It reports 15
`mismatch` statuses: the seven Epk and eight gamma observations. The values in
all 15 rows agree with both machine reads and the checked table layouts; this
is a compare-tool phase-token limitation, not a cell discrepancy.
`tools/mineru_table_draft.py::_phase_matches_column` accepts `gas`, `liquid`,
and `solid`, but the migrated extract correctly uses the required phase token
`l`, which that helper rejects before comparing candidate cells. No tool or
migrator code was changed. The per-cell audit records the independent
machine-read agreement and the render checks, including the flagged footnote
marker.

Distinct table locators checked against renders are Table 1 on page 3, Table 2
on page 4, and Table 3 on page 7. Equation 23 and its fitted coefficients were
checked on page 11. Table 3's `0.42ᵇ` reads as numeric `0.42` with footnote `b`;
footnote `b` attributes it to a value calculated from Colson et al. (1990).
The standard-state text and uncertainty header were checked on page 7.

## Recorded cells and conditions

- Table 1 has 16 named compositions. Its printed columns are SiO2, Al2O3, MgO,
  CaO, and NBO. The footnote says the oxide values are wt%, generally from
  electron-microprobe averages of five or more analyses; `gg52Si*` is the
  marked calculated composition. The separately printed addition is
  `0.1–0.3 wt% for FeO (added as Fe2O3)`; the paper does not give a row-specific
  addition, so it is retained as a note on each activity-coefficient row and
  not fabricated as a point composition component.
- Table 2 contains 17 rows. FeO temperature and Epk are printed together in
  seven rows: `gg` 1579 °C / −388 mV; `gg2%Al-A` 1580 / −400; `gg4%Al-A` 1575 /
  −393; `gg10Ca-A` 1580 / −393; `gg14Ca-A` 1580 / −381; `gg17Mg` 1593 / −356;
  and `gg22Mg` 1596 / −386. Both FeO cells are blank in the other ten rows.
  The `ggᵃ` footnote states that the reference electrode was in a diopsidic
  silicate melt.
- Table 3 contains 16 rows. Its FeO column is headed `γFeO, Liq wuestite` and
  carries `1σ = ±13%` for every value. Numeric cells are `gg` 1.00,
  `gg2%Al-A` 0.99, `gg4%Al-A` 0.78, `gg10Ca-A` 0.92, `gg14Ca-A` 1.02,
  `gg17Mg` 0.81, `gg22Mg` 0.70, and `Diop` 0.42ᵇ. The other eight cells are
  blank.
- The activity-coefficient rows carry the complete four-oxide composition
  from Table 1, its Table 1 locator, the Table 2 temperature where that
  composition has a printed FeO experiment temperature, and the printed
  1-atm furnace pressure from the experimental-method section. `Diop` has no
  FeO temperature in Table 2 and its Table 3 value is attributed to earlier
  measurements, so its temperature stays unknown.
- The paper lists air, CO2, 25%CO–75%CO2, 80%CO–20%CO2, and 90%CO–10%CO2 as
  furnace atmospheres but does not assign them to composition rows. It says
  actual experimental fO2 varied. Table 2's `calculated for fO2 = 1` is a
  pure-O2 normalization, not the experimental fO2. Each observation therefore
  preserves an unknown row-specific fO2 and gas assignment. No fO2 is inferred.
- The printed furnace condition is `Deltech 1-atm gas-mixing furnace`; the
  experiment registry carries 101325 Pa and every observation also carries a
  located point condition. The migrator maps the registry pressure to
  `identity.total_pressure_Pa`.
- The paper prints `aFeO = (aO2−)(aFe2+)` in the abstract but no numeric FeO
  activity. Figure 3's voltammetric curves, Figure 4's smoothed diffusion
  coefficients, and Figure 10's reciprocal Fe2+ activity-coefficient fit are
  figure-read candidates and remain undigitised.

## Standard state, evidence, and schema gaps

Table 3 prints the FeO header `γFeO, Liq wuestite`; footnote `a` calls the
standard state `Liquid wuestite ("FeO")` (PDF p. 7, published p. 3067, Table 3,
footnote a) and prints its free-energy expression as
`−244118 + 115.559·T − 8.474·T·ln(T)`. The same footnote defines the O2−
activity reference as a melt of diopsidic composition and gives
`R = 8.315 J K−1 mol−1`, `F = 96485 C mol−1`, `n = 2`, with T in Kelvin.
For that free-energy expression, footnote a cites `Barin (1989) after Holzheid
et al. (1997)`. Each γFeO row retains this citation and the same Table 3,
footnote a locator in `standard_state.free_energy_expression_source_as_printed`
and `standard_state.free_energy_expression_source_locator`; no cited source was
consulted or used to infer a wuestite composition.
The controller ruling confirms that the source does not state the liquid
wuestite's stoichiometry. The extract preserves the printed state and leaves the
migrated reference state unknown; it does not substitute pure liquid
stoichiometric FeO or solid wustite.

The closed `ReferenceStateConvention` set has no separate liquid
nonstoichiometric-wuestite convention; `Polymorph.WUSTITE` is paired with
Fe0.947O in `simulator/battery/polymorph_dictionary.py::_JANAF_CRYSTAL_ROWS`,
while `simulator/battery/records.py::StandardState` requires a closed
convention and endmember. These
facts do not identify the paper's liquid reference. The raw statement is
retained with a typed schema gap, following the Doyle 1988 precedent that keeps
`molten Fe0.947O` verbatim and records its schema gap. The schema request is a
nonstoichiometric liquid-wuestite reference state. The gap cites
`simulator/battery/enums.py::ReferenceStateConvention`,
`simulator/battery/enums.py::Polymorph.WUSTITE`, and
`simulator/battery/records.py::StandardState`.

Table 2's Epk cells are printed voltammetric peak potentials, not FeO
activities or activity coefficients. There is no exact potential/EMF quantity
token in `simulator/battery/enums.py::Quantity`,
`simulator/battery/migrate.py::QUANTITY_ALIASES`, or
`simulator/battery/migrate.py::TYPE_QUANTITY`. The seven numeric values are
preserved as typed refusals with descriptive non-token quantity labels; they
are not assigned an activity token. The focused test pins that the unknown
quantity set is exactly those seven Epk rows.

The paper also prints `1 / γFe2+ = A + B·XMgO + C·XCaO`, with R2 = 0.85,
coefficients and 1σ uncertainties, and eight data points. It is retained as
the printed fit, not retyped as FeO γ or converted into unprinted values. The
paper's own calculated γFeO values use smoothed diffusion coefficients that
are not tabulated. Their source method class remains `author_derived` with no
fabricated parents. The Diop value retains its printed Colson et al. (1990)
attribution. The migrator does not promote these rows to measured-reduced
evidence without admitted measured lineage; this follows its conditional
reduced-method handling in
`simulator/battery/migrate.py::_CONDITIONAL_REDUCED_METHODS`.

## Isolated migration and tests

The isolated migration produced 15 observations, 8 numeric gamma values and
7 unavailable Epk values, with 0 hard issues. There are 102 numeric source
cells and 28 absent FeO cells across the three audited tables. All observations
migrate with phase `l`; the seven documented Epk quantity gaps are the only
unknown-quantity observations. The activity-coefficient rows map their
composition, total pressure, and printed Table 2 temperature into the identity
through existing paths; fO2 and the liquid-wuestite reference convention stay
unknown. The Diop temperature stays unknown because no FeO experiment
temperature is printed for that row.

Identity axes printed from the isolated migration:

| Row | Composition identity (mole fraction, SiO2 / Al2O3 / MgO / CaO) | Total pressure | fO2 | Temperature | Reference state |
|---|---|---:|---|---:|---|
| gg, Table 3 row 2 | 0.4453583246657281791505984332 / 0.04337861227861909255161016068 / 0.3525338268400329587229867703 / 0.1587292362156197695748046359 | 101325 Pa | unknown; not mapped from source | 1852.15 K | unknown; verbatim state retained |
| gg14Ca-A, Table 3 row 8 | 0.4519066201100130016024911391 / 0.04420354108032039839283343814 / 0.3627495232244354029128189715 / 0.1411403155852311970918564518 | 101325 Pa | unknown; not mapped from source | 1853.15 K | unknown; verbatim state retained |
| Diop, Table 3 row 17 | 0.5035899857248491720163590971 / 0.0005299202464227058064644490992 / 0.2386231199132347763604678039 / 0.2572569741154933458167086502 | 101325 Pa | unknown; not mapped from source | unknown; no FeO temperature printed | unknown; verbatim state retained |

Local checks, all passing:

- `tests/test_colson_floden_haugen_2005_feo_extract.py`: 2 passed. It pins all
  table rows and values, migrated values and conditions, exact unknown rows,
  and proves mutations to an interior γ value and its temperature condition
  fail.
- `tests/test_yaml_flow_scalar_commas.py`: 17 passed.
- `tests/battery/test_migrate.py -k test_j01_store_census_series_numeric_matches_declared_field`:
  1 passed, 583 deselected.
- `tests/test_literature_extracts.py -k colson-floden-haugen-2005-nio-feo-o2-activities`:
  1 passed, 884 deselected.

No battery store was regenerated. Expected store delta: one work, one
experiment, four context records, and 15 observations (8 numeric and 7 typed
refusals), subject to the standard migrator conversion.

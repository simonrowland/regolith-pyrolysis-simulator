# Pickrell (1994) extraction audit

## Numeric table inventory and scope

The corpus has 26 MinerU table blocks. PDF pages 31–32 are the reagent/equipment inventory and an empty table block, with no numeric corrosion results. Printed numeric tables are inventoried below; PDF page numbers are absolute, while printed pages are reported separately.

| Table | PDF page(s) | Printed page(s) | Caption / rows | Disposition |
|---|---:|---:|---|---|
| 1 | 50 | 38 | Alkali reaction kinetics of Si3N4; 4 temperature/rate rows | Extracted; rate and two summary values lack exact quantity tokens |
| 2 | 67 | 55 | Alkali reaction kinetics of SiC; 4 temperature/rate rows | Extracted; rate and two summary values lack exact quantity tokens |
| 3 | 79 | 67 | Alumina-alkali product chemical analysis; 7 rows | Extracted; raw product moles/ratio lack exact quantity token |
| 4 | 81 | 69 | Beta-alumina/alumina XRD peak-area ratio; 8 rows | Extracted; semi-quantitative peak area/ratio lacks exact quantity token |
| 5 | 104–106 | 92–94 | Soda-cordierite phase analysis; 35 composition/temperature observations | Inventory only: batch carbonate/ceramic reactions, not vapor exposure |
| 6 | 108 | 96 | Soda-cordierite solidus and carnegieite inversion temperatures; 10 table rows | Inventory only: phase transitions, not vapor-corrosion kinetics |
| 7 | 110–111 | 98–99 | Potassia-cordierite phase analysis; 16 rows | Inventory only: batch carbonate/ceramic reactions, not vapor exposure |
| 8 | 112 | 100 | Potassia-cordierite solidus temperatures; 4 rows | Inventory only: phase transitions, not vapor-corrosion kinetics |
| 9 | 114–116 | 102–104 | Soda-zirconia phase analysis; 37 rows | Inventory only: batch carbonate/ceramic reactions, not vapor exposure |
| 10 | 118–119 | 106–107 | Soda-CSZ phase analysis; 19 rows | Inventory only: batch carbonate/ceramic reactions, not vapor exposure |
| 11 | 121–123 | 109–111 | Soda-aluminum titanate phase analysis; 25 rows | Inventory only: batch carbonate/ceramic reactions, not vapor exposure |
| 12 | 125–129 | 113–117 | Soda-alumina-titania phase analysis; 59 printed composition/heat-treatment rows | Inventory only: batch carbonate/ceramic reactions, not vapor exposure |

Tables 1–4 are the printed tables attached to the requested gaseous alkali corrosion kinetics and reaction-product analyses. Tables 5–12 start a separate solid oxide/carbonate reaction-products study. The distinction follows the source's Section 3.3 gas-flow apparatus description versus Section 4.2's prepared oxide/carbonate mixtures. Their table numbers and pages remain in the inventory so their exclusion is auditable.

## Cell audit

Cell key: **(a)** MinerU table draft and pdftotext-layout agree on the digits; render checked once for table row/column placement. **(b)** verified from the page render where one machine read was malformed or a numeric field had a flag. All values in Tables 1–3 are (a). Table 4 rows 1–7 are (a); row 8's beta-alumina area and ratio are (a), and the `0` transcription replaces the malformed MinerU `Oren` per render (b); the alumina-area cell is the printed dash `------` (b). The four table layouts were checked against PDF renders on pages 50, 67, 79, and 81. Table 4's final row is retained as printed, including its dash rather than a fabricated zero for alumina peak area.

| Table / PDF page | Printed count | Row / cells | Audit |
|---|---:|---|---|
| 1 / 50 | 4 | Rows 1–4: T 950, 1000, 1050, 1100 °C; rates 3.00, 7.40, 12.95, 27.22 μm/h | (a), rendered layout check |
| 1 / 50 | summary | Activation energy 199 kJ/mol; average SiO2/Na2O molar ratio 2.22 ± 0.64 | (a), render |
| 2 / 67 | 4 | Rows 1–4: T 950, 1000, 1050, 1100 °C; rates 2.95, 4.01, 5.33, 9.20 μm/h | (a), rendered layout check |
| 2 / 67 | summary | Activation energy 104 kJ/mol; average SiO2/Na2O molar ratio 2.55 ± 0.45 | (a), render |
| 3 / 79 | 7 | Rows 1–7, all four columns: etch times 0.5, 1, 2, 4, 6, 10, 14 h; alumina moles 6.22, 4.30, 2.89, 4.48, 2.37, 3.52, 3.37; soda moles 6.52, 6.70, 4.06, 1.14, 0.45, 0.57, 0.45; ratios 0.95, 0.64, 0.71, 3.94, 5.24, 6.23, 7.45 | (a), rendered layout check |
| 4 / 81 | 8 | Rows 1–7 all four numeric columns; row 8: etch time 26, beta-alumina area 0, alumina area `------`, ratio 0 | (a) except malformed row 8 cells verified by render (b) |
| Methods / 39 and Figure 10 / 66 | — | Table 1–2 conditions: Section 3.3 on page 39; SiC atmosphere: Figure 10 caption on page 66 | Render (b): p39 setup and p66 caption checked; Figure 10 prints dry air with 0.98 vol% sodium nitrate vapors |

No numeric cells from tables 5–12 are stored. Their conditions and phases describe batch phase-reaction experiments; they do not measure corrosion under alkali vapor. The thickness-time points in Figures 4, 10, 11, 14, 18, 21, 23, and 25 are not digitized. Their prose-reported fitted constants are recorded below; plots remain figure-read candidates. Other image-only plots and micrographs remain out of scope. The source reports no wall-deposit mass, specimen mass-change series, or absolute alkali partial pressure.

### Prose cell audit

The prose values below were read from the born-digital pdftotext text and checked against the corresponding PDF page renders. The cell assignment is the printed material, temperature, unit, and method described in the sentence. Printed n exponents are retained as method context and are not additional rate rows. Plot points used to obtain the rate constants are not transcribed.

| Source / PDF page | Printed count | Stored cells | Audit |
|---|---:|---|---|
| Alumina / 75 | 3 rates | 1050, 1100, 1150 °C: 0.12, 0.24, 0.45 μm/h; n = 0.9, 1.1, 1.3 | pdftotext + render; no figure points |
| Alumina / 77 | 1 fit | Ea approximately 200 kJ/mol; SD approximately 5 kJ/mol | pdftotext + render; Arrhenius fit of stored rates |
| Alumina / 75 | 1 bound | Less than 5 μm after 48 h at 1050 °C | pdftotext + render; inequality retained |
| Mullite / 85 | 4 rates | 950–1100 °C: 6, 10, 25, 39 μm/h; n = 0.7, 0.8, 1.2, 1.2 | pdftotext + render; no Figure 18 points |
| Mullite / 88 | 1 fit | Ea 182 kJ/mol; SD approximately 20 kJ/mol | pdftotext + render; Arrhenius fit of stored rates |
| Cordierite / 132 | 2 linear rates | 950, 1000 °C: 6, 9 μm/h; n = 0.8, 0.8 | pdftotext + render; Chapter 5's prose rates only, no Figure 21 points |
| Cordierite / 132 | 2 parabolic rates | 1050, 1100 °C: 0.0046, 0.0055 mm²/h; n = 0.5, 0.4 | pdftotext + render; mm²/h retained, no Figure 23 points |
| TTZ / 132 | 4 linear rates | 950–1100 °C: 0.4, 1.1, 3.4, 4.2 μm/h; n = 3.0, 1.6, 1.1, 0.8 | pdftotext + render; Chapter 5's prose rates only, no Figure 25 points |
| TTZ / 97 (printed 85) | 1 fit | Ea 229 kJ/mol | pdftotext + render; Arrhenius fit of stored rates; Figure 26 is cited on this page |
| TTZ / 95 (printed 83) | 2 measurements | 0.008 mm after 15 h at 950 °C; zero thickness for exposure times below 8 h | pdftotext + render; no temperature attached to the sub-8 h statement |
| TTZ / 97 (printed 85) | 2 blank results | 0.007, 0.010 mm after 12 h at 1000 °C, without alkalis present | pdftotext + render; two reported pairs of samples, not corrosion rates |
| Aluminum titanate / 132 | 1 measurement | Approximately 25% dimensional change at short times at 950 °C | pdftotext + render; stored as a dimensional change, not a rate |

Chapter 5's rounded restatement of the Si3N4 and SiC kinetics is not duplicated. Table 3 soda-mole cells and Table 4 alumina-peak-area cells remain explicit non-scored sibling columns: the extract schema/migrator currently projects the alumina-mole and beta-alumina-area fields respectively. The printed Table 3 ratio is retained, but its lineage does not claim that rounded stored moles reproduce it.

## Conditions, evidence, and schema gaps

Section 3.3 (PDF page 39, printed page 27) reports sodium nitrate as the alkali source and one fixed campaign concentration of approximately 1.0 ± 0.1 vol%, calculated assuming ideal gases from NaNO3 weight loss and fixed compressed-air flow. It says the fixed concentration was used throughout the experiments. The setup uses compressed air filtered for CO2 and moisture at 86 cm³/min and a salt-source zone at 720 ± 10 °C. Figure 10 (PDF page 66, printed page 54) captions the same campaign as dry air containing 0.98 vol% sodium nitrate vapor. These are stored as one campaign atmosphere, with the Section 3.3 value and derivation and Figure 10 as the alternate caption locator; all exposed rate rows carry one identical atmosphere condition. Neither value is converted to pressure. No total pressure, absolute alkali partial pressure, or numerical fO2 is reported. Individual thickness-time data are not extracted from the figures.

The stored corrosion rates describe reaction-layer thickness change, not mass loss. No exact quantity token exists in `Quantity` (`simulator/battery/enums.py::Quantity`): `Quantity.MASS_LOSS_RATE` and `Quantity.WALL_DEPOSIT_MASS` neither describe the printed thickness growth. The enum also has no activation-energy, etched-product-mole, XRD peak-area, or dimensional-change token. For the product and peak-area candidates, `Quantity.RESIDUE_COMPONENT_COMPOSITION` and `Quantity.EQUILIBRIUM_PHASE_COMPOSITION` are not exact tokens. `PHASE_BASIS_TOKENS` (`simulator/battery/enums.py::PHASE_BASIS_TOKENS`) does not provide an XRD peak-area basis. The validator's closed `OBSERVATION_TYPES` set (`tools/validate_literature_extracts.py::OBSERVATION_TYPES`) supplies `rate_series` but no more specific observation type. `TYPE_QUANTITY` (`simulator/battery/migrate.py::TYPE_QUANTITY`) associates that type with `Quantity.MASS_LOSS_RATE`; therefore every row explicitly uses its descriptive non-token quantity label. `map_quantity` (`simulator/battery/migrate.py::map_quantity`) returns an unsupported-quantity unknown for such a label before the type-inference fallback (also in `map_quantity`). This prevents the rate-series fallback from assigning mass loss. Existing extracts use `equilibrium_phase_composition` for measured equilibrium phase compositions (for example Park 2021); those are not the etched-product or peak-area measurements here. No quantity or phase gap is claimed to have an exact token: the migration test pins each descriptive label's exact unknown reason and verifies no unknown phases. The source-supported `cr` phase maps through `PHASE_MAP` (`simulator/battery/migrate.py::PHASE_MAP`); no phase schema change is needed.

Evidence classes: Table 1–2 rates and the prose rates are this work's own reductions from measured thickness-versus-time data. Each is `measured_reduced` with a located `raw_inputs: not_printed`, following the schema's unpublished-input case (`SCHEMA.md:232–289`); no plotted values are invented as parent observations. Activation energies are `measured_reduced` from the stored temperature-specific rates via the printed Arrhenius fit. The alumina and mullite rate uncertainties are reported as standard deviations; their confidence levels are not stated. Table 3 raw alumina/soda moles are the authors' atomic-absorption results (`measured_direct`). Its printed ratios are retained as `measured_reduced`, with located `raw_inputs: not_printed`; the exact unrounded inputs are not printed, and the displayed rounded mole columns fail to reproduce the last ratios. Table 4's ratio is reduced from the beta-area and alumina-area columns using the printed relation, `measured_reduced`; the final row's ratio of zero is retained as printed even though the alumina-area cell is a dash. The soda-mole and alumina-area columns are documented non-scored siblings rather than fabricated observations. No confidence level is stated for Table 4 areas or quotients.

No experiment total pressure, room-temperature condition, or unprinted exposure duration is added. The TTZ zero-thickness-under-8-h statement has no assigned temperature; the migration test checks temperature remains unknown while its printed campaign atmosphere is retained. The alkali-free blank conditions are kept separate from the campaign vapor atmosphere. The glassy sodium silicate reaction layers on Si3N4 and SiC are described as liquid at test temperature and bead during cooling; this remains source context and is not relabeled as a room-temperature measured liquid. Table 3/4's etching time is an analysis axis, not an alkali exposure duration.

## Migration and tests

The table comparator matched all 37 stored numeric table cells to the MinerU draft with no mismatches, missing cells, locator errors, or unverifiable cells. Prose values were separately checked against the cited page renders as listed above. The focused test runs the isolated real migrator and checks all source observation ids, each exact unsupported-quantity refusal, cell values, prose row counts, printed locators, evidence classes, and conditions. The expected migration is 66 point-level records (42 table point records plus 24 prose records), all unavailable because the requested quantities have no exact tokens, with 0 hard issues and 0 unknown phases. It checks total pressure remains unknown and unbound, and the TTZ sub-8 h statement remains without temperature. No store regeneration is committed. Expected generated-store delta: 1 work, 1 experiment, 66 point-level observations, 0 typed quantities, and 66 unavailable quantities.

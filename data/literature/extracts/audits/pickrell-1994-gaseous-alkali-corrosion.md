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

No numeric cells from tables 5–12 are stored. Their conditions and phases describe batch phase-reaction experiments; they do not measure corrosion under alkali vapor. Figure values are not transcribed: Figures 4 and 10 (Si3N4 and SiC thickness vs time), 11–15 (SiC/alumina kinetics and Arrhenius plots), 18 onward (mullite and cordierite), and the remaining image-only plots/micrographs. They are figure-read candidates, not tabulated values. The source reports no wall-deposit mass, specimen mass-change series, or absolute alkali partial pressure.

## Conditions, evidence, and schema gaps

Section 3.3 (PDF page 39, printed page 27) reports sodium nitrate as the alkali source, an atmosphere with 1.0 ± 0.1 vol% NaNO3 vapor, compressed air filtered for CO2 and moisture at 86 cm3/min, and a salt-source zone at 720 ± 10 °C; the page render confirms the setup statement. Figure 10's caption (PDF page 66, printed page 54) specifies dry air containing 0.98 vol% sodium nitrate vapors for the SiC series; its render confirms the exact value, which is attached only to Table 2 rows. Table 1 and 2 rates are temperature-specific outputs of fits to the corrosion-thickness/time data; the source says each plotted condition averages four specimens. Individual times are not extracted from the figures. No total pressure, absolute alkali partial pressure, or numerical fO2 is reported. The rates retain the printed gas concentration as an atmosphere note; no conversion to partial pressure is made.

The stated rates are reaction-layer thickness growth in μm/h. `Quantity.MASS_LOSS_RATE` in `simulator/battery/enums.py:95` is a mass-rate quantity; the closed `Quantity` enum at lines 67–112 has no exact layer-thickness-rate token, so `mass_loss_rate` is not used as a near-match. Activation energy and SiO2/Na2O molar ratio likewise have no exact `Quantity` members. Table 3 reports raw alumina/soda mole amounts and their ratio, not a normalized composition; `Quantity.RESIDUE_COMPONENT_COMPOSITION` (`enums.py:107`) is not a token for etched product amounts. Table 4 reports selected XRD peak areas and their quotient, neither a concentration nor composition token. `Quantity.EQUILIBRIUM_PHASE_COMPOSITION` (`enums.py:106`), used by extracts such as `park-2021`, represents equilibrium phase composition; it does not describe etch-product moles or semiquantitative peak areas. `PHASE_BASIS_TOKENS` (`enums.py:663`) is limited to `wt_percent`, `mole_fraction`, and `ppm_mass`, with no basis token for peak areas or their quotient. The closed `OBSERVATION_TYPES` set (`tools/validate_literature_extracts.py:80–102`) admits the nearest series containers used here, while `TYPE_QUANTITY` (`simulator/battery/migrate.py:435–444`) maps `rate_series` to `MASS_LOSS_RATE`. Each observation therefore sets an explicit descriptive non-token quantity label to prevent an inferred near-match. The printed values remain available and the real migrator records their quantities as unknown. `PhaseRole` is closed to melt, slag, glass, metal, and matte (`enums.py:301–313`); phase-state tokens are separately mapped by `PHASE_MAP` (`simulator/battery/migrate.py:380–398`), including the source-supported `glass` and `cr` states used here. No phase gap is claimed.

Evidence classes: Table 1–2 rate constants are fitted reductions of measured thickness-versus-time data. Their underlying thickness/time points occur only in Figures 4 and 10, so the tabled rate constants are `quoted_attributed`, not `model_derived`; no unstored figure input is presented as a stored parent. Activation energies are reduced from the stored temperature-specific rate constants using the printed Arrhenius relation, so they are `measured_reduced` with those rate observations as parents. The silicate-layer ratios are the authors' atomic-absorption results (`measured_direct`). Table 3 is direct atomic-absorption analysis (`measured_direct`); its printed alumina/soda ratios are separated as `measured_reduced` from the stored mole amounts. Table 4's ratio is reduced from the two stored XRD peak-area columns using the printed relation, `measured_reduced`; the final row's ratio of zero is retained as printed despite its dashed alumina-area cell. No confidence level is stated for the Table 4 areas or quotients.

No experiment total pressure, room-temperature condition, or unprinted exposure duration is added. The glassy sodium silicate reaction layers on Si3N4 and SiC are described as liquid at test temperature and bead during cooling; this is reported in the observation notes, not relabeled as a room-temperature measured liquid. Table 3/4's etching time is an analysis axis, not an alkali exposure duration. The dissertation's numeric alumina reaction products listed in prose at specific T/time are noted in the source discussion but are not substituted for table data.

## Migration and tests

The table comparator matched all 37 stored numeric cells to the MinerU draft with no mismatches, missing cells, locator errors, or unverifiable cells. The focused test runs the isolated real migrator and checks the 11 source observations and their row counts, the exact quantity-unknown source-observation set, printed cell pins from Tables 1–4, and migrated temperature/point-condition binding. The migrated rate observations retain the printed atmosphere and temperature, while total pressure has no migrated value. Migration produces 42 point-level observation records, 0 hard issues, 42 unavailable quantities, and 0 unknown phases. No store regeneration is committed. Expected generated-store delta: 1 work, 1 experiment, 42 point-level observations, 0 typed quantities, and 42 unavailable quantities.

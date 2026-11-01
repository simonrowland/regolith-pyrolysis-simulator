# Kunisada and Iwai (1993) extraction audit

## Table 1 audit

The primary transcription is the MinerU HTML table and Markdown for PDF page 2. The table reader found one table, 57 body rows, and eight columns. Its pre-screen reported numeric agreement of 0.793 with pdftotext, including 31 comma/dot-decimal flags, 19 fused-reference-decimal flags, and 56 numeric mismatches. I resolved the entire table against the 600-dpi render of PDF page 2; the cell/row crosswalk is in `kunisada-iwai-1993-na-silicate-sulfide-capacity.cells.csv`. Every populated numeric cell is marked render check (b), and the table's row/column assignment was checked once against the page.

The printed row count is 57: sample numbers 1–14, 101–111, 201–211, 301–311, and 401–410. The final alumina-series row is 410; no row 411 is printed. Blank oxide cells remain unreported and are not converted to zero. The extract retains the complete table as context and maps every printed oxide and sulfur measurement to an observation: 157 oxide-composition cells and 57 sulfur assays, for 214 measured observations. Each observation is located to its printed Table 1 row and column.

## Experimental conditions and analytical measurements

The experiments were conducted at 1500 °C. The samples were heated in argon, then exposed to 59.5 vol% CO, 39.7 vol% CO2, and 0.8 vol% SO2 at 504 cm3/min for 90–120 min. The paper reports platinum crucibles for all slags, magnesia for the MgO-bearing series, and alumina for the Al2O3-bearing series, with their dimensions in the context record. Slag and crucible were quenched by pulling them to the top of the reaction tube; whether quenching preserved the analyzed composition is not stated (PDF page 1, published page 43).

Na2O was measured by atomic absorption spectroscopy; the other oxides and sulfur were measured by inductively coupled plasma spectrometry. The reported relative error is 2%. The measured composition observations use the equilibrium-liquid phase token `l`; the paper reports equilibrium slag chemistry at 1500 °C, while the analyzed specimens were quenched. This phase choice describes the source's high-temperature equilibrium property; it does not assert that quenching preserved the composition.

Every measured composition observation carries the printed 1500 °C temperature, reactive gas mixture, and reported gas partial pressures with their source locators. The printed definition is `C_S = (wt% S) (P_O2 / P_S2)^(1/2)`. At 1500 °C, the authors report calculated partial pressures `pO2 = 1.34 × 10^-3 Pa` and `pS2 = 1.81 × 10^2 Pa`; the gas-reaction equations are retained in context. The paper does not report total experimental pressure, so it remains unknown on every observation and is not inferred from the flow rate.

## Capacity values, correlations, and figures

Table 1 prints sulfur assays, not per-sample sulfide-capacity values. The paper prints the definition and author-derived correlations in the abstract (PDF page 1), Eq. (4) (page 3), Eqs. (5)–(6) (page 4), and Eq. (8) (page 5). These relations, including the reported standard deviation and the `m` and `m'` factors, are stored as located context. The figures contain pointwise capacities, but the task prohibits reading values from figures. The 57 pointwise capacities that could be calculated from the sulfur assays and gas potentials are not printed by sample and were not computed; they remain a follow-up candidate.

The abstract, definition, gas-potential values, equations, and optical-basicity discussion were checked against 300-dpi renders of PDF pages 1–5. The equation locators are the abstract on page 1, Eq. (4) on page 3, Eqs. (5)–(6) on page 4, and Eq. (8) on page 5; the definition and gas-potential values are on page 2.

The discussion prints a pure-Na2O theoretical optical-basicity index of 1.65 (contrasted with the original 1.15) and a modified effective pure-MnO index of 1.21 (contrasted with the original 0.59). No Na2O activity is printed; there is no activity value to forward to the controller.

Figures 1–6 are captioned in the extract as context. Their numeric points are not read. Figure 1 is binary sulfide capacity versus basicity; Figure 2 compares prior Na2O–SiO2 capacities; Figure 3 shows additive-oxide effects; Figure 4 evaluates those effects; Figure 5 plots capacity against modified basicity; Figure 6 relates capacity to theoretical optical basicity. Caption pages were checked against 300-dpi renders of PDF pages 3–4; the optical-basicity discussion was checked on page 5.

## Validation evidence

- Focused extract test: 3 passed. It pins the 57-row count, four render-checked table cells, three measured observation values, and migrated conditions and locators for all 214 observations.
- Mutation check: changing the pinned sample 1 sulfur assay from `0.003` to `0.004` made the focused test fail; restoring `0.003` made it pass.
- `tests/test_yaml_flow_scalar_commas.py`: 17 passed after the observation migration.
- `tests/test_literature_extracts.py`: `UNVERIFIED-local`; the required local run reached the 300-second cap (exit 124) at 16% progress, with no failure output.
- Isolated migration: `obs=214 hard=0 unavailable=0 quantity_unknown=0 phase_unknown=0`. The three pinned rows carry identity composition, `fO2_Pa`, and temperature; total pressure is unknown, as the paper does not report it. Temperature, gas mix, pO2, pS2, and composition conditions preserve their source locators.
- MinerU table comparison: `matched=0 no-match=0 mismatch=0 presence-only=0 needs_mapping=0 locator-error=0 unverifiable=0 total=0`. Its `_numeric_points` reader does not inspect the phase-composition `component_amount` field, so no table measurement is comparable through this command; the committed cell crosswalk and focused render pins provide the numeric audit.

No battery store was regenerated. The expected store delta is 214 measured composition observations and no pointwise sulfide-capacity observations; the latter require a separate follow-up decision. The printed capacity relations remain context records.

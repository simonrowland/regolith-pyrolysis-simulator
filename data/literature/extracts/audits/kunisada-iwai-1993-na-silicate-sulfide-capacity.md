# Kunisada and Iwai (1993) extraction audit

## Table 1 audit

The primary transcription is the MinerU HTML table and Markdown for PDF page 2. The table reader found one table, 57 body rows, and eight columns. Its pre-screen reported numeric agreement of 0.793 with pdftotext, including 31 comma/dot-decimal flags, 19 fused-reference-decimal flags, and 56 numeric mismatches. I resolved the entire table against the 600-dpi render of PDF page 2; the cell/row crosswalk is in `kunisada-iwai-1993-na-silicate-sulfide-capacity.cells.csv`. Every populated numeric cell is marked render check (b), and the table’s row/column assignment was checked once against the page.

The printed row count is 57: sample numbers 1–14, 101–111, 201–211, 301–311, and 401–410. The final alumina-series row is 410; no row 411 is printed. Blank oxide cells remain blank and are not converted to zero. The extract retains all eight printed columns, including the analyzed sulfur mass percent.

## Experimental conditions and capacity definition

The experiments were conducted at 1500 °C. The samples were heated in argon, then exposed to 59.5 vol% CO, 39.7 vol% CO2, and 0.8 vol% SO2 at 504 cm3/min for 90–120 min. The paper reports platinum crucibles for all slags, magnesia for the MgO-bearing series, and alumina for the Al2O3-bearing series, with their dimensions in the extract. Slag and crucible were quenched by pulling them to the top of the reaction tube; whether quenching preserved the measured composition is not stated (PDF page 1, published page 43).

The printed definition is `C_S = (wt% S) (P_O2 / P_S2)^(1/2)`. At 1500 °C, the authors report calculated partial pressures `pO2 = 1.34 × 10^-3 Pa` and `pS2 = 1.81 × 10^2 Pa`. The gas-reaction equations and the footnote-marker reading are retained in the extract. The paper does not print total experimental pressure, so no total pressure is supplied or inferred.

The table’s measured sulfur values and the printed capacity equation provide the inputs for pointwise log-capacity values without reading the figures. The extraction evidence rule requires those equation-reduced values to be `measured_reduced`. However, the current capacity path under `simulator/battery/migrate.py::PRINTED_CAPACITY_QUANTITIES` requires `author_derived`, and its special capacity branch emits `EvidenceClass.AUTHOR_ESTIMATE`. That conflicts with the task’s required evidence class. The printed capacity token exists (`sulfide_capacity`); this is not a schema gap. Capacity observations are therefore held pending a controller ruling or a migrator change, which is out of scope for extraction work. No figure values are digitized.

## Correlations, optical basicity, and figures

The printed correlations are retained in context: `log C_S = 4.64 B - 6.88` at 1500 °C (with σ = 0.37), the modified basicity `B'` with its printed `m` and `m'` factors and `log C_S = 4.64 B' - 6.88`, and `log C_S = 27.0 Λ_th - 21.2` at 1500 °C. The fits are quoted from the paper; their plotted input points are not reconstructed.

The discussion prints a pure-Na2O theoretical optical-basicity index of 1.65 (contrasted with the original 1.15) and a modified effective pure-MnO index of 1.21 (contrasted with the original 0.59). No Na2O activity is printed; there is no activity value to forward to the controller.

Figures 1–6 are captioned in the extract as context. Their numeric points are not read. Figure 1 is binary sulfide capacity versus basicity; Figure 2 compares prior Na2O–SiO2 capacities; Figure 3 shows additive-oxide effects; Figure 4 evaluates those effects; Figure 5 plots capacity against modified basicity; Figure 6 relates capacity to theoretical optical basicity. Caption pages were checked against 300-dpi renders of PDF pages 3–4; the optical-basicity discussion was checked on page 5.

## Migration limitation

The current extract carries the complete measured Table 1 as table context but has no scored observations because pointwise capacity rows cannot be assigned the required `measured_reduced` evidence class with the current migrator. The isolated migration and tests can validate the context and fidelity pins, but cannot establish the required per-capacity-row identity/condition binding. This remains the blocking item.

## Validation evidence

- Focused extract test: 3 passed. It pins four rendered Table 1 cells, the 57-row count and row IDs, blank-cell handling, and the unprinted total-pressure state; after isolated migration it also pins the rendered table and method locators.
- Mutation check: changing sample 1 Na2O from `29.1` to `29.2` made the focused render-pin test fail; after restoring `29.1`, the focused test passed again.
- `tests/test_yaml_flow_scalar_commas.py`: 17 passed.
- `tests/test_literature_extracts.py`: `UNVERIFIED-local`; the required local run reached the 300-second cap (exit 124) at 16% progress, with no failure output.
- Isolated migration: `obs=0 hard=0 unavailable=0 quantity_unknown=0 phase_unknown=0`; no unknown quantity/phase observations are present. The focused migration test also confirms the PDF page 2/table 1 and PDF page 1 method locators survive. The zero observation count reflects the open capacity-class blocker; it does not verify row-condition binding for capacity observations.
- MinerU table comparison: `matched=0 no-match=0 mismatch=0 presence-only=0 needs_mapping=0 locator-error=0 unverifiable=0 total=0`. The comparison utility checks numeric observation points, while this WIP retains Table 1 in the context container.

No battery store was regenerated. Current expected store delta is zero observations; the pending capacity series would add 57 observations if a compliant evidence path is authorized.

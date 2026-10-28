# Masuda and Tanaka (1979) extraction audit

## Table coverage and render audit

- Table 1, PDF page 5 / printed page 17: 22 printed body rows, 178 numeric cells. All numeric cells and the row/column assignment were checked against the full-page render. The rendered table labels seven condensates, one interrupted residue, and the JB-1 reference. Table 1 states trace-element ppm and selected major oxide percent, on a Ta-free basis.
- Table 2, PDF page 6 / printed page 18: 21 printed body rows, 149 numeric cells. All numeric cells and the row/column assignment were checked against the full-page render. The average REE row excludes Eu and Yb.
- `masuda-tanaka-1979-vacuum-condensates.cells.csv` records all 327 numeric cells by printed table, PDF page, printed body-row index, column, exact printed text, and verification method (b), the render. The pdftotext layer has only ADS bibliographic watermarks, so it could not serve as the independent second read. MinerU QA reported no malformed table, but classified both pages as scanned and skipped digit-agreement checks.
- The 13 numeric residue ppm cells (La through Sr, excluding absent dashes) match the draft compare tool with row and column coordinates: 13 matched, 0 mismatch, 0 no-match, 0 locator error. All other cells remain in the Table 1/2 context transcripts.

## Data disposition and conditions

- Table 1 has seven condensate analyses, mixed trace ppm and selected oxide percent, with unreported components. The existing `condensate_composition` token requires a complete component mole-fraction vector. This is a schema gap for the printed mixed/incomplete analyses; all seven table rows remain verbatim in `context_no_token` with the typed refusal and candidate token.
- The interrupted 9-26-5 residue has 13 trace-element ppm values and uses `residue_composition_vs_time` with `residue_ppm`. Table 1 prints dashes for every major-oxide residue value, including Na2O and K2O. It is a single residue endpoint, not a T/time series.
- Table 2 enrichment factors are author-calculated relative values. They are not a measured composition vector or a phase distribution coefficient, and compatible input observations cannot be stored for derivation lineage. They remain attributed typed-refused context. No Table 2 factors are duplicated as independent measurements.
- The table's `Sample analyzed (mg)` reports collected analytical product mass, not initial charge mass. The methods say samples were usually about 100 mg and usually evaporated in about five minutes; neither is assigned to the interrupted run. Initial bell-jar pressure `(2–5) × 10^-5 Torr` is prior to vaporization; the operating pressure rose by orders of magnitude and was not measured above the crucible or collector. No pressure, exact charge mass, interrupted-run time, or fO2 was assigned to residue rows.
- The optical pyrometer range is 1800–2000 °C for the crucible, while local instantaneous sample temperatures can differ. The row condition retains this experiment-level range and leaves a per-row point temperature unknown. The paper does not state a quench result or identify the post-run residue as glass; its phase remains unavailable.

## Figure-read candidates (not extracted)

Figures 3a–3c (enrichment against collection distance), 4 (Yb/Ca/Eu against Sr), 5 (Al against Ca), and 6 (estimated REE patterns in flight-distance fractions) contain plotted-only values. Figure 2a–2c restate Table 2 graphically. No plotted values were digitized.

## Isolated migration

The real migrator produced 13 observations, 0 hard issues, 65 unavailable identity-axis issues (five unprinted axes per observation), and 13 review-queue entries for the unidentified residue phase. The two typed-refused table contexts remain context records. The expected merged-store delta is 13 residue observations and 2 context records; no battery-store files were regenerated.

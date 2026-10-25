# Hayashi et al. (2019) extraction audit

## Machine transcription and renders

The full MinerU table draft was regenerated from the corpus `mineru/` source. MinerU and pdftotext agree on the numeric digits in Table 1 (physical PDF p. 2) and the 13 numeric columns MinerU retained from Table 2 (physical PDF p. 4). The row and column assignments for both tables were checked against their page renders. Table 1 has 2 data rows and 18 numeric cells, including the two numeric sample labels. Table 2 has 9 data rows and 14 numeric columns per row, for 126 numeric cells. The cell audit records 144 cells total.

Table 2's oxygen-pressure cells contain exponents, so all nine were render checked. MinerU omitted the entire printed `gammaFeO1.5` column; pdftotext contains it, and all nine cells were checked against the p. 4 render. The full values are preserved in the extract and render-grid fixture. The row labels were checked against the source's mapping: A1/B1/C1/D1/E1 use master glass 1; A2/B2/C2/D2 use master glass 2.

Evidence `(a)` in the cell audit means MinerU and pdftotext agree on the digits and the render confirms row/column placement. Evidence `(b)` means the render settles a flagged exponent cell or a column MinerU omitted. Table 1 nominal and analyzed compositions are bound as two sample records because the table tool emits scalar cells and cannot express a master-glass composition vector.

## Compare output resolution

The raw row/column-aware compare output reports 36 mismatches. The compare helper does not recognize the migrator's liquid phase token `l` (it only filters `gas`, `liquid`, and `solid`); this accounts for 27 values that match once the phase selector is removed from a temporary compare copy. The extract retains `phase: l`. That compare reports 63 matched values and 9 remaining mismatches: the final `gammaFeO1.5` column absent from MinerU. The 9 cells are confirmed by pdftotext and the page 4 render, as recorded in the full cell audit. No source extract phase or value was changed to make the compare pass.

## Conditions, phase, and schema limits

Table 2 prints row-specific melt composition, PO2, and the measured values. The experiment section prints 1 573 ± 2 K; the width is retained with confidence level not stated. CO–CO2 runs print a total flow of 100 mL/min, but no gas ratio or total pressure. The Ar–O2 mixture is named for PO2 = 1.3 × 10⁻⁶ atm; its ratio and flow are not printed. Total pressure remains unknown on every row. The flow rate is recorded as a flow rate, never as pressure.

The activities use Raoultian reference states. The source names liquid FeO and solid FeO1.5 reference states but does not name an FeO1.5 polymorph, so that polymorph is explicitly unknown. Activity outputs use the stored CFe(Pt) rows as their measured inputs and cite the printed calculation and references; gamma outputs retain their paired activity and row composition.

The Mössbauer percentages are measured after equilibration and quenching into iced water. The paper says Mössbauer was measured at room temperature and says that XRD identified phases, but the narrative does not state the post-quench phase. The extract preserves that phase description without assuming glass. It keeps room temperature as printed and leaves numeric measurement temperature unknown. Quench preservation is `not_stated` on every Mössbauer row.

The source prints Fe2+/Fetotal, Fe3+(oct)/Fetotal, and Fe3+(tetr)/Fetotal percentages, not a combined Fe3+/Fe2+ ratio. Fe2+/Fetotal uses the general valence-ratio token. The schema has no identity axis for ferric coordination site, so the 18 octahedral and tetrahedral rows are retained as typed refusals with their as-printed values and `schema_gap_coordination_specific_ferric_fraction`.

## Figure-only candidates

Numeric points in Figure 2 (valence fractions vs. log PO2), Figures 4–5 (activities vs. composition), Figure 6 (activity coefficients vs. composition), and Figure 7 (activity coefficients vs. valence fractions) remain figure-read candidates and were not digitized.

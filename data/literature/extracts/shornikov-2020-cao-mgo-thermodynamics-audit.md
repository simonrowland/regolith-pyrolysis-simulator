# Shornikov 2020 CaO–MgO thermodynamics — extraction audit

Source: Shornikov, S. I. (2020), Thermodynamic properties of the CaO–MgO melts, Experiment in Geosciences, vol. 26, no. 1, pp. 77–80. PDF pages 1–4 of this file. The preceding abstract on PDF page 1 (Fe–Ca–S) is not this paper and is not extracted. No DOI is printed.

## Tables

MinerU `p002-t01` and `p003-t02` match `pdftotext-layout.txt` on every numeric cell. No `needs_render_check` flag on a numeric cell. The only `en_dash_minus` flags are the reference years "1978–1982". Numeric hyphens are ASCII. Dashes are empty, not zero. O2 `0.000` is a printed zero and is stored.

Table 2 (15 Gibbs energies, kJ/mol, 2973 K from the column header, not the 2600–3300 K model domain) was emitted. Standard pressure, the formation reaction, and formation-from-elements are not inferred. Liquid CaMgO2 is blank and is absent. Evidence is `quoted_attributed`: the caption says the present study calculated the cells, and the cited parents are not stored.

Table 1 melting enthalpies with both a numeric Tm and a numeric ΔHm were emitted (6 CaO + 7 MgO). The placeholder attribution was replaced from that row's reference cell. Digits were not edited. Eliezer et al., 1981 prints ΔHm 68.000 and a dash for Tm; that enthalpy is hand-added and the temperature is unknown. Manara and Ronchi print Tm and a dash for ΔHm; those enthalpies are absent.

Table 1 Tm cells are hand-written `transition_temperature` on `values.T_K`. Emit writes other quantities on `values.value`, which is not where this quantity is read. `property_kind` is `melting_point`. Total pressure is unknown because the table does not print one.

Prose ± attaches only where the introduction names that citation: Manara 3222±25 K, Ronchi 3250±20 K, present-study CaO 3214±12 K, MgO 3246±13 K. The table cells themselves do not print the ±.

## Figure 1

Linear T (K) versus mol% CaO. Data markers only. Phase-field boundaries were not sampled. MinerU's invented chart table was ignored.

Y ticks are the dark stubs just inside the left spine: 3200 at y=28, 3000 at y=119, 2800 at y=209, 2600 at y=300, 2400 at y=391. T = 3261.74032276 − 2.20506362 y. Residuals are within 0.9 K. X: 0 mol% at x=95, 100 mol% at x=650.

Symbol 1 is left-half teal (the outline is a full circle; the centre is the circle, not the fill centroid). Symbol 2 is a red open circle. Symbol 3 is a blue filled circle. Symbol 4 is one half-red disk, red on the lower half. Read 1 is a circle fit. Read 2 is chord midpoints, not an offset of read 1. Three legend glyphs near x=603, y=321–366 were dropped. 32 markers remain. Pairs differ by less than 1.5 px except where the two y reads coincide, and that row stores a half-pixel width (1.1025 K) rather than zero. Uncertainty is half the real difference in K and in mol%.

The caption prints Doman et al., 1965. The reference list says 1963. The caption year is kept.

Composition is mole percent. AmountBasis has no mole-percent token, so the axis stays unknown and the read is text on the row. `property_kind` is `phase_diagram_temperature`, so composition is not required as an identity axis. The marker has no single phase; phase is omitted.

## Figure 2

Panel a is calculated activity curves with series labels and no data markers. Six unavailable activity records (CaO 1–3, MgO 4–6). Panel b is calculated mixing-energy curves. Series 1, 2, 3 and 7 are context (`figure_mixing_energy`), not `delta_fG`. The prose minimum, from –10 kJ/mol at 2973 K to –14 kJ/mol at 3173 K, in the range 50–55 mol% CaO, is one context row.

## Left out

Kubaschewski ΔfH298 of CaMgO2 (−3.77±0.62 kJ/mol), Powell & Searcy dolomite enthalpies, and the eutectic literature sentences are not this paper's tabulated results.

## Tool friction

`compare` reads `values.points` and ignores `values.value`, so it reports no cells. Emit cannot place `transition_temperature` on `T_K`, and one temperature column cannot cover a block that contains a Tm dash. No header is a powers-of-ten factor. The tool was not changed.

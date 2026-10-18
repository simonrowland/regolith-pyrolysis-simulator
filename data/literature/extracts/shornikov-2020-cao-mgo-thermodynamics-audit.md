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

Read 1 is the embedded JPEG. Read 2 is a fresh 600 dpi render of PDF page 2. Each shape uses one geometric definition on both rasters, so the page centre is not a second algorithm on the JPEG segmentation and not an affine copy of it. Affine twins are stored beside the two raw reads. Teal: midpoint of the rightmost dense column (the vertical diameter). Red open circles: centroid of the enclosed hole, except the crescent under the 50 mol% disk, which is the ring centre. Blue: interior centroid. Maroon: midpoint of the widest dense row (the horizontal diameter of the upper-half disk).

JPEG ticks: 3200 at y=28, 3000 at y=119, 2800 at y=209, 2600 at y=300, 2400 at y=391. T = 3261.74032276 − 2.20506362 y. Residuals are within 0.9 K. X: 0 mol% at x=95, 100 mol% at x=650. Page ticks: 3200 K at y=2210.5, 3000 at 2484, 2800 at 2755.5, 2600 at 3027.5. T = 4824.237881 − 0.734617182 y. Page x is 0 mol% at 592 and 100 mol% at 2261.

Census on both rasters: symbol 1 = 7, symbol 2 = 14, symbol 3 = 11, symbol 4 = 8, total 40. A count of eight teal disks is not what the mask shows. The strict teal mask has seven data left-halves plus the legend glyph. Near 50 mol% the black diagonal crosses the red ring and leaves at most seven fringe pixels (JPEG y=265–267, x=369–375). That is not a disk. The legend half-disk near JPEG (603, 388) is dropped, as are the other legend glyphs (JPEG x>575 and y>305; page x>1900 and y>3080).

Symbol 4 is maroon on the upper half. The stored centre is the horizontal diameter, not the upper-half component centroid and not the red ring underneath. At 50 mol% the disk is `s4_04`: JPEG [372.5, 256.0], page [1426.5, 2896.0], T_K 2697.2 (the page read is 2696.8 K). The overlapping red crescent is a separate row, `s2_09`: JPEG [373.0, 263.0], page [1426.0, 2914.0], T_K 2681.8. There is no teal disk in that stack. The clipped blue marker left of 0 mol% is `s3_01`, T_K 3103.8, mol% −0.4.

Stored precision is 0.1 K and 0.1 mol%. The uncertainty is half the unrounded difference of the two reads, rounded half-up. A half-difference that rounds to zero is stored as 1.1 K or 0.09 mol%, which is half a pixel on that axis. No width is 0.

The width is a digitising difference, not a number the paper prints. `observation.uncertainty` and `T_m_uncertainty_K` both migrate as `UncertaintyKind.PRINTED`, and no extract field stores `UncertaintyKind.DERIVED`. Each marker is `status: typed_refusal`. The YAML keeps the positive width. Migration leaves `uncertainty.kind` NONE, keeps the point temperature, and rejects the row. The admission reason names that gap. The printed-band resolver returns no band for kind NONE.

`property_kind` is `liquidus`. The printed composition is mole percent. AmountBasis has no mole-percent token, so the point condition stays unknown and the mole percent is not divided by 100. Identity composition is then the generic unknown "no composition mapped from source", because that unknown mole-percent state is not an oxide weight map the identity filler copies. Phase is omitted: the schema has no boundary token. Table melting points stay `melting_point`.

The caption prints Doman et al., 1965. The reference list says 1963. The caption year is kept.

## Prose kept as schema-gap context

These sentences are printed results. `OBSERVATION_TYPES` has no formation-enthalpy or entropy type, and AmountBasis has no mole-percent token, so they are `quoted_prose_schema_gap` context rows, not scored observations.

- Rankin & Merwin (1916): eutectic 59 mol. % CaO at 2573±50 K. Published page 77.
- Eutectic range: 45 mol. % CaO (Afanasjev & Saldau, 1950) to 59 mol. % CaO (Doman et al., 1963), and 2553 K (Wartenberg & Prophet, 1932; Wartenberg et al., 1937) to 2643 K (Ruff et al., 1933; Doman et al., 1963). Published page 77.
- The Fig. 1 liquidus was constructed from Foex 1968, Ronchi & Sheindlin 2001, and Manara et al. 2014. The sentence crosses the page break onto published page 78.
- Kubaschewski (1972): CaMgO2 decomposing at 2573 K, ΔfH298 = −3.77±0.62 kJ/mol. The paper spells Kubashevsky in that sentence. Shornikov's criticism is kept: no compounds or solid-solution regions at 1173–1273 K (Fletcher et al., 1970; Terpstra et al., 1984).
- Powell & Searcy (1978): dolomite to gaseous CO2 and glassy CaMgO2, ΔfH850 = 78.65 kJ/mol and ΔfS850 = 36.82 J/mol/K. The criticism is kept: the glassy claim rests on broad x-ray peaks, and the torsion data disagree because condensation and self-cooling were not accounted for (Lvov & Ugolkov, 2003).

Table rows that already carry a prose ± (Manara 3222±25 K, Ronchi 3250±20 K, present-study CaO 3214±12 K, MgO 3246±13 K) are not repeated here.

## Figure 2

Panel a is calculated activity curves with series labels and no data markers. Six unavailable activity records (CaO 1–3, MgO 4–6). Panel b is calculated mixing-energy curves. Series 1, 2, 3 and 7 are context (`figure_mixing_energy`), not `delta_fG`. The prose minimum, from –10 kJ/mol at 2973 K to –14 kJ/mol at 3173 K, in the range 50–55 mol% CaO, is one context row.

## Tool friction

`compare` reads `values.points` and ignores `values.value`, so it reports no cells. Emit cannot place `transition_temperature` on `T_K`, and one temperature column cannot cover a block that contains a Tm dash. No header is a powers-of-ten factor. The tool was not changed.

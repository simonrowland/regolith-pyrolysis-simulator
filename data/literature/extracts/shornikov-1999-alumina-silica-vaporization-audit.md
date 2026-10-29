# Shornikov 1999 Al2O3–SiO2 Doklady — extraction audit

Source: Shornikov, S. I., Archakov, I. Yu., and Shultz, M. M. (1999). Evaporation and Thermodynamic Properties of the Al2O3–SiO2 System. Doklady Physical Chemistry, Vol. 364, Nos. 4–6, 1999, pp. 48–50. Translated from Doklady Akademii Nauk, Vol. 364, No. 5, 1999, pp. 643–646. Original Russian text copyright 1999 by Shornikov, Archakov, Shultz. Received October 14, 1998. The byline prints Shultz. No DOI is printed. PDF sha256 ade7468d3eda818d71f44702cc47be2162c7855fc6c513c57216f13837d826cb, 3 pages. pdftotext is empty (scan).

## Duplicate verdict

Not a duplicate of `shornikov-2000-aluminosilicate-vaporization`. That paper is Shornikov, Archakov, and Chemekova, Russian Journal of Physical Chemistry 74(5), 677–683 (2000). Different venue, a third author, and a full article. The two papers report the same campaign. This extract does not score the numbers the 2000 extract already stores.

## Page map

The PDF page 1 header prints the span 48–50. The PDF page 2 running header prints 49. The PDF page 3 running header prints 50. Published page = 47 + PDF page.

## Tables

The paper has no data table. MinerU `<details>` blocks under Figs. 1–3 are chart reconstructions (round mole-percent grids). They were not converted and not emitted.

## Standard state

No activity row is stored, so migration does not call `_standard_state_from_extract_text`. The quotes are on the context rows anyway.

SiO2, equation (1), published page 49. The integral starts at x(Al2O3) = 0. The paper does not state the phase of that limit and does not say a pure-silica cell was measured at 2220 K. Expected phase: unknown. The stable-phase inference is not applied: the reference temperature of a pure oxide is not stated.

Al2O3, equation (3), published page 49. The paper says the liquidus point dictated the standard state. The integral starts at x(SiO2) = 0.263, the complement of the printed 73.7 mol % Al2O3 liquidus. That is a composition anchor, not a pure oxide. Expected phase: unknown. The schema has no field for an activity referenced to a liquidus composition. Amendment, with no affected row: a reference-composition field that can store x(SiO2) = 0.263 and the anchor kind `liquidus`.

## Figures

Renders: 200 dpi full pages `al1999-1.png`, `al1999-2.png`, `al1999-3.png`, and 300 dpi crops `al1999-fig1-2.png`, `al1999-fig2-2.png`, `al1999-fig3-3.png`. No labelled-tick calibration, because no point was read.

Fig. 1, published page 49, 2220 K. Filled squares on (SiO)+ (curve 1), filled circles on (Al)+ (curve 2), dotted I(SiO)+/I(Al)+ with no markers (curve 3). The ordinate is I(i)+ in relative units. Refused as a series. A relative ion current is not stored as a partial pressure. The prose plateau 73.7 ± 0.2 mol % Al2O3 is the overlap row, not a new scored point.

Fig. 2(a), published page 49. Curves 1–4, no markers. Curve 1 is this work at 2220 K. Refused. A continuous curve was not traced.

Fig. 2(b). Curves 1–4, no markers of their own. Refused. Four filled circles are caption series 5, the central atom model at 1873 K [14]. Refused. The filled circle printed as ● 5 at the right of the panel is the legend glyph, not a fifth datum.

Fig. 3, published page 50. Curves 1–4, same series as Fig. 2. The numerals on the plot are curve labels. No data marker. Refused. The ordinate is ΔfGT in kJ/mol along composition, and `OBSERVATION_TYPES` has no type for that curve.

## Prose left unscored

- Liquidus 73.7 ± 0.2 mol % Al2O3 at 2220 K. The 2000 extract stores 73.67 ± 0.15 mol%.
- The 67.6 mol % Al2O3 sample melts at 2160 K, and mullite melts at 2180 K. The 2000 extract stores 2160 K at 67.63 mol% and mullite at 2180 K.
- Toropov and Galakhov [11], 68.9 mol % at 2123 K, and Risbud and Pask [12], 36.9 mol %, are cited literature.
- The second integral in equation (2) is 3% of the first and is neglected. That is a stated approximation, not an activity.
- (Si)+ and (Mo)+ do not exceed 3% of (SiO)+. (AlSiO)+ does not exceed 0.1% of (SiO)+. No composition is paired with either bound.
- Cell: Mo effusion cells, MI-1201, 1720–2520 K, area ratio no less than 250, EOP-66 better than 10 K, ionization 20.0 ± 0.1 eV.
- Literature mullite enthalpies (the 23 kJ/mol spread and the sign disagreement) are cited and were not extracted.

## Admitted

None. `observations` is empty on SiO2 and on Al2O3.

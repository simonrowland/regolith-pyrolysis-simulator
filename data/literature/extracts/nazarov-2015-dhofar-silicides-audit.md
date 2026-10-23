# Nazarov et al. (2015) extraction audit

## Table 2: KEMS pressures

Table 2 is on PDF page 5 (published p. 172). It has seven species rows, 33 numeric pressure cells and two printed em dashes (AlO at 1833 and 1883 K). The two machine reads agree on digits, sign and exponent: MinerU's table HTML uses LaTeX such as `$1.93 \times 10^{-9}$`, while `pdftotext -layout` reads `1.93 × 10–9`. The page render was checked across the whole table for row and column assignment and every pressure value; every cell below is therefore audited by route **(b), render**. The two dashes were also checked against the render.

Table 2 makes the paper in scope as an empirical KEMS pressure series. The earlier out-of-scope note overlooked these rows; no anorthite KEMS series was present in the extract set before this one.

The required `mineru_table_draft.py compare` output is committed in [nazarov-2015-dhofar-silicides-compare-output.txt](nazarov-2015-dhofar-silicides-compare-output.txt). The file contains a CSV header and 33 data rows; the stderr summary is kept outside the CSV. The comparator reports `matched=0 no-match=0 mismatch=33 total=33`. These are parser mismatches, not discrepant digits: the comparator does not parse the LaTeX-formatted pressure cells, so its page candidates contain only the five temperature headings. All 33 flagged pressure cells were resolved against the page 5 render, with no value disagreement. The extract keeps the generic `value` field required by the comparator and `p_atm` read by the existing pressure migrator; both carry the same printed atm value, with no unit conversion. `p_atm_as_printed` retains the printed mantissa and exponent.

| Species row | 1833 K, col. 2 (b) | 1883 K, col. 3 (b) | 1933 K, col. 4 (b) | 1983 K, col. 5 (b) | 2033 K, col. 6 (b) |
|---|---:|---:|---:|---:|---:|
| Ca, PDF row 3 | 1.93 × 10⁻⁹ | 6.11 × 10⁻⁹ | 1.89 × 10⁻⁸ | 5.87 × 10⁻⁸ | 1.14 × 10⁻⁷ |
| Al, PDF row 4 | 1.33 × 10⁻⁹ | 3.29 × 10⁻⁹ | 7.51 × 10⁻⁹ | 2.98 × 10⁻⁸ | 6.07 × 10⁻⁸ |
| AlO, PDF row 5 | — (printed dash) | — (printed dash) | 1.18 × 10⁻⁹ | 5.61 × 10⁻⁹ | 2.29 × 10⁻⁸ |
| SiO, PDF row 6 | 6.95 × 10⁻⁵ | 1.32 × 10⁻⁴ | 2.28 × 10⁻⁴ | 5.77 × 10⁻⁴ | 8.87 × 10⁻⁴ |
| SiO₂, PDF row 7 | 1.58 × 10⁻⁸ | 3.94 × 10⁻⁸ | 8.88 × 10⁻⁸ | 2.08 × 10⁻⁷ | 4.43 × 10⁻⁷ |
| O*, PDF row 8 | 3.16 × 10⁻¹⁰ | 9.20 × 10⁻¹⁰ | 2.63 × 10⁻⁹ | 5.00 × 10⁻⁹ | 1.40 × 10⁻⁸ |
| O₂**, PDF row 9 | 3.66 × 10⁻¹² | 1.28 × 10⁻¹¹ | 4.48 × 10⁻¹¹ | 7.31 × 10⁻¹¹ | 2.68 × 10⁻¹⁰ |

## Equation (1): SiO pressure-temperature fit

Equation (1) on published p. 171 (PDF page 4, index 3) prints `log p_SiO (atm) = −(21 250 ± 1265)/T + (7.414 ± 0.655)`. The extract stores it as a separate SiO `pressure_temperature_relation`, with both printed coefficient widths and the notice that the confidence level is not stated. Equation (1) gives no separate validity range, so the row uses the Table 2 temperature range, 1833–2033 K. The fit uses pressure in atm and temperature in K. The Table 2 SiO point note only says that per-point uncertainty is not reported; it no longer implies that the fit has no uncertainty.

The page 5 table layout and every row-to-temperature-column assignment were visually checked once against the render. Rows O* and O₂** retain their printed calculated status and footnote method as `measured_reduced`; their numerical raw equilibrium inputs are not tabulated and are marked `not_printed` with the table footnote locator. MoO₂ and MoO₃ are kept as cell-contamination context only; neither has a numeric pressure row in Table 2.

## Table 1: meteorite composition context

Table 1 is on PDF page 3 (published p. 170). It has 16 body rows and 12 columns (element; three Si⁰-object columns; eight Fe-silicide columns). All cells below were read in both MinerU and `pdftotext -layout`; their row and column assignments were checked against the page 3 render. Route **(a)** applies to every body cell: the two machine reads agree and the render confirms the table layout. The content is context only and feeds no rail.

Column order: `element | Si⁰-object mean | limits | number of analyses | Fe₂.₈₃Si | Fe₂.₀₀Si# | Fe₄.₈₆Si₃ | Fe₁.₀₀Si | Fe₀.₉₇Si | Fe₀.₉₃Si | Fe₀.₈₇Si# | Fe₂.₉₉Si₇`.

```text
Si    | 57.6 | 50.9–68.4 | 24  | 14.8  | 18.8 | 23.0 | 33.1 | 33.3 | 33.7 | 35.9  | 53.7
Ti    | 0.02 | <0.24     | 24  | b.d.l.| b.d.l.| 0.35 | 0.02 | 0.09 | b.d.l.| 1.40  | b.d.l.
Al    | 0.96 | 0.03–5.39 | 23  | n.d.  | n.d. | n.d. | n.d. | n.d. | n.d. | n.d.  | n.d.
Cr    | 0.04 | <0.14     | 24  | b.d.l.| 0.26 | 9.30 | 0.15 | 0.54 | 0.05 | 2.60  | 1.94
Fe    | 4.85 | 0.54–11.3 | 24  | 81.0  | 66.9 | 65.3 | 62.0 | 61.9 | 59.1 | 57.65 | 43.7
Mn    | 0.17 | <0.40     | 24  | b.d.l.| b.d.l.| 0.16 | b.d.l.| 0.32 | 0.03 | 0.82  | 0.47
Mg    | 0.15 | <0.70     | 16  | n.d.  | n.d. | n.d. | n.d. | n.d. | n.d. | n.d.  | n.d.
Ca    | 0.26 | 0.05–2.13 | 16  | n.d.  | n.d. | n.d. | n.d. | n.d. | n.d. | n.d.  | n.d.
Na    | 0.03 | <0.09     | 16  | n.d.  | n.d. | n.d. | n.d. | n.d. | n.d. | n.d.  | n.d.
K     | 0.02 | <0.12     | 16  | n.d.  | n.d. | n.d. | n.d. | n.d. | n.d. | n.d.  | n.d.
Ni    | 0.53 | <1.76     | 24  | 2.92  | 12.2 | 0.16 | 4.14 | 3.47 | 5.80 | 0.82  | 0.02
Co    | n.d. | n.d.      | n.d.| 0.14  | 0.18 | b.d.l.| 0.22 | 0.05 | 0.22 | b.d.l.| b.d.l.
Cu    | n.d. | n.d.      | n.d.| n.d.  | 0.16 | n.d. | b.d.l.| n.d. | n.d. | b.d.l.| n.d.
P     | 0.24 | 0.11–0.45 | 8   | 0.07  | 1.04 | 0.58 | 0.26 | 0.92 | 1.27 | 0.67  | 0.81
S     | 0.16 | <0.51     | 13  | b.d.l.| b.d.l.| b.d.l.| b.d.l.| b.d.l.| b.d.l.| b.d.l.| b.d.l.
Total | 65.0 | 60–73     |     | 98.9  | 99.6 | 98.8 | 99.9 | 100.6| 100.2| 100.0 | 100.6
```

Table 1 footnotes are retained in the extract. Fig. 4 contains SiO disproportionation Gibbs-energy curves only; its values are figure-read candidates and were not extracted. The invented MinerU `<details><summary>line</summary>` chart table under Fig. 4 was ignored.

## Isolated migration

The real migrator produced 34 observations: the 33 Table 2 point observations and the separate Equation (1) relation row. Validation reports 0 hard issues. The relation row has an unavailable scalar value and unknown single-point temperature because it records a fit over a range; its published equation, coefficients, and Table 2 range remain in the extract. Ten non-hard queue entries remain on `derived_from` for the five O* and five O₂** points. Their numerical raw equilibrium inputs are not tabulated, so the extract records the located `not_printed` raw inputs and does not invent parent observations; the migrator still queues ancestry for page grounding. MoO₂/MoO₃ context and Table 1 context produce no observations.

| Migrated row | Quantity | temperature_K | composition identity | total_pressure_Pa | fO2_Pa |
|---|---|---:|---|---|---|
| Ca, 1833 K | p_partial | 1833 | formula-derived synthetic anorthite: CaO 0.25, Al₂O₃ 0.25, SiO₂ 0.50 | unknown: not derivable from the chamber residual pressure | unknown: no fO₂ condition stated |
| SiO, 1933 K | p_partial | 1933 | formula-derived synthetic anorthite: CaO 0.25, Al₂O₃ 0.25, SiO₂ 0.50 | unknown: not derivable from the chamber residual pressure | unknown: no fO₂ condition stated |
| O₂**, 2033 K | p_partial | 2033 | formula-derived synthetic anorthite: CaO 0.25, Al₂O₃ 0.25, SiO₂ 0.50 | unknown: not derivable from the chamber residual pressure | unknown: no fO₂ condition stated |

For all 33 points, temperature and composition are value states with table locators in `point_conditions`; total pressure and fO₂ are explicit unknown states with table locators. Temperature and composition also reach identity. Total pressure and fO₂ remain unknown because the source does not report them as KEMS conditions. The approximate residual chamber pressure is not the in-cell total pressure, and the oxygen vapor species pressures are not a stated fO₂ condition. The O* and O₂** derivation methods and `not_printed` raw-input declarations are located to the Table 2 footnotes.

## Verification and expected store delta

- `tests/test_nazarov_2015_dhofar_silicides_extract.py` and `tests/test_yaml_flow_scalar_commas.py`: 21 passed (`-n 0`). The focused test pins three migrated SiO pressures against the page 5 render, checks temperature and point conditions on all 33 table points, and checks the Equation (1) coefficients and page locator. Its mutation changes both `value` and the migrator-read `p_atm` for the 1933 K SiO point in a temporary YAML file; re-migration makes the render pin raise as expected.
- Expected store delta after the controller regenerates: +1 work, +1 Mo-cell bench, +1 synthetic-anorthite KEMS experiment, +33 partial-pressure point observations (including five measured-reduced O* and five O₂** points), +1 Equation (1) relation row, and 3 context records. Table 1 and Mo-oxide context add no scored observations. No battery-store regeneration was run.

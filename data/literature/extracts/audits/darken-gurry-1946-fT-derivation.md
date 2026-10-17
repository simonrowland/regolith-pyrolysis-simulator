# Darken & Gurry (1946): extraction audit and f(T) derivation

Source: Darken and Gurry, “The System Iron-Oxygen. II. Equilibrium and Thermodynamics of Liquid Oxide and Other Phases,” *JACS* 68 (1946), 798–816, [doi:10.1021/ja01209a030](https://doi.org/10.1021/ja01209a030). The scan used for the transcription is `darken-gurry-1946-iron-oxygen-ii.pdf`; page references below give PDF page followed by printed journal page.

## Part A — extraction audit

The relevant printed tables were checked cell by cell against 300 dpi renders. The Table I L3 temperature was rechecked at 400 dpi and corrected to 1596 °C; its `r = 4.067` is unchanged. The extract stores source columns and their printed signs, units, qualifiers, and footnote marks without adding converted numeric twins. Table I's printed time tokens `5½`, `3¼`, and `4½` are retained verbatim beside their numeric hour values.

| Table | PDF / journal page | Extracted rows | Printed quantities and units |
| --- | --- | ---: | --- |
| I | 3 / 800 | 76 | `r` (dimensionless), temperature (°C), time (h) |
| III A | 6 / 803 | 13 | `r` (dimensionless), temperature (°C), time (h) |
| III B | 6 / 803 | 3 | `pCO2/pCO` (dimensionless), temperature (°C) |
| IV A | 8 / 805 | 6 | `log pO2` (atm), `log(pCO2/pCO)`, temperature (°C) |
| IV B | 8 / 805 | 6 | `log pO2` (atm), `log(pCO2/pCO)`, temperature (°C) |
| V | 10 / 807 | 10 | Partial molar heat, cal per gram-atom oxygen (two five-row blocks) |
| VI | 11 / 808 | 25 | `N2/N1`, `log(pCO2/pCO)`, and the printed negative-log `pO2`, `a1`, and `aFeO` columns; stored as measured oxygen-potential rows plus a linked derived-activity observation |
| VII | 12 / 809 | 6 | `N2/N1`, `log K_FeO` |
| VIII | 14 / 811 | 6 | `log K_Fe3O4`, `log(pCO2/pCO)`, `log a1` |
| IX | 14 / 811 | 6 | Heat quantities in kcal as printed; fusion heat per gram-atom Fe and per gram-atom O |
| X | 15 / 812 | 6 | Entropy-transfer values; the table does not state a unit |

Table II is not liquid-oxide data; Table IV C is the magnetite–hematite equilibrium and is outside the requested liquid-oxide field. They were not copied into this FeO liquid extract. Table I’s printed `r = (Fe++ + Fe+++)/Fe++` has no matching quantity token in the current extract schema; it remains verbatim and is typed as unavailable rather than assigned a new schema value. Table IX does not state a temperature, so each row carries an unknown temperature condition with its table locator.

The render audit resolved the OCR error in Table IV A row 6: the printed initial gas ratio is **13.62** (not 13.02). The first Table VI iron–liquid row is marked as iron–liquid equilibrium; signs in all negative-log columns were read from the render. Six render-pinned cells are tested with page locators in `tests/test_darken_gurry_1946_extract.py`, including Table I L3 at 1596 °C and Table VI's 1500 °C first composition `1.0225` (without a superscript).

An isolated `Migrator` run emitted 181 observations from 163 printed rows, with zero hard issues. Table VI's 22 mappable measured rows and 22 linked activity rows are separate emissions. All 181 carry liquid phase; all 89 Table I and III A rows carry the printed ratio as a located point composition. Six Table IX rows correctly retain temperature as unknown. Four source rows remain in the extract without numeric observations: Table IV A row 1 has no gas-potential values, Table VI rows 1400-6 and 1400-7 are parenthesized values outside liquid stability, and Table VI row 1600-1 retains its superscript `c` tokens.

Migrated evidence classes by table are:

| Table | Migrated class counts |
| --- | --- |
| I | 76 `measured_tabulated` |
| III A | 13 `measured_tabulated` |
| III B | 3 `measured_tabulated` |
| IV A | 5 `measured_tabulated` |
| IV B | 6 `measured_tabulated` |
| V | 10 `model_derived` |
| VI | 22 `measured_tabulated`; 22 `model_derived` |
| VII | 6 `quoted_attributed` |
| VIII | 6 `quoted_attributed` |
| IX | 6 `quoted_attributed` |
| X | 6 `quoted_attributed` |

Tables V and VI name the stored Table VI oxygen-potential observation as their derivation input. Tables VII–X remain `quoted_attributed` with the D&G attribution because their underlying calculation inputs are not stored as linked rows in this extract; no parent observations were invented.

`tools/mineru_table_draft.py compare` was run against the MinerU draft index. Its output was:

```text
species,observation_id,point_index,pdf_page,value,status,draft_cells,matched_cell,path
compare: matched=0 no-match=0 mismatch=0 total=0
```

The comparator only considers generic numeric `values.points[].value` fields. This extraction preserves the printed multi-column table cells under their named source columns, so there are no comparator-eligible values. This zero-row result is not evidence of cell agreement; the cell audit above is the render comparison.

The requested search for a separate Darken & Gurry (1945) extract found none. The only extract hit is Morey (1964), whose Table 10 context cites D&G (1945, 1946) but is not a source-specific D&G extract. The expected corpus and legacy corpus locations contain no local 1945 source PDF, so the pair `1.792/1.363` supplied for the comparison below has not been independently mapped to a temperature or liquid composition.

## Part B — Gibbs–Duhem derivation

### Premise and integral

Let `x = N2/N1 = NO/NFe`, the oxygen-to-iron atomic ratio used by D&G, and let `pO2` be the equilibrium oxygen partial pressure. For the Fe–O liquid, Gibbs–Duhem gives

\[
N_{Fe}\,d\mu_{Fe}+N_O\,d\mu_O=0,
\qquad d\mu_{FeO}=d\mu_{Fe}+d\mu_O=(1-x)\,d\mu_O.
\]

At gas–liquid equilibrium, `dμO = (RT/2) d ln(pO2 / 1 atm)`. Therefore

\[
\ln f(T)=\ln\!\frac{a_{FeO}(x=1)}{a_{FeO}(x_b)}
=\frac12\int_{1}^{x_b}(x-1)\,d\ln(p_{O_2}/1\,\mathrm{atm}),
\]

where `xb` is the first Table VI composition reported in equilibrium with metallic iron. This uses only D&G’s measured oxygen potential as a function of `N2/N1`. The logarithm argument is dimensionless; `x` is dimensionless, and `RT` cancels from the ratio.

### Numerical method and sensitivity

D&G tabulate base-10 **negative** logarithms of `pO2`, so the integration used `log10(pO2) = -(-log10(pO2))`. For each isotherm, the slope between the iron-boundary row and the next composition row was linearly extrapolated back to `x = 1`. The integral was evaluated by trapezoids on a uniform `Δx = 10^-4` grid, including the final shortened step. Because the extrapolated `log pO2(x)` is linear, trapezoids are exact for this model: the largest difference from the analytic integral was `2×10^-18` in `ln f`.

The source does not report measurement uncertainties. As a local-model sensitivity check, replacing the first two-point slope by the chord from the iron-boundary row to the third composition row changes `ln f` by 0.005915, 0.000841, and 0.000168 at 1400, 1500, and 1600 °C, respectively. This is an extrapolation sensitivity, not a statistical uncertainty; the experiment provides no point at `x = 1`, and input rounding is not included in the quadrature error.

| D&G temperature | `xb` | First two rows: `d log10(pO2)/dx` | `ln f` | `f` | Wider-chord sensitivity in `ln f` |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1400 °C | 1.034 | 36.6667 | 0.024400 | 1.024700 | 0.005915 |
| 1500 °C | 1.0225 | 28.5714 | 0.008326 | 1.008361 | 0.000841 |
| 1600 °C | 1.0125 | 26.9091 | 0.002420 | 1.002423 | 0.000168 |

As `xb` approaches 1, the interval shrinks and the integrand contains `(x - 1)`, so `ln f` approaches zero and `f` approaches 1. D&G report liquid-oxide measurements through 1640 °C; 1700 °C is outside their measured range, so there is no D&G Gibbs–Duhem value at 1700 °C.

## Cross-check: NASA Glenn versus JANAF solid wüstite

The NASA Glenn labels at `thermo.inp` lines 12239 and 12244 say `Fe.947O(cr)` and `Fe.947O(L)`, but the corresponding composition fields encode **Fe0.95O** (`n_Fe = 0.95`, molecular weight 69.05215), while JANAF Fe-001 is **Fe0.947O**. The reproduced NASA columns below use the NASA composition field, `n_Fe = 0.95`. NASA’s source is the [NASA Glenn coefficient database](https://github.com/nasa/cea/blob/main/data/thermo.inp) (documented in [NASA TP 2002-211556](https://ntrs.nasa.gov/citations/20020085330)); JANAF sources are [Fe-001](https://janaf.nist.gov/tables/Fe-001.html) and [Fe-019](https://janaf.nist.gov/tables/Fe-019.html). The published NASA label and its composition field therefore identify different stoichiometries.

The table reports residuals `NASA − JANAF`, in kJ mol`^-1`, for the `G` function and `ΔfG`. NASA’s solid segment covers 298.15–1652 K. JANAF Fe-001 is tabulated at 100 K intervals, so the 1650 K comparison linearly interpolates JANAF between 1600 and 1700 K. The corrected polynomial `G` residual is +0.00088 kJ mol`^-1` at 1000 K and +0.09934 kJ mol`^-1` at 1600 K. The physics ruling d-164 treats `f_GD` and `f_J` as different standard states; the residuals below are a thermochemical cross-check and do not equate those routes.

| T (K) | `G` residual | `ΔfG` residual |
| ---: | ---: | ---: |
| 1000 | +0.00088 | +0.12746 |
| 1100 | +0.00813 | +0.17523 |
| 1200 | +0.02334 | +0.25358 |
| 1300 | +0.04208 | +0.32543 |
| 1400 | +0.06361 | +0.39860 |
| 1500 | +0.08277 | +0.47002 |
| 1600 | +0.09934 | +0.53884 |
| 1650 | +0.08427 | +0.56942 |

The maximum absolute residuals in the overlap are 0.09934 kJ mol`^-1` in `G` and 0.56942 kJ mol`^-1` in `ΔfG`. The JANAF-anchored liquid candidate is

\[
\ln f_J=\frac{\Delta_fG(\mathrm{Fe}_{0.95}\mathrm O,L;NASA)-\Delta_fG(\mathrm{FeO},l;JANAF)}{RT},
\]

with `n_Fe = 0.95` from the NASA composition field. It is reported as its own JANAF-anchored standard state, not as the D&G Gibbs–Duhem ratio. The NASA liquid segment begins at 1652 K; JANAF Fe-019 supplies the FeO(l) reference. The listed `ΔfG` residual is at most 0.56942 kJ mol`^-1`. No uncertainty interval is printed for the NASA coefficient record, and JANAF Fe-001 is a machine-transcribed compilation. I report the NASA/JANAF residuals without claiming a pass/fail tolerance for the composition-mismatched solids. Under d-164, `f_J` remains a separate route candidate rather than a numerical substitute for `f_GD`.

## Boundary-composition correction

D&G (1945) Table III A gives the iron-boundary liquid near Fe0.969O at 1400 °C. In D&G’s `x = NO/NFe`, this is `x = 1/0.969 = 1.03199174`; Fe0.947O is `x = 1/0.947 = 1.05596621`. Using the 1400 °C Table VI oxygen-potential curve, linearly extrapolating the first segment to `x = 1.03199174` and interpolating the measured segments through `x = 1.07`, gives

\[
\ln f_{0.947}-\ln f_{0.969}=0.03510118,
\qquad f_{0.947}/f_{0.969}=1.03572450.
\]

Thus using Fe0.947O in place of the Fe0.969O iron-boundary liquid overstates `ln f` by 0.03510 (3.57% in the ratio); translating a Fe0.947O result to Fe0.969O multiplies it by 0.96551. This correction quantifies that composition approximation only. It does not join `f_GD` to `f_J`; the NASA liquid composition is Fe0.95O, and no additional Fe0.969-to-Fe0.95 correction is claimed here.

## Route comparison

The table keeps source-derived, quoted, and excluded routes visible without selecting by numerical agreement. `f_GD` and `f_J` have distinct headings because they use different standard states. The 1945 pair is repeated as unassigned evidence, not treated as either `f`.

| T (°C) | D&G 1946 `ln f_GD`; `f_GD = a(x=1)/a(x_b)` within the D&G melt | JANAF-anchored candidate `ln f_J`; `f_J` | D&G 1945 gas-ratio pair | Holzheid/Barin quote | Masson–Temkin quote |
| ---: | ---: | ---: | --- | --- | --- |
| 1400 | 0.024400 (1.024700) | 0.671341 (1.956859) | 1.792 / 1.363; T/composition mapping unresolved | 1.94 (quoted at 1400 °C) | 0.936; T/reference not specified, excluded |
| 1500 | 0.008326 (1.008361) | 0.621170 (1.861104) | 1.792 / 1.363; T/composition mapping unresolved | — | 0.936; T/reference not specified, excluded |
| 1600 | 0.002420 (1.002423) | 0.572328 (1.772388) | 1.792 / 1.363; T/composition mapping unresolved | — | 0.936; T/reference not specified, excluded |
| 1700 | Outside D&G data | 0.529406 (1.697923) | 1.792 / 1.363; T/composition mapping unresolved | — | 0.936; T/reference not specified, excluded |

The 1945, Holzheid/Barin, and Masson–Temkin values are reproduced as supplied for this task; the 1945 PDF is not present in the expected local corpus locations, and these quotations have not been independently rederived here. The 1945 gas-ratio pair remains unassigned to a temperature or composition.

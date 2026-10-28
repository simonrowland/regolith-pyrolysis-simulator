# Witthohn et al. (2000), numerical evidence audit

The MinerU table inventory and QA found one printed table: Table 1 on absolute
PDF page 15, with seven sample columns (A-G) and 13 composition rows. The
sample compositions are retained in seven unscored context rows. The
Table 1 row/column layout was checked against the page render; MinerU and
pdftotext agree on 99.0% of numeric cells. The flagged D / CO2 cell reads
`3)-` in the machine text; the render shows the superscript footnote 3 followed
by a dash, and footnote 3 says “not analyzed.” All other stored numeric cells
have matching machine reads, with the rendered table confirming row and sample
assignments.

Table 1 cell audit. The (a) mark means MinerU and pdftotext read the same cell;
the render confirmed the row and sample-column layout. The D / CO2 cell is (b),
settled from the render and footnote. Percent values are retained as printed.

| PDF page / row | A | B | C | D | E | F | G |
|---|---:|---:|---:|---:|---:|---:|---:|
| 15 / temperature (a) | 450 °C | 450 °C | 450 °C | 550 °C | 575 °C | ≈1500 °C | ≈1400 °C |
| 15 / ash content (a) | ad 9.6 | ad 12.6 | ad 9.9 | ad 6.2 | ad 8.4 | – | – |
| 15 / SiO2 (a) | 42.4 | 46.7 | 36.4 | 12.7 | 38.3 | 42.0 | 40.9 |
| 15 / Al2O3 (a) | 2.8 | 3.1 | 4.2 | 2.1 | 18.3 | 24.3 | 24.3 |
| 15 / Fe2O3 (a) | 8.9 | 7.0 | 10.2 | 11.1 | 11.4 | 14.9 | 11.2 |
| 15 / CaO (a) | 19.3 | 15.5 | 17.4 | 38.1 | 8.5 | 9.4 | 9.7 |
| 15 / MgO (a) | 3.0 | 4.3 | 7.6 | 3.5 | 2.8 | 2.6 | 3.0 |
| 15 / Na2O (a) | <0.1 | 0.5 | 1.5 | 0.3 | 1.1 | 1.2 | 1.5 |
| 15 / K2O (a) | 0.3 | 0.4 | 0.5 | 0.1 | 1.7 | 1.7 | 1.7 |
| 15 / SO3 (a) | 12.5 | 15.0 | 11.6 | 14.4 | 9.2 | <0.1 | <0.1 |
| 15 / P2O5 (a) | <0.1 | <0.1 | <0.1 | 0.1 | 0.2 | 0.3 | 0.3 |
| 15 / CO2 | 5.9 (a) | 3.0 (a) | 5.1 (a) | not analyzed (b) | 12.5 (a) | <0.1 (a) | <0.1 (a) |
| 15 / Cl (a) | 0.3 | 0.1 | 0.3 | 0.5 | <0.1 | – | – |
| 15 / Rest (a) | 1.8 | 0.8 | 1.2 | 0.5 | 2.8 | 1.4 | 2.1 |
| 15 / Sum (a) | 97.2 | 96.4 | 96.0 | 83.4 | 106.8 | 97.8 | 94.7 |

| Target evidence | Locator | Machine-readable numeric cells | Disposition |
|---|---|---:|---|
| Na(g), samples A-D | Fig. 2, p. 16 | 0 | Typed refusal; points appear only on a logarithmic plot |
| K(g), samples A-D | Fig. 2, p. 16 | 0 | Typed refusal; points appear only on a logarithmic plot |
| Na(g), samples E-G | Fig. 3, p. 17 | 0 | Typed refusal; points appear only on a logarithmic plot |
| K(g), samples E-G | Fig. 3, p. 17 | 0 | Typed refusal; points appear only on a logarithmic plot |
| Na2O activity, quasi-binary | Fig. 4, p. 17 | 0 | Typed refusal; plotted experimental points attributed to prior work |
| Na2O activity, quasi-ternary | Fig. 5, p. 18 | 0 | Typed refusal; plotted experimental points attributed to Neudorf and Elliott (1980) |
| K2O activity | Fig. 6, p. 18 | 0 | Typed refusal; calculated curves only, no measured K2O activity points |

The pressure method is recorded in source context: the text (p. 5) identifies
Knudsen effusion mass spectrometry and reduces detector signal using isotopic
abundance, ionization cross-section, temperature and a calibration constant
determined with pure silver. The four Na(g)/K(g) candidate observations carry
`measured_reduced` evidence; their raw detector-signal rows are not printed,
and the plotted point values are excluded by the figure rule.
The activity standard state is also preserved in context from p. 9:
`a_i = p_i/p_i0`, with `p_i0` the vapour pressure of the pure component.
Figures 4-5 reuse measurements attributed to Neudorf and Elliott (1980),
Yamaguchi et al. (1982), Rego et al. (1985), and Tsukihashi and Sano (1985);
the caption and text identify emf, transpiration, and chemical-equilibration
methods. The paper does not tabulate those points. Figure 6 is identified in
the text as FACT calculation output (FACT 2.1, modified quasi-chemical model,
ChemSage delta-G minimiser).

No render digitization was performed: wave rules exclude figure-read values.
No numeric pressure or activity observations are admitted. An isolated
migration produced seven unavailable/rejected target observations, nine
context rows (seven Table 1 samples and two method/reference facts), and zero
hard issues. Focused checks pin three Table 1
cells to the render: sample A SiO2 = 42.4%, sample C K2O = 0.5%, and sample G
CaO = 9.7%. No experiment total pressure or fO2 was promoted from general
apparatus context, and Table 1 sample characterization was not placed into
measurement identity conditions. Expected unregenerated store delta: +7
rejected observations, +9 context rows, +1 work, +1 experiment, and +0 benches.
No battery-store regeneration is committed.

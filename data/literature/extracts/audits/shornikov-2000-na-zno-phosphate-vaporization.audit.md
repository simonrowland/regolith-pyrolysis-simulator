# Shornikov & Archakov 2000 vapour audit

Source: `shornikov-2000-na-zno-phosphate-vaporization`, 8 PDF pages. The paper
contains no numbered data tables. MinerU's content-list conversion found **0
tables**; MinerU and the tesseract OCR were used as the two machine reads. The
source `pdftotext` output is empty because the PDF has no text layer.

## Eligible observations

**Printed Na(g) pressure/flux rows: 0.** On PDF page 2 (published page 39), the
authors state that partial pressures for Na and other vapour species were
determined using the Hertz–Knudsen equation with partial evaporation
coefficients. The text gives no numerical Na pressure, flux, or rowwise
composition/temperature values. The study-wide 950–1550 K range is printed in
the introduction (PDF page 1), but it does not define point temperatures.

The reported procedure is high-temperature mass-spectrometric Knudsen
effusion. The samples were measured in molybdenum effusion cells with an
evaporation-to-effusion-square ratio greater than 500 using a modified MI 1201T
mass spectrometer (PDF pages 1–2). On PDF page 2, the authors say partial
pressures for PO, PO2, P2, Zn, Na, and NaPO3 were determined by the
Hertz–Knudsen equation with partial evaporation coefficients. They also report
comparison-method agreement using ionization cross-sections and Ag and Zn
partial-pressure standards. These statements are retained as typed context,
not numerical Na vapour observations.

Figures 1–3 were reviewed against renders of PDF pages 3 and 6. None plots
Na(g) partial pressure or evaporation flux, but each carries scorable data on
an owner rail and is retained as a figure-read candidate. Values remain
undigitised.

| Figure / page | Rail | Axes and units | Standard/reference state | Series and temperature |
|---|---|---|---|---|
| 1(c), p. 60 | `melt_activity` | x(ZnO), mole %; y(log a_i), dimensionless | Pure oxide components forming the ternary system | 1 Na2O, 2 ZnO, 3 P2O5; present study at 1100 K. Figure 1(d) is prior SnO-system data at 1300 K and is not included in this source's candidate. |
| 2, p. 63 | `residue_composition` | x(Na2O), mole %; y(x(ZnO)), mole % | Not applicable to composition coordinates | 1 Na2O·ZnO·P2O5; 2 4Na2O·6ZnO·5P2O5. Temperature is not stated in the figure or caption. |
| 3, p. 63 | `thermochemistry` | x(ZnO), mole %; y(ΔG_T/RT), dimensionless | Text names the pure oxide components as standard states | 1 Na4P2O7–Zn2P2O7, present study at 1100 K; 2 Sn2P2O7–Zn2P2O7, prior data at 1300 K. |

The extraction and figure-inventory context row each carry these three
candidates. The numerical figure-only values represented by MinerU `<details>`
blocks are excluded. The renders confirm axes, legends, and locators; no plotted
values were transcribed. No printed pressure/flux table cells exist to render
check.

## Cell audit and row count

There is no printed pressure/flux table to reconcile; printed eligible row
count is **0**. The MinerU table audit summary is 4 content lists, 0 tables, 0
flagged tables, and 0 scan tables. Thus there are no cells marked (a) or (b).
The figure-only observations are excluded under the extraction rule.

## Migration

The context-only extract has zero observation rows. Its focused regression
test validates the extract, migrates it in isolation, pins zero observations
and zero hard issues, and checks the three figure candidates and their rails.
Expected store delta: one work and three unscored context rows; no
observations. No store regeneration is committed.

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

Figures 1–3 were reviewed from their captions and machine text. Figure 1 (PDF
page 3, published p. 60) shows phase diagrams and pure-oxide activities; its
present-study activity panel is at 1100 K and the caption's series are Na2O,
ZnO, P2O5, and SnO in the comparison panel. Figure 2 (PDF page 6, published
p. 63) shows changes in initial melt composition during evaporation, with
series Na2O·ZnO·P2O5 and 4Na2O·6ZnO·5P2O5. Figure 3 (PDF page 6, published
p. 63) shows Gibbs energies of formation for Na4P2O7–Zn2P2O7 and
Sn2P2O7–Zn2P2O7 melts, with the present study at 1100 K and earlier data at
1300 K. None of the three figures plots Na(g) partial pressure or evaporation
flux, so the explicit figure-read-candidate list is empty. The numerical
figure-only values represented by MinerU `<details>` blocks are excluded. No
page render was needed: there were no eligible numeric pressure/flux cells or
machine-read numeric tables to reconcile.

## Cell audit and row count

There is no printed pressure/flux table to reconcile; printed eligible row
count is **0**. The MinerU table audit summary is 4 content lists, 0 tables, 0
flagged tables, and 0 scan tables. Thus there are no cells marked (a) or (b).
The figure-only observations are excluded under the extraction rule.

## Migration

The context-only extract has zero observation rows. Its focused regression
test validates the extract, migrates it in isolation, pins zero observations
and zero hard issues, and checks that the explicit empty figure-candidate list
is present. Expected store delta: one work and three unscored context rows; no
observations. No store regeneration is committed.

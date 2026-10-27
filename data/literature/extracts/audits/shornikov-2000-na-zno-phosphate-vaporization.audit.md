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
mass spectrometer (PDF pages 1–2). These are method details, not numerical Na
vapour observations.

Figures 1–3 show phase diagrams and oxide activities, composition changes
during evaporation, and Gibbs energies. MinerU places invented `<details>`
tables below figures 1–3; those values are excluded. The figure data are
figure-read candidates only and were not transcribed. No page render was needed:
there were no eligible numeric pressure/flux cells or machine-read numeric
tables to reconcile.

## Cell audit and row count

There is no printed pressure/flux table to reconcile; printed eligible row
count is **0**. The MinerU table audit summary is 4 content lists, 0 tables, 0
flagged tables, and 0 scan tables. Thus there are no cells marked (a) or (b).
The figure-only observations are excluded under the extraction rule.

## Migration and focused-test status

No extract rows were emitted because no eligible numerical observations are
printed. The isolated migration and the focused test requiring three stored
numeric values cannot be completed without either inventing values or using
figure-read values that the extraction rules explicitly exclude. Controller
guidance is requested on whether a zero-row audit is sufficient for this
source.

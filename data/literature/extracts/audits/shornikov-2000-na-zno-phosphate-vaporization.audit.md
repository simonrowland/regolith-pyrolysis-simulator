# Shornikov & Archakov 2000 vapour audit

Source: `shornikov-2000-na-zno-phosphate-vaporization`, 8 PDF pages. The paper contains no numbered data tables. MinerU's content-list conversion found **0 tables**; MinerU and the tesseract OCR were used as the two machine reads. The source `pdftotext` output is empty because the PDF has no text layer.

## Eligible observations

**Printed Na(g) pressure/flux rows: 0.** On PDF page 2 (published page 59), the authors state that partial pressures for Na and other vapour species were determined using the Hertz–Knudsen equation with partial evaporation coefficients. The text gives no numerical Na pressure, flux, or rowwise composition/temperature values. The study-wide 950–1550 K range is printed in the introduction (PDF page 1), but it does not define point temperatures.

The reported procedure is high-temperature mass-spectrometric Knudsen effusion. The samples were measured in molybdenum effusion cells with an evaporation-to-effusion-square ratio greater than 500 using a modified MI 1201T mass spectrometer (PDF pages 1–2). On PDF page 2, the authors say partial pressures for PO, PO2, P2, Zn, Na, and NaPO3 were determined by the Hertz–Knudsen equation with partial evaporation coefficients. They also report comparison-method agreement using ionization cross-sections and Ag and Zn partial-pressure standards. These statements are retained as typed context, not numerical Na vapour observations.

Figures 1–3 contain plotted context on the melt-activity, residue-composition, and thermochemistry rails. All 35 digitised points remain context-only, so the extract still has zero observations. The extraction and figure-inventory context rows list each candidate as read and verified.

| Figure / page | Points | Axes and units | Standard/reference state | Series and temperature |
|---|---:|---|---|---|
| 1(c), p. 60 | 8 | x(ZnO), mole %; y(log a_i(i)) | Pure oxides forming the ternary system | 1 Na2O, 2 ZnO, 3 P2O5; present study at 1100 K |
| 2, p. 63 | 18 | x(Na2O), mole %; y(x(ZnO)), mole % | Not applicable to composition coordinates | 1 Na2O·ZnO·P2O5; 2 4Na2O·6ZnO·5P2O5; no temperature printed |
| 3, p. 63 | 9 | x(ZnO), mole %; y(ΔG_T/RT) | Text names pure oxide components as standard states | 1 Na4P2O7–Zn2P2O7, present study at 1100 K; 2 Sn2P2O7–Zn2P2O7, prior data at 1300 K |

## Digitisation verification

Two independent calibrated reads were compared. All matched points agree within 2× the combined uncertainty; the largest normalized discrepancy is 0.066. The independent pass identified two missed Fig. 2 series-1 symbols; both were reread from a fresh 600 dpi render and agree with the blind read within 2× the combined uncertainty. Coordinates and x/y uncertainties in the extract are copied from `points-v2.tsv`; uncertainty is digitisation uncertainty (tick-fit residual plus symbol half-width), not reported experimental uncertainty. Pixel centres use the 600 dpi page render, origin at top-left, with row increasing downward.

The verification records document two issues in the earlier read/report: its TSV omitted those two Fig. 2 points, and its report contained an incorrect total and an incorrect claim that an overlapping Fig. 2 locus was not emitted. The reconciled 35-row TSV includes both added symbols and retains the two distinct open rings.

## Migration and regression evidence

The focused regression validates the extract and migrates it in isolation: **0 observations, 0 hard issues, 38 context rows** (3 existing context rows plus 35 figure points). It pins one exact x/y point from each figure and checks point counts of 8, 18, and 9. No schema change or store regeneration is included.

Expected store delta: one work, 38 unscored context rows, and zero observations. No committed battery-store regeneration.

Source and verification records:

- `/Users/simonrowland/Repos/regolith-pyrolysis-simulator/docs-private/research/2026-10-26-scorability/shornikov-2000-figs/points-v2.tsv`
- `/Users/simonrowland/Repos/regolith-pyrolysis-simulator/docs-private/research/2026-10-26-scorability/shornikov-2000-figs/verify-review.md`
- `/Users/simonrowland/Repos/regolith-pyrolysis-simulator/docs-private/research/2026-10-26-scorability/shornikov-2000-figs/reconcile.md`
- `/Users/simonrowland/Repos/regolith-pyrolysis-simulator/docs-private/research/2026-10-26-scorability/shornikov-2000-figs/calibration.md`

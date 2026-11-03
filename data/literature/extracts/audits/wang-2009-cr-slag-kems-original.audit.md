# Wang et al. (2009) KEMS extraction audit — WIP

Status: paused for the composition ruling below. No extract rows have been committed.

## Machine-text and table audit

Transcription followed the MinerU table blocks and markdown, then the born-digital pdftotext layer. The table QA pre-screen found 100% numeric-cell agreement for all five MinerU tables and no dropped-plusminus hits. Table 4 had a LaTeX-format flag. Render checks of PDF pages 4 and 5 confirmed the table row and column assignments, including all 20 rows in Tables 3, 4, and 5; Table 4's draft parser counted one header line as an extra body row.

| Printed table | PDF page | Printed body rows | Numeric cells in QA | QA agreement | Render/layout check |
| --- | ---: | ---: | ---: | ---: | --- |
| 1 | 2 | 20 | 140 | 1.000 | Machine-text cross-check only; used to identify the conflict below |
| 2 | 3 | 0 | 0 | 1.000 | Not an extraction table |
| 3 | 4 | 20 | 120 | 1.000 | Confirmed on render |
| 4 | 5 | 20 | 120 | 1.000 | Confirmed on render; LaTeX flag resolved |
| 5 | 5 | 20 | 120 | 1.000 | Confirmed on render |

## Unresolved printed composition conflict

Table 4's caption says its rows use the compositions in Table 3, whose caption says the compositions are after complete FeO vaporization and before measuring the activity temperature dependencies. However, Table 4's printed Cr2O3 mole fraction for sample 1 is 0.062, matching Table 1's initial charge, while Table 3 gives 0.066 for sample 1. Table 4 therefore prints a composition value that conflicts with its stated Table 3 composition source. The same choice determines which composition reaches the pressure rows' identity.

Please rule which composition to bind to the Table 4 pressure fits: the post-FeO full composition from Table 3 (as named by the Table 4 caption and surrounding prose), or the Table 1 initial composition reflected by Table 4's own Cr2O3 fraction. Until resolved, no composition has been bound to the pressure fits.

## Scope findings

Table 4 reports CrO and CrO2 pressure-temperature fit coefficients, not individual pressure points. Table 5 reports Cr2O3 activity fit coefficients and activity values at 1950 K and 2035 K. The other species appear as pressure curves only in Figures 1 and 2; those figure-read values are out of scope. The text gives no numerical ion-current intensities. No numeric measurement total pressure or oxygen partial pressure is printed for the table series.

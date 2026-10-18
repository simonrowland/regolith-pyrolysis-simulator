# Shornikov and Yakovlev (2018), abstract 1845: extraction audit

Source: *Thermodynamic Study of Double Inversion of MgO and SiO2 Relative Fugacity at CAIs Evaporation*, 49th Lunar and Planetary Science Conference (2018), LPI Contribution No. 2083, abstract 1845. The source PDF SHA-256 is `913d26a8ac7de1519cb53c27bba1d8092220dc38450cdc3aedb3731542bd6d0f`. No DOI is printed in the paper.

## Table 1

The table has five sample rows and four oxide columns (20 numeric cells). MinerU HTML and `pdftotext-layout.txt` agree on all digits. Because this is a born-digital PDF, the PDF was rendered at 600 dpi once to confirm the row and column assignment. No cell disagreements or hand corrections were needed. The emitted cells were regrouped as one unscored initial-composition context record, preserving each printed value and its row locator.

The required `compare` run reports zero candidates because it compares scored observations and the final extract intentionally keeps this starting-composition table in `context[]`. Its header-only CSV is committed. The separate table-tool emit staged all 20 cells; the source test pins printed values in the context record.

| PDF page / table row | Sample | CaO (mol %) | MgO (mol %) | Al2O3 (mol %) | SiO2 (mol %) | Evidence |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| 1 / 1 | 5aN-L | 11.17 | 25.00 | 23.83 | 40.00 | MinerU + pdftotext; row/column assignment checked on 600 dpi render |
| 1 / 2 | 5aN-3 | 14.43 | 25.60 | 23.83 | 36.14 | MinerU + pdftotext; row/column assignment checked on 600 dpi render |
| 1 / 3 | 5aN-H | 17.03 | 27.98 | 23.23 | 31.76 | MinerU + pdftotext; row/column assignment checked on 600 dpi render |
| 1 / 4 | CAI4 | 24.10 | 24.32 | 15.52 | 36.06 | MinerU + pdftotext; row/column assignment checked on 600 dpi render |
| 1 / 5 | CAI B | 26.76 | 17.05 | 11.41 | 44.78 | MinerU + pdftotext; row/column assignment checked on 600 dpi render |

The prose associates 5aN-3 with references [5, 6], CAI4 with [7], and CAI B with [8]; Table 1 itself does not print row-level citations. The extract marks those rows `quoted_attributed` with the references as printed and records the distinction from citations printed in the table. The authors state that 5aN-L and 5aN-H were studied as low- and high-CaO compositions; no cited source is attached to either row, so their table values are attributed to this work.

## Text-stated results and cited ranges

The extract preserves the study temperature, 2173 K, and the two text-stated 5aN-3 inversion thresholds, about 35 and about 60 mol % Al2O3, as context. It also preserves the introduction's three attributed experimental ranges as printed: 1600–2500 K [1], 1577–2502 K [3], and 1673–2273 K [4]. No values were inferred from plotted curves.

## Figures

Figures 1–5 are recorded under species sibling `context[]` entries. Their captions, printed axes, phase/sample/species identities, temperature, series and visually counted marker counts are in the extract. Every record says `digitisation: deferred to a figure pass`; none contains numeric figure points. Figure 1 is on PDF page 1; Figures 2–5 are on PDF page 2. No figure-only quantity was migrated as a scored observation.

## Migration and expected store delta

The extract contains no scored observations because its only tabulated values are starting compositions and all figure reads are deferred. Expected store delta: 0 observations and 8 context records (one table, five figures, one cited-range record, one text-stated result record). The migration check must report zero hard issues and zero unavailable observations. Temperature-on-every-row does not apply because there are no observations.

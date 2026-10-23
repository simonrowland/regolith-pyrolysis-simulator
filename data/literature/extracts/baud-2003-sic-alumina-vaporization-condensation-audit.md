# Baud et al. (2003), Table 5 cell audit

Table 5 is PDF page 6 (printed page 24). It prints four powder beds and four
gas species, for 16 explicit `β (at 1500 K)` point cells. The page render was
checked after the pdftotext transcription to confirm the row and column
assignments for the complete table. The source tree has no MinerU output for
this paper, so each numeric cell is confirmed by route (b): the rendered PDF
cell. The table contains no total pressure; `knudsen_effusion` records the
stated vaporization reference regime, and no pressure value is supplied.

| Powder bed | Al(g) | Al2O(g) | SiO(g) | CO(g) |
|---|---:|---:|---:|---:|
| SiC (Nor.) + Al2O3 | 0.0129 (PDF p. 6, row 1, col. β(Al), b) | 0.0034 (PDF p. 6, row 1, col. β(Al2O), b) | 0.0106 (PDF p. 6, row 1, col. β(SiO), b) | 0.0311 (PDF p. 6, row 1, col. β(CO), b) |
| SiC (Car.) + Al2O3 | 0.0101 (PDF p. 6, row 2, col. β(Al), b) | 0.0033 (PDF p. 6, row 2, col. β(Al2O), b) | 0.0451 (PDF p. 6, row 2, col. β(SiO), b) | 0.0249 (PDF p. 6, row 2, col. β(CO), b) |
| SiC (Nor.) + Al2O3 + C | 0.0127 (PDF p. 6, row 3, col. β(Al), b) | 0.004 (PDF p. 6, row 3, col. β(Al2O), b) | 0.022 (PDF p. 6, row 3, col. β(SiO), b) | 0.0211 (PDF p. 6, row 3, col. β(CO), b) |
| SiC (Nor.) + Al2O3 + Si | 0.0281 (PDF p. 6, row 4, col. β(Al), b) | 0.0219 (PDF p. 6, row 4, col. β(Al2O), b) | 0.0138 (PDF p. 6, row 4, col. β(SiO), b) | 0.113 (PDF p. 6, row 4, col. β(CO), b) |

Printed row count: 16 coefficient points. The mean and fitted rows in each bed
are excluded. The input cell values match the pdftotext text layer, and the
render confirms their species/bed placement. The MinerU draft/compare tool
could not run because this seat has no MinerU artifact for this source.

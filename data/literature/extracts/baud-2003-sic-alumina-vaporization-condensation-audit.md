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

The derivation relations quote the following printed `ln β (fit)` cells. Each
fit cell is confirmed by the PDF page 6 render (b), and remains derivation
metadata rather than an additional observation.

| Powder bed | ln β fit: Al(g) | ln β fit: Al2O(g) | ln β fit: SiO(g) | ln β fit: CO(g) |
|---|---|---|---|---|
| SiC (Nor.) + Al2O3 | 0.989 − 8009/T (row 1, col. ln β fit) | 5.663 − 17 027/T (row 1, col. ln β fit) | −4.733 + 274/T (row 1, col. ln β fit) | −6.032 + 3844/T (row 1, col. ln β fit) |
| SiC (Car.) + Al2O3 | 1.633 − 9341/T (row 2, col. ln β fit) | 3.375 − 13656/T (row 2, col. ln β fit) | 1.631 − 7095/T (row 2, col. ln β fit) | −16.118 + 18 635/T (row 2, col. ln β fit) |
| SiC (Nor.) + Al2O3 + C | 1.915 − 9416/T (row 3, col. ln β fit) | 7.412 − 19 403/T (row 3, col. ln β fit) | −13.122 + 13961/T (row 3, col. ln β fit) | −7.824 + 5949/T (row 3, col. ln β fit) |
| SiC (Nor.) + Al2O3 + Si | −7.601 + 6042/T (row 4, col. ln β fit) | −10.414 + 9888/T (row 4, col. ln β fit) | −21.64 + 26 039/T (row 4, col. ln β fit) | −8.112 + 8900/T (row 4, col. ln β fit) |

Printed count: 16 explicit β points. The mean rows are excluded as observations;
the fitted rows are retained only as the printed derivation relation for each
1500 K point. The input cell values match the pdftotext text layer, and the
render confirms each value and fit's bed/species placement. The MinerU
draft/compare tool could not run because this seat has no MinerU artifact for
this source.

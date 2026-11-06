# King (1954) extraction audit

| Table/page and cells | Independent reads | Render check | Result |
|---|---|---|---|
| Table I, p. 5849, CaFe2O4 left pair, 15 rows | MinerU HTML and pdftotext digits agree | p. 5849 render: row/column alignment; comma/dot flags 52.99 and 94.62 checked | 30 cells |
| Table I, p. 5849, CaFe2O4 middle pair, 15 rows | MinerU HTML and pdftotext digits agree | p. 5849 render: paired layout checked | 30 cells |
| Table I, p. 5849, CaFe2O4 right pair, 14 rows | MinerU HTML and pdftotext digits agree | p. 5849 render: parenthesized final extrapolation checked | 28 cells |
| Table I, p. 5849, Ca2Fe2O5, 10 rows × 3 pairs | MinerU HTML and pdftotext digits agree | p. 5849 render: row/column alignment checked | 60 cells |
| Table II, p. 5850, calcium entropy columns | MinerU table block flags LaTeX/numeric mismatches; pdftotext values | p. 5850 render checked 1.16, 33.58, 34.7 ± 0.2; 2.50, 42.61, 45.1 ± 0.3 | 6 cells |

Row reconciliation: 44 CaFe2O4 and 30 Ca2Fe2O5 Table I rows; all included. Table II has two calcium entropy summaries. Entropy results are measured_reduced by the authors’ integration/extrapolation. Experimental pressure is not printed and is absent.

The MinerU compare output records 72 matches and two mismatches: the parenthesized Table I Cp entries at 298.16 K. The render confirms those printed cells as `(36.72)` and `(46.10)`; the extract keeps their numeric magnitudes for migration and preserves the printed parentheses in `printed_value`. No other table-cell mismatch remains.

Review fix round: all four observations use `per: mol_species`; only the two parenthesized 298.16 K Cp points set `approximate: true`; the 296.08 K and 296.09 K neighbors remain non-approximate. The Ca2Fe2O5 extrapolated entropy component retains the printed digits as the string `"2.50"`. The focused test pins per-basis, approximate flags, and the quoted cell. `tests/test_literature_extract_king_1954_calcium_ferrites.py` passes, as do the three review-selected cases from `tests/test_literature_extracts.py` (repo validation, fidelity coverage, and this source's fidelity sample). The full `tests/test_literature_extracts.py` suite remains UNVERIFIED-local after its earlier run was interrupted at 159 passed beyond the 300-second cap.

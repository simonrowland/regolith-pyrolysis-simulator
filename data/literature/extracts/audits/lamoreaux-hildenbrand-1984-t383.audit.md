# Lamoreaux and Hildenbrand (1984), Part I: K2O audit

The existing 1984 extract was extended after the duplicate check and controller steer seq 5. The supplied corpus excerpt covers printed pages 153–157 only; it is not the complete 151–173 review. Its Tables 1–4 cover alkali oxides. K2O is the only requested addition present in this excerpt.

## Table coverage and cell audit

| Printed table/page | Printed row count or selected count | Extracted K2O rows | Cell verification |
|---|---:|---:|---|
| Table 1, p. 153 (PDF p. 1) | 16 data rows; 1 K2O row | 1 | Row 8, columns 2–3: 1190 ± 50 K and 4.8 ± 1.0 kK. Render checked because MinerU classified the body as headers; pdftotext agrees. |
| Table 2, p. 154 (PDF p. 2) | 3 K2O phase rows, 7 polynomial segments | 3 | Rows 35–41, coefficient columns 2–5 and range column 6. MinerU and pdftotext agree on the stored cells except row 36, column 3 (5.772 vs 5.172) and row 40, column 3 (7.039 vs 1.039); both differences checked against the render, which agrees with MinerU. Species and segment assignments checked against the rendered layout. |
| Table 3, p. 155 (PDF p. 3) | 3 K2O phase rows, 6 polynomial segments | 3 | Rows 79–84, coefficient columns 2–5 and range column 6. MinerU and pdftotext agree on all stored digits and signs; row/column assignments checked against the rendered layout. |
| Table 4, p. 157 (PDF p. 5) | 2 K2O rows | 2 | Rows 3–4, columns 2–4. Render checked for each value and uncertainty; pdftotext and MinerU agree. K2O(s) column 4 is blank and was not stored. |

Numeric cells in the Table 2 and Table 3 row/column ranges above are individually represented in the extract's segment coefficients and bounds; the ranges state where each cell was audited. The emitted table fragments preserve the draft's per-cell readout. The final extract groups coefficient cells into the printed equation segments, which `emit` cannot assemble. Table 1 plus-minus cells and Table 4 uncertainty cells were hand-transcribed after render checks.

The committed `compare.txt` records two no-matches: the new K2O melting-temperature cell and the pre-existing Na2O melting-temperature cell. MinerU classified every Table 1 body row as a header, so the compare tool has no candidate body rows; the K2O value was checked against the page render and pdftotext. The Na2O record is pre-existing and was not changed. There were no reported mismatches.

## Scope, evidence, and conditions

- New records: 9 observations (Table 1: 1; Table 2: 3; Table 3: 3; Table 4: 2). Table 2 has 7 K2O segments, and Table 3 has 6.
- Evidence is `quoted_attributed` to the authors' selected compilation. Tables 1–4 refer readers to Table 5 for their underlying sources, but Table 5 is outside the supplied excerpt; each row says so in its attribution.
- The excerpt states a 1.01325 bar standard state on p. 153. It is retained only as a standard-state label. No experimental total pressure or other point condition is printed for these compiled rows, so none is stored.
- Table 2's Gibbs energy polynomial and Table 3's enthalpy-increment polynomial do not have matching `Quantity` enum tokens. They remain in the extract as typed-refused records; no schema or migrator token was added.
- The excerpt contains no printable vapor-pressure table or log-p equation for K2O. Vapor-pressure curves are figure-only and are left as figure-read candidates.
- Other oxides/species in the excerpt skipped for this addendum: Li2O, Li2O2, NaO2, Na2O, Na2O2, KO2, K2O2, RbO2, Rb2O, Rh2O2, CsO2, CsO3, Cs2O, Cs2O2, Cs3O, and barium-bearing species. Existing rows were not changed.

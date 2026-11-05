# Bonnickson (1954) calcium ferrite extraction — WIP

## Source reads and unresolved compound label

Machine text was read from the MinerU markdown and `pdftotext-layout.txt` in the source corpus. The article begins on PDF page 1; Tables I and II are printed on PDF page 2; the heat-content equations and melting discussion are on PDF page 3. Table I identifies its calcium compounds as `CaFe2O4` and `Ca2Fe2O5`; Table II repeats those formulas. The surrounding prose also identifies the latter as `Ca2Fe2O5`.

The page-3 equation block prints the solid fit for `Ca2Fe2O5(c)`, then labels the liquid fit `CaFe2O5(l)`. This conflicts with Table I, Table II, the abstract and prose, all of which identify the compound as `Ca2Fe2O5`. The liquid-fit formula is retained as printed pending a ruling on whether the equation may be associated with the Ca2Fe2O5 species while preserving the source's apparent omission. No equation row has been assembled yet.

## Table structure

MinerU has read both printed tables on page 2. Table I has 31 body rows including two compound-heading rows; it contains 25 CaFe2O4 measurements and 24 Ca2Fe2O5 measurements, with the remaining rows belonging to excluded MgFe2O4. Table II has 24 body rows including its two header rows; calcium heat-content values are among the table's paired heat-content/entropy columns. The MinerU parser flags comma-grouped thousands as ambiguous separators and several empty/merged cells, so comparison must be numeric-column aware. These tables are born-digital: `pdftotext-layout.txt` provides the second machine read. The table row/column layout still needs render verification, and every disagreement/flagged cell must be rendered and audited before extraction.

## Conditions and scope

The text states the results are in defined calories (1 cal. = 4.1840 abs. joules) per mole. No experimental total pressure is stated; do not add a pressure. The reported heat-content reference is `H(298.16 K)`. Figure-only values are excluded. Magnesium ferrite is excluded. The paper reports no other calcium ferrite heat-content values cited from other work; its own table values and fitted results are reported in this paper.

## Remaining work

Resolve the printed liquid-fit formula with the controller before associating that fit with a species. Then extract all calcium entries from Tables I and II, the unambiguous CaFe2O4 and Ca2Fe2O5 equations, melting temperatures/enthalpies, and the incongruent-melting text; finish the cell audit, isolated migration, focused fidelity test, required test files, and expected store-delta calculation.

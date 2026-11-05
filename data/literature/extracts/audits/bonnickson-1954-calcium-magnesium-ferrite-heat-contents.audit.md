# Bonnickson (1954) calcium ferrite extraction — WIP

## Source reads and unresolved compound label

Machine text was read from the MinerU markdown and `pdftotext-layout.txt` in the source corpus. The article begins on PDF page 1; Tables I and II are printed on PDF page 2; the heat-content equations and melting discussion are on PDF page 3. Table I identifies its calcium compounds as `CaFe2O4` and `Ca2Fe2O5`; Table II repeats those formulas. The surrounding prose also identifies the latter as `Ca2Fe2O5`.

The page-3 equation block prints the solid fit for `Ca2Fe2O5(c)`, then labels the liquid fit `CaFe2O5(l)`. The controller ruled that the equation may be associated with Ca2Fe2O5 if the paper ties it to that compound's sample and melting interval. It does: the immediately preceding paragraph says Ca2Fe2O5 melting was complete at 1752 K, and the liquid equation covers 1750–1850 K. Preserve `CaFe2O5(l)` as the printed label and document the formula as a contextual misprint.

Table I has two CaFe2O4 observations in the incongruent-melting interval: 1504.6 K / 64,320(a) and 1529.7 K / 74,970(a). The source says melting may begin as low as 1463 K, is not complete at 1530 K, and adopts 1510 K arbitrarily for the fitted phase boundary. By controller ruling, retain both numeric values as typed-refused rows with phase unknown; do not assign phase from the adopted 1510 K. Preserve the row marker and footnote (a), and record the melting interval and the fitted 1510 K treatment separately with their locators.

## Table structure

MinerU has read both printed tables on page 2. Table I has 31 body rows including two compound-heading rows; it contains 25 CaFe2O4 measurements and 24 Ca2Fe2O5 measurements, with the remaining rows belonging to excluded MgFe2O4. Table II has 24 body rows including its two header rows; calcium heat-content values are among the table's paired heat-content/entropy columns. The MinerU parser flags comma-grouped thousands as ambiguous separators and several empty/merged cells, so comparison must be numeric-column aware. These tables are born-digital: `pdftotext-layout.txt` provides the second machine read. The table row/column layout still needs render verification, and every disagreement/flagged cell must be rendered and audited before extraction.

## Conditions and scope

The text states the results are in defined calories (1 cal. = 4.1840 abs. joules) per mole. No experimental total pressure is stated; do not add a pressure. The reported heat-content reference is `H(298.16 K)`. Figure-only values are excluded. Magnesium ferrite is excluded. The paper reports no other calcium ferrite heat-content values cited from other work; its own table values and fitted results are reported in this paper.

## Remaining work

Extract all calcium entries from Tables I and II, the equations, melting temperatures/enthalpies, and incongruent-melting text; finish the cell audit, isolated migration, focused fidelity test, required test files, and expected store-delta calculation.

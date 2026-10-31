# Kozhina and Shultz (2000), Table 1 extraction audit

## Scope and disposition

Table 1 contains 20 binary-composition rows and 40 populated numeric cells. The composition ranges are Na2O-SiO2 (five rows, XNa2O 0.1–0.5), Na2O-B2O3 (five, 0.1–0.5), Na2O-GeO2 (six, 0.1–0.6), and Na2O-P2O5 (four, 0.4–0.7). The first three are at 1300 K; the phosphate values are at 1073 K, as printed in that system's header.

The source describes these as integral melt formation functions referred to “the corresponding properties of the pure oxides.” The extract preserves the phrase without assigning unprinted phases to the pure-oxide reference. The schema has no scored quantity for binary-melt integral formation Gibbs energy and enthalpy, so the table is carried as a `measured_table_context` row. These are the authors' values derived from EMF measurements and Gibbs-Duhem integration; they are not calorimetry or elemental-standard compound formation energies. Blank cells remain absent.

## Full-cell audit

MinerU's Table 1 HTML and the pdftotext layout agree on the digits, signs, and decimal places for all 40 populated cells. This is verification route (a) for every numeric cell. The rendered PDF page 4 (printed page 94) confirms the table's row and column assignment once. The render showed no disagreement or flagged cell. No OCR redo was needed.

| Printed row | System | XNa2O | T (K) | ΔGf (kJ mol-1) | ΔHf (kJ mol-1) | Cell verification |
|---:|---|---:|---:|---:|---:|---|
| 1 | Na2O-SiO2 | 0.1 | 1300 | -26.8 | -18.4 | (a), MinerU + pdftotext |
| 2 | Na2O-SiO2 | 0.2 | 1300 | -53.6 | -43.1 | (a), MinerU + pdftotext |
| 3 | Na2O-SiO2 | 0.3 | 1300 | -78.7 | -73.3 | (a), MinerU + pdftotext |
| 4 | Na2O-SiO2 | 0.4 | 1300 | -98.6 | -99.2 | (a), MinerU + pdftotext |
| 5 | Na2O-SiO2 | 0.5 | 1300 | -113.7 | -120.6 | (a), MinerU + pdftotext |
| 6 | Na2O-B2O3 | 0.1 | 1300 | -36.4 | -38.9 | (a), MinerU + pdftotext |
| 7 | Na2O-B2O3 | 0.2 | 1300 | -67.4 | -76.2 | (a), MinerU + pdftotext |
| 8 | Na2O-B2O3 | 0.3 | 1300 | -94.0 | -98.8 | (a), MinerU + pdftotext |
| 9 | Na2O-B2O3 | 0.4 | 1300 | -116.0 | -110.1 | (a), MinerU + pdftotext |
| 10 | Na2O-B2O3 | 0.5 | 1300 | -134.0 | -113.9 | (a), MinerU + pdftotext |
| 11 | Na2O-GeO2 | 0.1 | 1300 | -33.5 | -27.6 | (a), MinerU + pdftotext |
| 12 | Na2O-GeO2 | 0.2 | 1300 | -61.8 | -55.7 | (a), MinerU + pdftotext |
| 13 | Na2O-GeO2 | 0.3 | 1300 | -86.0 | -81.6 | (a), MinerU + pdftotext |
| 14 | Na2O-GeO2 | 0.4 | 1300 | -105.9 | -107.2 | (a), MinerU + pdftotext |
| 15 | Na2O-GeO2 | 0.5 | 1300 | -120.7 | -129.8 | (a), MinerU + pdftotext |
| 16 | Na2O-GeO2 | 0.6 | 1300 | -127.8 | -137.3 | (a), MinerU + pdftotext |
| 17 | Na2O-P2O5 | 0.4 | 1073 | -224.0 | -186.7 | (a), MinerU + pdftotext |
| 18 | Na2O-P2O5 | 0.5 | 1073 | -258.5 | -224.4 | (a), MinerU + pdftotext |
| 19 | Na2O-P2O5 | 0.6 | 1073 | -278.8 | -254.1 | (a), MinerU + pdftotext |
| 20 | Na2O-P2O5 | 0.7 | 1073 | -285.2 | -272.3 | (a), MinerU + pdftotext |

The paper does not tabulate total pressure or fO2 for these rows; neither is added. The values of XNa2O and the binary-system headers are transcribed as printed; no complementary composition is calculated.

## Other source material

- Figure 2 partial chemical-potential functions are figure-only candidates and were not digitized. The rendered PDF page 3 (printed page 93) confirms the figure and caption; locator: page 3, published page 93, PDF page index 2.
- Equation 3 is retained literally in the paper's printed form. Its activity and chemical-potential sides have inconsistent signs; no correction was made.
- The prose examples of very low Na2O activity are not Table 1 cells and were not converted into logarithmic observations.

## Tool and migration evidence

- MinerU table QA: one table, 47 numeric cells including headers/coordinate column; numeric agreement 1.000, no flagged table.
- `mineru_table_draft.py compare`: zero candidates because the extract correctly stores this schema-gap table in `context`, outside scorer observations. No comparison mismatch was suppressed.
- Isolated migration: 0 scored observations, 0 unavailable values, 0 hard issues; all three context rows are carried into the work record.

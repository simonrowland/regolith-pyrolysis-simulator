# Christensen, Conway & Kelley (1960), USBM RI 5565 — extraction audit

Scope is NaFeO2. The full pdftotext file and MinerU markdown were searched for CaFe2O4 and Ca2Fe2O5; neither compound appears. This report provides no standard formation enthalpy or Gibbs energy for NaFeO2. No values are imported from another source.

## Machine reads and render resolution

MinerU Table 7 is draft `p012-t08.csv` (absolute PDF page 12, printed page 5); Table 8 is `p013-t09.csv` (PDF page 13, printed page 6). MinerU QA reports low digit agreement with pdftotext (0.651 for target page-12 block; 0.643 and 0.703 for page-13 blocks). Following the input-order rule, every retained numeric table cell was read from the 600 dpi page render. Both table row/column assignments and every locator were checked on those renders. See the paired cell CSV for the individual T, H and S cells. MinerU `details` chart blocks were ignored.

| Printed table | Printed page / PDF page | Printed rows | Stored numeric rows / cells | Audit method |
|---|---:|---:|---:|---|
| 7 measured heat content | 5 / 12 | 19 per paired column | 38 T-H rows (76 cells) | Render, every cell |
| 8 smooth heat content and entropy | 6 / 13 | 23 | 23 T-H-S rows (69 cells) | Render, every cell |

Species assignment was checked against the printed NaFeO2 caption. Table 7 has 26 crystalline readings through 1492.8 K, seven explicitly marked “in melting range,” and five above the printed 1620 K melting point. Table 8 has 23 rows: five unlabeled lower-temperature points are assigned to the α form identified at its 870 K transition; the remaining rows carry the printed α, β, γ, or l label. Parenthesized 1900 and 2000 K Table 8 values are extrapolations.

## Text and citation values

The source states transitions complete on heating at 870 K and 1270 K; the 870 K event has no isothermal heat absorption (heat-capacity peak only), and the 1270 K heat absorption is 520 cal/mol. Fusion is at 1620 K, with 11760 cal/mol heat absorption and 7.26 cal/mol K entropy of fusion. Table 8 prints all four H(T)-H(298.15 K) fits and Cp(T) equations; they are retained in located context. The authors adopt King’s S°298.15 = 21.1 ± 0.2 cal/deg mole. They compare their measured NaFeO2 heat contents with Margrave and Grimley (1958): cited values are 0.4–3.5% higher, average 2.1%; the authors report repeating their measurements and conclude the discrepancy is not in their measurements. Those are the only within-source alternate NaFeO2 values/comparisons.

## Migration and schema

The isolated real migrator emits 92 observations, with 0 hard issues and 88 informational issues. The pressure axes account for all 88 (85 `standard_pressure_Pa`, 3 `total_pressure_Pa`): the report never states pressure, so those conditions remain unknown. No values were supplied for them.

The printed 870 K and 1270 K solid-solid transition enthalpies have no exact quantity token. `simulator/battery/enums.py::Quantity` has `ENTHALPY_OF_FUSION` but no solid-solid transition enthalpy; `simulator/battery/migrate.py::QUANTITY_ALIASES` and `TYPE_QUANTITY` provide no exact alias or type inference. `tools/validate_literature_extracts.py::OBSERVATION_TYPES` does not add an exact quantity representation. They remain the only two quantity-unknown typed refusals; their printed values and locators are preserved. Heat of fusion remains the exact `heat_of_fusion` quantity.

## Comparison artifact

The MinerU compare output reports 18 matched values, 74 OCR mismatches, and 0 locator errors. It is retained as a discrepancy index. Its mismatches are not silently treated as corrections: Table 7 and Table 8 stored cells are sourced from page renders as recorded in the paired cell audit CSV.

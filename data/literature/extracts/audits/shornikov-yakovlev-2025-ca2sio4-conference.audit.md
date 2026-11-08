# Shornikov and Yakovlev 2025 extraction audit

## Inventory and method

- The four-page conference excerpt has no printed tables. MinerU content-list inspection found zero `table` blocks; `tools/mineru_table_draft.py convert` reported 0 tables, and `tools.mineru_table_qa` reported 0 tables and 2 invented `<details>` chart summaries. The two summaries are ignored as required.
- The row-aware `tools/mineru_table_draft.py compare` output is committed alongside this report; it has zero matched, unmatched, or mismatched points because the draft contains no table cells.
- The machine reads were the MinerU Markdown and the born-digital `pdftotext-layout.txt`. The relevant digits, signs, units, and intervals agree. The text locators below were checked against renders of physical PDF pages 1–4 (printed pages 264–267).
- Printed table row count: **0**. Four text-result rows are retained: two new KEMS-derived mixing results and two quoted prior-literature ranges. Their exact mixing quantities have no matching quantity tokens, so all four migrate as unavailable values.

## Full-cell audit

| Printed numeric content | PDF page / printed page | Machine reads | Render and disposition |
|---|---:|---|---|
| Prior mixing enthalpy range −54 to −34 kJ/mol at 298 K | 1 / 264 | MinerU and pdftotext agree | p264 render confirms both negative signs, endpoints, unit, and 298 K; stored as a quoted, typed-refused range. |
| Prior mixing entropy range −2.4 to 3.3 J/(mol K) | 1 / 264 | MinerU and pdftotext agree | p264 render confirms sign, endpoints, and unit; stored as a quoted, typed-refused range. |
| High-temperature mixing-energy measurement accuracy up to 4 kJ/mol | 1 / 264 | MinerU and pdftotext agree | p264 render confirms; retained as method context, not a result row. |
| KEMS interval 1762–2269 K | 1 / 264; repeated on 4 / 267 | Both reads agree | p264 and p267 renders confirm the conference value; abstract repeat is recorded. The journal-abstract value 1762–2267 K and queue value 2288 K are external discrepancies only. |
| Sample firing interval 1773–1823 K | 2 / 265 | Both reads agree | p265 render confirms; retained in sample-preparation context. |
| Mixing-energy comparison, about 1.5 kJ/mol above Barin (1995) | 3 / 266 | Both reads agree | p266 render confirms; retained as a comparison context, not an absolute Gibbs-energy point. |
| Mixing enthalpy −34.90 ± 0.20 kJ/mol | 3 / 266; repeated on 4 / 267 | Both reads agree | p266 and p267 renders confirm minus sign, both decimals, uncertainty, and unit. Retained as an unavailable model-derived result because the mixing quantity has no exact token and its printed derivation is incomplete for migration. |
| Mixing entropy 6.60 ± 0.10 J/(mol K) | 3 / 266; repeated on 4 / 267 | Both reads agree | p266 and p267 renders confirm both decimals, uncertainty, and unit. Retained as an unavailable model-derived result because the mixing quantity has no exact token and its printed derivation is incomplete for migration. |

## Figure-only candidates and conditions

- Figure 2 caption names partial pressures of Ca, SiO, O, and CaSiO3 over Ca2SiO4. The chart plots log pressure in atm against T (K), and the Ca2SiO4 curve is series 2. No pressure points were digitized. The caption says inverse temperature, but the rendered abscissa is plainly T (K); that caption error is recorded in the extract.
- Figure 3 plots Ca2SiO4 mixing Gibbs energy. The present-work KEMS series is marker 7 and Barin (1995) is series 8. Figure values were not digitized. The only text number about the comparison, approximately 1.5 kJ/mol, is kept as context.
- This conference text gives no numerical component activities and no definition of the mixing quantities' reference state. No standard-state frame is assigned. The task's condensed-element convention is not applied without a printed definition.
- Total pressure and fO2 are not printed; the extract stores both with `state: unknown` and `reason: not_published`. The migrator drops unknown total pressure from `point_conditions` and preserves fO2 as an unknown located condition; neither enters the identity as a value. The printed Ca2SiO4 stoichiometry is bound through `point_conditions.composition`. No pressure, fO2, sweep gas, or reference state is inferred.
- The proceedings do not assign a phase to the two current results. Their phase is unknown; the phase diagram is not used to infer a phase.
- The abstract on p.267 states: “The partial pressures of the vapor species over Ca2SiO4 were obtained by the Knudsen mass spectrometric effusion method at 1762–2269 K. The mixing energy, as well as the enthalpy and entropy of calcium orthosilicate equal to –34.90±0.20 kJ/mol and 6.60±0.10 J/(mol×K) were calculated.” The two results are therefore `model_derived`, with this located method and no claim about a second- or third-law treatment. The values remain unavailable: the proceedings print neither a calculation equation nor numerical pressure inputs; Figure 2 gives pressure curves only. The p.265 text cites Shornikov and Archakov (2000) for experimental conditions, but does not identify it as the derivation method or provide a named calculation reference.

## Schema-gap and migration record

- `OBSERVATION_TYPES` provides the generic `gibbs_table` family but no mixing-specific observation type; `QUANTITY_ALIASES` has no exact mixing-enthalpy or mixing-entropy token. `Quantity.DELTA_FH` is formation enthalpy, `Quantity.PARTIAL_MOLAR_ENTHALPY` is partial-molar, `Quantity.S` is entropy rather than entropy of mixing, and the fusion tokens are not mixing quantities. The descriptive non-token labels prevent `TYPE_QUANTITY` from inferring `delta_fG` from `gibbs_table`. The proceedings do not state a phase for the two new results, so their migrated phase is unknown. The existing `sugawara-2011-sodium-silicate-fusion-mixing.yaml` extract likewise keeps its unsupported mixing-enthalpy token as typed-refused rather than binding a near-miss.
- The isolated migration retains four unavailable observations and three context rows. Both new result rows migrate as `MODEL_DERIVED` with unknown phase. It reports eight hard issues: four per result. `simulator/battery/validate.py::validate_corpus` requires the `raw_inputs: not_printed` declaration to have an equation-bearing method locator, named compiled references, and resolved lineage for model-derived observations. The source supplies none of the equation, derivation references, or numerical plotted inputs needed to satisfy those requirements. The rows stay unavailable and model-derived; no equation, reference, or source lineage was invented. The focused test pins these eight source-limited issues, phase and evidence class, numeric result fields, and the three identity-axis records.

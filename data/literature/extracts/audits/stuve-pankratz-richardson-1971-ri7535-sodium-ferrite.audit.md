# Stuve, Pankratz & Richardson (1971), RI 7535 — NaFeO₂ audit

## Scope and result

Only the NaFeO₂ entry is in scope. Table 8 (PDF page 18, printed page 11) contains five body rows; the NaFeO₂(c) entry is body row 3. The other four rows are Na₂O, Fe₂O₃, (Na₂O)₂·FeO, and (Na₂O)₄·Fe₂O₃, so they are excluded. Exactly one NaFeO₂ numeric observation is stored.

The sole NaFeO₂ value in this report is **ΔGf° at 1,000 K: −60.1 kcal per gram-atom of oxygen**, attributed in the reference column to **Koehler and coworkers (10)**. The source's discussion identifies Koehler et al. as the work on NaFeO₂, and reference 10 is Koehler, Barany & Kelley, “Heats and Free Energies of Formation of Ferrites and Aluminates of Calcium, Magnesium, Sodium, and Lithium,” Bureau of Mines Report of Investigations 5711 (1961).

| Property | NaFeO₂ value in this source | Other NaFeO₂ value cited in this source | Finding |
|---|---|---|---|
| ΔGf° at 1,000 K | −60.1 kcal per gram-atom O; Koehler et al. (10), Table 8 | None separately printed | No within-source numerical disagreement |
| ΔfH°₂₉₈ | None | None | Not reported for NaFeO₂ |
| S°₂₉₈ | None | None | Not reported for NaFeO₂ |
| Cp(T) equation or table | None | None | Not reported for NaFeO₂ |
| H(T)−H₂₉₈ | None | None | Not reported for NaFeO₂ |
| High-temperature NaFeO₂ transition, including a δ phase near 1498 K | None | None | No NaFeO₂ transition value in this source |
| NaFeO₂ fusion temperature or enthalpy | None | None | No NaFeO₂ fusion value in this source |

The paper's heat-capacity, enthalpy, and incongruent-melting discussion applies to (Na₂O)₄·Fe₂O₃, not NaFeO₂, and was excluded. No value was read from a figure or derived.

## Full-cell audit

| Cell | Machine reads | Render check | Resolution |
|---|---|---|---|
| Table 8, PDF p. 18 / printed p. 11, body row 3, ΔGf° column, NaFeO₂(c): −60.1 | MinerU HTML table and pdftotext both read `-60.1` | 300 dpi render checked the full five-row layout and the NaFeO₂ row/column assignment | (b); exact value confirmed |
| Table 8 caption temperature, PDF p. 18 / printed p. 11: 1,000 K | MinerU markdown reads `1,000° K`; pdftotext reads `1,000 ° K` | 300 dpi render confirms the caption and printed temperature | (b); stored T_K and point condition confirmed |
| Table 8, PDF p. 18 / printed p. 11, body row 3, Reference column: Koehler and coworkers (10) | MinerU HTML table and pdftotext both read the printed attribution | 300 dpi render confirms the NaFeO₂(c) reference cell | (b); attribution confirmed |

MinerU's QA pre-screen reports Table 8 numeric agreement 1.000 with pdftotext, with no numeric flags. The rendered page also confirms the Table 8 caption's 1,000 K temperature and per-gram-atom-of-oxygen basis. The discussion locator on the same PDF page separately confirms the Koehler attribution.

The page render verifies both distinct locators used by the extract: the NaFeO₂ row in Table 8 and the Koehler sentence in the DISCUSSION text above the table.

The printed denominator is represented by the existing `mol_atom` basis, while `values.per_as_printed` preserves “per gram-atom of oxygen.” Units use the existing `kcal/mol` energy token for that declared basis; no per-formula value or converted twin is added. Table 8 gives ΔGf° but does not print a formation equation or elemental reference forms for this comparator. The extract records that limit in `standard_state`; it adds no pressure, fO2, or composition condition.

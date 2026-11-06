# Moosavi-Khoonsari and Jung (2016) extraction audit

## Scope and source distinction

This journal paper remains its own source, separate from `moosavi-khoonsari-2015-na-fe-cmas-thesis`. The thesis contains direct counterparts to journal Tables II–VI as thesis Tables 5.2–5.6 (thermochemical properties, optimized solution parameters, and the three invariant-reaction tables). Its Table 5.1 is the counterpart to journal Table I. Tables I–VI are therefore duplicate assessment material; this paper's observations retain this paper's DOI, locators, and attribution.

The first committed slice covers NaFeO2 thermochemistry. It stores quoted Table II enthalpy and entropy values and the quoted fusion temperature. The authors' optimized enthalpy, entropy, Cp equation, polymorph transition enthalpies, fusion temperature, and liquid associate Gibbs-energy term are recorded as located model context. Transition enthalpy has no exact token in `simulator/battery/enums.py::Quantity`, so it remains context instead of being mislabeled as fusion or formation enthalpy. Figure 3 curves are figure-read candidates and were not digitized.

## NaFeO2 comparison within this paper

| Property | Value quoted from other work | Current assessment in this paper |
|---|---|---|
| ΔfH°298 | −698.02 ± 1.30 kJ/mol, Koehler et al. [28], SC | −697.350 kJ/mol, Table II |
| S°298 | 88.34 ± 0.84 J/mol-K, King [32], AC | 88.341 J/mol-K, Table II |
| Cp | King low-temperature Cp and Margrave and Grimley [31] / Christensen et al. [27] heat-content measurements are described; no numerical points transcribed from Figure 3 | `110.95 + 0.0076933T − 2552200T^-2` J/mol-K, same for α, β, and γ |
| α→β and β→γ transition | Christensen et al. [27] data are the stated basis; no separate numerical experimental transition values printed | 41.84 J/mol at 870 K; 2177 J/mol at 1270 K |
| Fusion temperature | 1620 K, Table IV [27, 28] | 1620 K, Table IV |
| Fusion enthalpy | No numeric value printed in text or tables | Not printed; figure-only comparison in Figure 3(a) excluded |

## Cell audit for the committed slice

| Cell(s) | Machine reads | Render confirmation | Resolution |
|---|---|---|---|
| Table II, PDF p. 5 / printed p. 580, NaFeO2 row, ΔH°298: −698.02 ± 1.30 | MinerU table HTML/markdown and pdftotext agree on digits and sign | 150 dpi page render confirms row, column, minus sign, and uncertainty | Stored quoted value with printed uncertainty |
| Table II, PDF p. 5 / printed p. 580, NaFeO2 row, S°298: 88.34 ± 0.84 | MinerU and pdftotext agree | Same render confirms row, column, and uncertainty | Stored quoted value with printed uncertainty |
| Table II, PDF p. 5 / printed p. 580, α-NaFeO2 row: −697.350; 88.341; Cp expression; transition block | MinerU and pdftotext agree on each numeric string; MinerU merges the spanning transition cell | Same render confirms all populated numerical cells and row/column assignment | Optimized values retained as model context; transition enthalpies retained as typed context because no exact quantity token exists |
| Table IV, PDF p. 7 / printed p. 581, `L = NaFeO2(c)`, Exp. temperature: 1620 (1347) | MinerU and pdftotext agree | Page render confirms phase label and Exp./Calc. column assignment | Stored 1620 K as quoted experimental fusion temperature |

Table II has 33 printed body rows across its two pages. The render used for this slice is the page containing the NaFeO2 rows; non-NaFeO2 Table II cells and the remaining QA-flagged tables are deferred to the continuing extraction, not silently considered complete. The NaFeO2 mapping and values are in the source YAML and the focused test pins the values and both table locators.

## Migration result for this slice

Isolated migration: 3 observations, 0 hard issues, 0 unavailable values. The ΔfH identity queues `formation_elements` and `reaction` as unknown because the article labels a formation enthalpy but does not print an element reference set or a balanced formation reaction. These axes were not invented. Standard pressure (1 bar, stated for the system) and mol basis are carried from printed text/table units.

No conditions are assigned to context rows. The experimentally quoted entropy's unit is J/mol/K; pressure and mol-species basis are present in the printed standard thermochemistry context.

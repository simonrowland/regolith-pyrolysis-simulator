# Asai, Yokokawa & Maekawa (1982): activity basis question

## Source facts transcribed from machine text

The English abstract says the emf concentration cell used a Na2O·2B2O3 reference melt and measured the Na2O–B2O3–Al2O3 system at approximately 1150 K. The Japanese text says the B2O3/Na2O molar ratios were 2, 3, 4, 9, and 15, with Al2O3/Na2O from 0 to 1. The cell is described as O2(Pt) | Na2O·2B2O3 : Na2O–B2O3–Al2O3 | O2(Pt). The method is emf measurement in a concentration cell.

Printed Table 2 is captioned “Relative partial molar quantities of Na2O ... at 1173 K, referred to Na2O·2B2O3 melt.” It prints ΔG, ΔH and ΔS, not a dimensionless activity column. The table has 32 ΔG entries across five B2O3/Na2O series: 4 (ratio 2), 8 (3), 7 (4), 6 (9), and 7 (15). MinerU puts the table across two blocks on PDF pages 3 and 4 (zero-based `page_idx` 2 and 3 in the p001-p005 chunk). Table 1 separately prints emf (mV) against temperature for the same composition families.

The paper's machine text gives the concentration-cell equation connecting emf and the activity ratio, and explicitly describes Na2O·2B2O3 as the reference standard. No printed total pressure or numeric oxygen fugacity was located in the machine text. The paper describes preparation of glass samples by quenching after approximately 1300 K treatment, but the requested activity measurements are described in the melt and are evaluated at 1173 K; phase and quench interpretation should follow the eventual activity-basis ruling.

## Unresolved classification required before extraction

The requested rail target is absolute `a(Na2O)/a(NaO0.5)`, while the numerical Table 2 cells are relative ΔG values against Na2O in Na2O·2B2O3. Converting each ΔG cell with `exp(ΔG/(RT))` would produce a dimensionless ratio to that melt reference; it would not be a value printed as activity. The brief also requires values in their printed form and prohibits conversion at extraction. Please rule whether this task should:

1. store the calculated activity ratio as measured_reduced, retaining the printed ΔG and the explicit Na2O·2B2O3 standard state as derivation evidence; or
2. preserve the published ΔG values as printed and mark the requested activity rail typed-refused / schema gap because the paper does not tabulate the requested activity form.

Please also confirm whether the requested `a(Na2O)/a(NaO0.5)` denotes the activity ratio to the stated Na2O·2B2O3 reference (whose activity is 1), or requires a different Na2O versus NaO0.5 convention. No activity numbers have been assigned pending this ruling.

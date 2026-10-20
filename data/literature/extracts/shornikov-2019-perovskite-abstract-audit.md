# Shornikov and Yakovlev (2019), MetSoc abstract 6005: extraction audit

## Duplicate verdict

pdfinfo Title is `6005.PDF`. Page 1 header: "82nd Annual Meeting of The Meteoritical Society 2019 (LPI Contrib. No. 2157) 6005.pdf". sha256 `c09c421f62accbed788a44e15e17c42caed290a500c45503d154a44d7efe3c67`.

The same PDF is already extracted as `metsoc-2019-6005`. That file stores the printed formation enthalpy −39.88±0.54 kJ/mol, formation entropy 3.15±0.28 J/(mol×K), and melting enthalpy 47.61±1.84 kJ/mol at 2241±10 K. It records Table 1 as a typed absence. Those three thermo rows are not repeated here.

The same campaign's pressures and activities are already in `shornikov-2019-perovskite-evaporation`, `shornikov-2019-perovskite-evaporation-rjpc`, `shornikov-2019-perovskite-evaporation-thermodynamics`, and `shornikova-2019-perovskite-thermodynamics`. Reference [4] on this page is the TMS chapter. Figs. 1 and 2 are not re-read.

Table 1 gas mol% is not in those extracts. Those 30 cells are the only rows admitted here.

## Table 1

Caption: "The composition of the gas phase over perovskite (mol %)." The paragraph above the table says the composition at 1700–2400 K was calculated from the oxide activities. `method_class` is `model_derived`. The activities are not printed in this abstract, so no `derived_from` parent is stored. Expected migration result: a hard `conditional_field` on `derived_from` and on `derivation` for each numeric cell (class b-767). Not measured on this laptop.

`basis_as_published` is `gas-phase mol %`, which migration reads as the equilibrium-composition subtype. The quantity identity treats total pressure as not applicable, so the unstated total pressure is not stored as an unknown pressure axis. The condensed phase is perovskite, named in the caption; no oxide mole-fraction vector was invented.

Every digit below was read on a 600 dpi render of PDF page 1, including a crop of the table. pdftotext-layout drops or wraps some superscripts (the 1700 K CaO line shows `8.0×10` without the exponent). MinerU keeps the exponents. The render agrees with MinerU. The ten scientific cells are stored as decimal strings. `value_as_printed` is the MinerU cell. That spelling change is the only hand correction.

| CSV | Species | T, K | Printed cell | Stored value | Audit |
| --- | --- | ---: | --- | --- | --- |
| r02 c02 | O | 1700 | 34 | 34 | render agrees with MinerU |
| r02 c03 | O2 | 1700 | 1.3 | 1.3 | render agrees with MinerU |
| r02 c04 | Ca | 1700 | 35 | 35 | render agrees with MinerU |
| r02 c05 | CaO | 1700 | $8.0×10^{-3}$ | 8.0e-3 | render agrees; decimal spelling |
| r02 c06 | Ti | 1700 | $3.4×10^{-5}$ | 3.4e-5 | render agrees; decimal spelling |
| r02 c07 | TiO | 1700 | 0.96 | 0.96 | render agrees with MinerU |
| r02 c08 | TiO2 | 1700 | 29 | 29 | render agrees with MinerU |
| r02 c09 | Ti2O3 | 1700 | $4.7×10^{-3}$ | 4.7e-3 | MinerU flag non_numeric_in_numeric_column; render agrees; decimal spelling |
| r02 c10 | Ti2O4 | 1700 | $2.5×10^{-3}$ | 2.5e-3 | MinerU flag non_numeric_in_numeric_column; render agrees; decimal spelling |
| r02 c11 | CaTiO3 | 1700 | $3.9×10^{-5}$ | 3.9e-5 | render agrees; decimal spelling |
| r03 c02 | O | 2241 | 16 | 16 | render agrees with MinerU |
| r03 c03 | O2 | 2241 | 1.2 | 1.2 | render agrees with MinerU |
| r03 c04 | Ca | 2241 | 10 | 10 | render agrees with MinerU |
| r03 c05 | CaO | 2241 | $3.4×10^{-2}$ | 3.4e-2 | render agrees; decimal spelling |
| r03 c06 | Ti | 2241 | $2.8×10^{-3}$ | 2.8e-3 | render agrees; decimal spelling |
| r03 c07 | TiO | 2241 | 8.2 | 8.2 | render agrees with MinerU |
| r03 c08 | TiO2 | 2241 | 64 | 64 | render agrees with MinerU |
| r03 c09 | Ti2O3 | 2241 | 0.13 | 0.13 | render agrees with MinerU |
| r03 c10 | Ti2O4 | 2241 | 0.11 | 0.11 | render agrees with MinerU |
| r03 c11 | CaTiO3 | 2241 | $6.6×10^{-2}$ | 6.6e-2 | render agrees; decimal spelling |
| r04 c02 | O | 2400 | 17 | 17 | render agrees with MinerU |
| r04 c03 | O2 | 2400 | 1.4 | 1.4 | render agrees with MinerU |
| r04 c04 | Ca | 2400 | 8.6 | 8.6 | render agrees with MinerU |
| r04 c05 | CaO | 2400 | $5.1×10^{-2}$ | 5.1e-2 | render agrees; decimal spelling |
| r04 c06 | Ti | 2400 | $6.0×10^{-3}$ | 6.0e-3 | render agrees; decimal spelling |
| r04 c07 | TiO | 2400 | 11 | 11 | render agrees with MinerU |
| r04 c08 | TiO2 | 2400 | 62 | 62 | render agrees with MinerU |
| r04 c09 | Ti2O3 | 2400 | 0.15 | 0.15 | render agrees with MinerU |
| r04 c10 | Ti2O4 | 2400 | 0.12 | 0.12 | render agrees with MinerU |
| r04 c11 | CaTiO3 | 2400 | 0.39 | 0.39 | render agrees with MinerU |

No empty or dash cell. `skipped_empty_or_dash` is 0.

## Standard state

Table 1 is not an activity dataset. No `standard_state` is stored. Migration is not asked to assign a reference phase. The oxide activities used as parents are not printed here, so the physics ruling on the stable phase at T was not applied.

## Figures

The 600 dpi page render shows both figures. Axes are partly cut in the table crop; no tick line was calibrated and no marker centre was measured. Each caption series is refused because the number is already stored elsewhere. No value was invented.

| Series | Decision | Evidence |
| --- | --- | --- |
| Fig. 1 curve 1, Ca | refused | Caption: partial pressure over perovskite. Same campaign pressures are tabulated in the TMS and RJPC extracts. Not re-read. |
| Fig. 1 curve 2, CaO | refused | Same caption and same reason. |
| Fig. 1 curve 3, Ti | refused | Same caption and same reason. |
| Fig. 1 curve 4, TiO | refused | Same caption and same reason. |
| Fig. 1 curve 5, TiO2 | refused | Same caption and same reason. |
| Fig. 1 curve 6, O | refused | Same caption and same reason. |
| Fig. 1 curve 7, O2 | refused | Same caption and same reason. |
| Fig. 1 curve 8, CaTiO3 | refused | Same caption and same reason. |
| Fig. 2 curve 1, CaO at 2250 K | refused | Caption: activity in CaO–TiO2 at 2250 K. Already figure-read on Fig. 3 of `shornikov-2019-perovskite-evaporation-thermodynamics`. |
| Fig. 2 curve 2, TiO2 at 2250 K | refused | Same caption and same reason. |
| Fig. 2 curve 3, CaTiO3 at 2250 K | refused | Same caption and same reason. |

The curves are continuous. Individual glyphs were not separated, and none was refused as a legend glyph. MinerU produced no chart table for these figures.

## Refused prose numbers

−39.88±0.54 kJ/mol, 3.15±0.28 J/(mol×K), 47.61±1.84 kJ/mol, and 2241±10 K stay in `metsoc-2019-6005` and in the full-paper extracts. They are not copied here.

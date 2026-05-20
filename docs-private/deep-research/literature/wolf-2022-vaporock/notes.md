# Wolf 2022 VapoRock Extraction Notes

Source read: staged private corpus in parent checkout at
`docs-private/deep-research/literature/wolf-2022-vaporock/`.

Numeric anchor selected: BSE JANAF model output used for manuscript Fig. 4,
stored as `latex_src/data/bse_logP_JANAF.csv`.

Why this anchor: it is VapoRock's own model output, not experimental
digitization. It includes the BSE composition from manuscript Table 1, a
temperature grid, the imposed oxygen fugacity, and log10(bar) vapor partial
pressures for all gas species.

fO2 convention: the CSV column named `O2(g)` is the VapoRock log10 partial
pressure of O2 in bar, equal to the oxygen fugacity channel consumed by
`System.eval_gas_abundances(T_K, logfO2)`. The adjacent `dIW` column duplicates
that raw log10(fO2/bar) value in the staged source data, despite manuscript text
describing the same curve as approximately Delta IW +4.5 at 1500 K to +1 at
3000 K. The adapter parity cohort therefore maps:

`paper O2(g) log10(bar) -> VapoRockBackend.equilibrate(..., fO2_log=...)`.

Values in `benchmark-fixture.yaml` are converted as:

`partial_pressure_Pa = 10 ** csv_log10_bar * 1e5`.

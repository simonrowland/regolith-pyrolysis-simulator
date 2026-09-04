# SF04 high-temperature validation decision

- No high-temperature failure onset located.
- No new validation supports a blanket ceiling raise.
- Disagreement is species-specific: SiO and Mg fail every sample; Fe crosses
  by 0.004 dex; Na and K pass.
- The table is NOT evidence for a physical or numerical ceiling at 1950 K.

The comparison threshold is 0.5 dex absolute residual. Pressure sensitivity
was checked over the bracket `[1e-10, 2e-2] bar`.

The copied snapshot isolates evaluation from ordinary concurrent edits to the
source checkout and binds recorded digests to the copied bytes. It is not a
defence against an actor with write access to the running process's temporary
directory.

The 42 rows checked in here were generated before `chemistry.py` entered the
hashed import closure, so they bind `equil.py` and the three JANAF tables
only. The VapoRock chemistry module those rows executed is not identified by
any recorded digest. Rows written outside the explicit `legacy_input=True`
replay mode carry that digest; this mode exists solely to replay this
pre-existing artifact and must not be used to write new evidence. Regenerating
these rows requires ThermoEngine and the sibling VapoRock checkout.

Evidence: `validation-data/vapour_rail_sf04_high_t_residuals.csv`, compared
against the companion-workbook extract at
`data/literature/extracts/sf04-magma-companion-workbook.yaml`.

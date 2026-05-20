# VapoRock Full-Speciation Diagnostic

Date: 2026-05-20

## Scope

Targeted scope only. VapoRock's full JANAF gas output is exposed as
`vaporock_full_speciation_Pa` for diagnostics, benchmarks, and two-engine
cross-checks. The evaporation/ledger path still consumes only the filtered
`vapor_pressures_Pa` surface.

## Guard

`vapor_pressures_Pa` remains the ledger-feeding map. It is still filtered by
the VAPOR_PRESSURE provider against the validated species declared in
`data/vapor_pressures.yaml`. Long-tail species such as `O2`, `Si2`,
`Al2O2`, and oxide-colliding gases such as `SiO2_gas` are not routed through
`EVAPORATION_FLUX`, `simulator/evaporation.py`, or the mass-balance ledger.

`vaporock_full_speciation_Pa` is read-only. It does not affect activities,
fluxes, stoichiometry, parent-oxide validation, atom conservation, or
`commit_batch`.

## Naming

The diagnostic uses the existing VapoRock adapter normalization:

- VapoRock gas suffixes are stripped: `O2(g)` -> `O2`.
- Oxide-colliding gases are namespaced: `SiO2(g)` -> `SiO2_gas`.
- Values are normalized to Pa using the existing explicit-unit path or the
  unambiguous `eval_gas_abundances` log10(bar) conversion.

## Structural Species

K and O2 are VapoRock-authoritative structural species.

K is already in the validated evaporation species set with `parent_oxide:
K2O`. VapoRock should be preferred when available; the Antoine coefficients in
`data/vapor_pressures.yaml` are fallback-only approximations.

O2 is diagnostic-only. It has no single parent oxide in the evaporation schema:
it is a disproportionation/byproduct term already handled by the existing O2
accounting. Adding O2 to `vapor_pressures_Pa` would require a Tier-2
evaporation/ledger design, not this targeted diagnostic.

## Verification

Required checks:

- Installed VapoRock check on 2026-05-20 returned 30 finite positive species
  at the basalt smoke point, including `O2`, dimer `Si2`, and oxide gas
  `MgO_gas`.
- `vaporock_full_speciation_Pa` contains the VapoRock long tail, including O2,
  at least one dimer, and at least one `_gas` oxide when VapoRock emits them.
- Filtered `vapor_pressures_Pa` is byte-identical for species set and values.
- O2 appears in `vaporock_full_speciation_Pa` but not `vapor_pressures_Pa`.
- Mass balance and corpus parity remain unchanged.

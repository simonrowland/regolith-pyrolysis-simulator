# Output Interpretation

## Evaporation Alpha

Hertz-Knudsen-Langmuir fluxes are scaled by per-species `evaporation_alpha`
metadata in `data/vapor_pressures.yaml`. Each numeric alpha block carries a
source citation, temperature context, uncertainty envelope, and confidence tier.

Tier 2 values are proxy or conditional proxy values. Ca and Ti use Zhang 2014
CaTiO3 melt coefficients, Al uses a broad conflicting-proxy envelope, and
elemental Si is valid only for the inactive pure-element Si branch. The SiO
silicate-vapor path keeps its separate SiO alpha.

Tier 3 species intentionally have no numeric alpha. Cr, Mn, and CrO2 fail loud
when they would otherwise emit nonzero flux, returning a `missing_alpha`
diagnostic instead of silently using alpha=1.0. Prototype continuity runs can
opt into the unmeasured fallback with
`setpoints.chemistry_kernel.allow_unmeasured_alpha_fallback: true`; outputs then
record `unmeasured_alpha_fallback_species`.

The evaporation diagnostic includes `flux_uncertainty_pct`, a per-species map
derived from the alpha envelope. It is alpha-only uncertainty, not a total model
uncertainty: vapor-pressure fits, melt activities, temperature dependence, and
composition dependence remain separate limitations.

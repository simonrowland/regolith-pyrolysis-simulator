# Hashimoto (1990) extraction audit

## Coverage and method

The source has no text layer and no printed data tables. MinerU and Tesseract
both transcribed the numeric prose values. PDF page renders were checked for
pages 1–3; every numeric value retained in the extract was checked against its
page render and is listed in `hashimoto-1990-forsterite-evaporation-kinetics.numeric-cell-audit.csv`.
The figure captions and labels were checked against page 2. Figure 1 flux curves
were not digitized. The printed-row count is 14 alpha statements plus two
residue-composition statements. Three figure records and one qualitative
residue-stoichiometry record preserve relevant source context without adding
plotted points.

The table draft and QA found zero MinerU tables and zero table rows. There is
therefore no printed table row count to reconcile. The row count above is the
complete set of numeric alpha and residue-composition statements in the paper's
requested scope.

## Transcription and semantics

The authors define the free evaporation coefficient as `alpha_v = J_v/J_e`.
The 14 reported alpha statements are stored under the matching MgO, SiO2, and
Mg2SiO4 species. Solid forsterite coefficients remain their own family. The
two coefficients quoted from earlier SiO2 effusion work retain the printed
attribution and its missing temperature. Figure-only rates remain un-digitized.

The alpha values are reported by Hashimoto from measured free-evaporation rates
and theoretical maximum rates, but the numeric curve inputs are not stored.
They are therefore `quoted_attributed` under d-110. The 5 vol% glass amount and
approximately 65 wt% SiO2 residue-glass composition are the authors' measured
residue results and are `measured_direct`. The source's solid-stoichiometric
and molten-almost-stoichiometric statements remain qualitative context because
they contain no numeric composition to store.

The source prints the liquid-SiO2 alternate-species alpha as 1.2 while explicitly
stating that alpha above one is impossible by its definition and may result from
underestimating experimental surface area. The existing migrator's alpha-domain
guard (`simulator/battery/migrate.py::_quantity_contradiction`) therefore produces an unknown
quantity and unavailable value for that source row. This is a domain refusal,
not a schema gap or a changed evidence class. The task's requirement to preserve
the printed value conflicts with its acceptance requirement for no
quantity-unknown observations. Because its quantity becomes unknown, the
migrator also does not bind this row's printed temperature range to identity.
This row is held for a controller ruling; the migrator and schema were not
changed.

The alpha quantity profile also requires an identity composition. The source
prints the pure-material formulas MgO, SiO2, and Mg2SiO4, but no analyzed oxide
percent maps. The current migrator leaves `Identity.composition` unknown for
these alpha rows because its existing composition paths consume structured
oxide maps. Whether the printed formula itself should be mapped to a formula
stoichiometry composition is unresolved; no derived percentages were invented.

## Conditions and phase

The experiment section gives temperature ranges and says the charge cooled
quickly by radiation. It does not say the glass was preserved or altered on
quench, so glass composition rows carry `quench_state: not_stated` located to
page 1. The solid and liquid states are those actually measured after the run;
no in-situ values are printed. The temperature ranges are preserved as ranges,
with no invented midpoint. The alternate SiO2 gas-species coefficients use the
same solid/liquid experimental series; their numeric temperature-range source
is page 2 and is separately located in the extract.

The furnace is described as capable of operating at `1 × 10^-6 mm Hg`; that is
equipment capability, not a run pressure. The source says local vapor pressure
was below one tenth of intrinsic pressure and oxygen fugacity was not
experimentally controllable. Neither statement gives a numerical run total
pressure or fO2. Both conditions are therefore absent from every observation
and remain unknown in identity. No composition percentages are inferred for
the experimental charge.

## Residue volume-fraction schema gap

The printed approximately 5 vol% value is a phase volume fraction. No exact
token represents it: `simulator/battery/enums.py::Quantity` has no phase volume
fraction; `tools/validate_literature_extracts.py::OBSERVATION_TYPES` has no
phase-volume-fraction observation type; `simulator/battery/migrate.py::QUANTITY_ALIASES`
and `simulator/battery/migrate.py::TYPE_QUANTITY` have no exact mapping.
`simulator/battery/enums.py::Quantity.RESIDUE_COMPONENT_COMPOSITION` describes
an oxide component's composition, not phase abundance;
`simulator/battery/enums.py::PHASE_BASIS_TOKENS` has no volume-fraction basis;
`simulator/battery/enums.py::PhaseRole.GLASS` identifies the phase but does not
type its amount. The row is retained as typed refusal with a descriptive
non-token quantity. The 65 wt% SiO2 composition remains a typed
`residue_component_composition` observation for phase `glass`.

## Migration evidence

The isolated migration emits 16 source observation rows, with zero hard
validation issues, two quantity-unknown rows, and zero phase-unknown rows. The
two unavailable values are the 5 vol% typed schema refusal and the 1.2
alpha-domain refusal described above. The solid and liquid alpha ranges reach
the migrator as printed temperature ranges; identity temperature is unknown
because the profile does not accept a range midpoint. The two author
extrapolations reach identity as 1973.15 K and 1673.15 K. Total pressure and
fO2 stay unknown because the source does not print their run values. The
identity-composition issue and the alpha 1.2 gate conflict are recorded in the
isolated migration report.

Three alpha-profile identity checks (the profile requires composition,
total_pressure_Pa, fO2_Pa, and temperature_K):

| Observation | Composition | Total pressure | fO2 | Temperature |
| --- | --- | --- | --- | --- |
| MgO solid alpha | Unknown: formula not mapped | Unknown: not printed | Unknown: not printed | Unknown: printed range 1873.15–2173.15 K; no midpoint |
| SiO2 solid alpha | Unknown: formula not mapped | Unknown: not printed | Unknown: not printed | Unknown: printed range 1833.15–1958.15 K; no midpoint |
| Forsterite extrapolation at 1700 °C | Unknown: formula not mapped | Unknown: not printed | Unknown: not printed | 1973.15 K |

The generated observation/battery store was not regenerated or committed. The
expected addition is 16 source rows: 14 alpha statements (one unavailable by
the existing alpha-domain guard), one glass-residue composition row, and one
typed-refused glass volume-fraction row. Figure and qualitative context records
do not add observation rows.

## Figure-read candidate

Figure 1 contains measured flux-vs-temperature curves for SiO2, MgO, and
Mg2SiO4. These are figure-read candidates for a later digitization task; no
values were read from the curves in this extraction.

# t-1522 design proposal: reaction equilibrium constants

## Decision

Add one quantity, `Quantity.LOG10_K_REACTION = "log10_K_reaction"`. Keep `Quantity.LOG10_KF = "log10_Kf"` restricted to formation from the elements. A general reaction constant has a different identity: its full signed reaction and each term's standard state determine K. Reusing `LOG10_KF` would let an exchange or compound reaction pass a formation-constant profile and would conflate different standard states.

Reuse the existing `Reaction` / `ReactionTerm` representation in `simulator/battery/records.py` and `Identity.reaction`; do not add a second reaction model. Add the minimum missing reaction metadata described below.

The adjacent quantities stay distinct:

- t-912's Sossi `log10_K_star_equilibrium_fit` remains `Quantity.LOG10_K_STAR`, not `LOG10_K_REACTION`. Sossi defines K* = αₑ·γᵢ·K as an apparent fitted vaporization constant. The controller ruling says its standard state is not applicable because the γ inside K* refers to pure liquid oxide. Its printed reaction may use the same `Reaction` structure, but K* must never be scored as a thermodynamic reaction K or an activity.
- t-1515's De Maria ΔᵣH is a separate quantity (proposed token `DELTA_RH`), not `DELTA_FH`. It can use the same reaction structure, with its own `reference_temperature_K` and `reaction_enthalpy_law` (`second_law` / `third_law`) identity axes. Those two fields prevent conflating a measured temperature with the temperature at which ΔᵣH is reported, or second-law with third-law values. This is a separate follow-up quantity, not a reason to broaden `LOG10_K_REACTION`.

Thus one reaction stoichiometry object serves K, K*, and ΔᵣH; each quantity keeps its own value semantics and quantity-specific identity profile.

## Identity and source mapping

| Field | Representation | Failure it prevents |
|---|---|---|
| `identity.quantity` | `LOG10_K_REACTION`; unit metadata is `dimensionless`; the token fixes base 10 | Prevents a reaction K from entering the `LOG10_KF` formation branch or being treated as a dimensional pressure. |
| `identity.species` | Permit `None` for reaction quantities and exclude it from their identity profile. `reaction` is the identity axis. | Prevents an arbitrary “main” product (or the first product) from standing in for a multi-species reaction. Existing species quantities retain their current required species. |
| `identity.reaction.terms` | Existing `Reaction(terms=...)`; `ReactionTerm.coefficient` is an exact `Fraction`, reactants negative and products positive. Preserve coefficient magnitudes and orientation; term order may be canonicalized, but do not rescale or reverse the reaction. | Prevents losing stoichiometry, treating K and 1/K as the same identity, or treating a doubled reaction as the same K. |
| `ReactionTerm.species` | Existing `Species.formula`, `phase`, and `polymorph`, populated per term when printed or page-grounded. Preserve component formulas such as `MgSi1/2O2` as the source's species/component basis; do not silently reduce them to a different basis. | Prevents comparing gas, liquid, crystal, and solution-component terms as if they were the same species. |
| `ReactionTerm.standard_state` | Add an optional `State[StandardState]` per term, reusing the existing `StandardState` type. A reaction K requires a resolved state for every term that contributes to its mass-action quotient; otherwise preserve the value but mark identity incomplete. `identity.reference_state` is not applicable to the whole reaction. | Prevents applying one species' standard state to every term, or substituting a pure phase for a solution-component state. |
| `identity.standard_pressure_Pa` | Reuse the existing positive-Pa identity field for the gas standard pressure. If no gas term uses pressure, record not-applicable; if the source does not define p°, keep it unknown. Keep experimental `total_pressure_Pa` in point conditions, not in this field. | Prevents silently exchanging 1 bar and 1 atm, or mistaking the experiment's total pressure for the gas standard pressure. |
| `identity.temperature_K` | Reuse the existing positive-K field; make one observation per temperature when a source prints a multi-temperature fit/table. | Prevents comparing constants at different temperatures or treating a temperature series as one point. |
| `values.provenance.reaction_as_printed` | Exact, verbatim source reaction string, retained beside the structured identity. | Prevents losing the source equation while parsing or canonicalizing it. |
| `values.provenance.standard_state_as_published` | Exact source convention/notation where printed; the typed term states remain the identity fields. | Prevents a later reviewer from being unable to audit how a term's standard state was assigned. |
| `values.provenance.log_base_as_published` and `units_as_published` | Exact printed notation/unit text. Normalize only when the paper or its table convention establishes the base. | Prevents silently reading `ln K` as `log10 K`, or losing whether the source reported a logarithm or an unlogged K. |
| `value.kind` / `value.point` | `ValueKind.POINT` with a finite dimensionless log10 value for a printed point. | Prevents a point value being read as an interval or pressure. |
| `value.kind` / bound fields | Use `ValueKind.BOUND` and `bound_value`. Retain an exact comparator in existing `bound_operator` only when printed. Add `bound_direction` (`lower` / `upper`) and `bound_inclusivity` (`included` / `excluded` / `unknown`) so `Min.` / `Max.` do not have to be guessed into `>` / `>=` / `<` / `<=`. Preserve the exact source string in `qualifier_as_published`. | Prevents seven Williams bounds being treated as points and prevents guessing strictness from “Min.” or “Max.” alone. Equality at an unknown-inclusivity boundary remains indeterminate. |
| `observation.uncertainty` | Reuse `Uncertainty`: `PRINTED` with the source's verbatim ±/SD text and numeric amount/basis only when unambiguous; use `NONE` when not reported. For log-K errors use a `dex` basis. | Prevents converting uncertainty to K-space, inventing a standard deviation, or replacing absence with zero. |

The generic migrator should not guess a reaction from free text. The extract supplies both the exact `reaction_as_printed` and structured `reaction.terms`; the existing `_reaction_from_plain` reader in `simulator/battery/migrate.py` can deserialize those terms. A source-specific parser may be used only for a closed, page-checked grammar with unambiguous species boundaries, coefficients, and phase notation. It must refuse to construct `Reaction` when a fraction could be either a species-formula component or an external coefficient, a phase legend is needed but unresolved, a species formula is unsupported, or atom balance fails. Preserve the raw string, numeric print, qualifier, temperature, and locator in the typed refusal record in those cases; never fall back to `LOG10_KF`.

For example, the printed `Fe + 1/2 SiO2 + 1/2 O2 = FeSi1/2O2` maps to signed terms `Fe:-1`, `SiO2:-1/2`, `O2:-1/2`, `FeSi1/2O2:+1`, with phases and standards filled only from the paper's notation. The phrase “at 1 atm” is an experimental condition unless the source establishes that it is also p°. An unprinted phase or standard pressure remains unknown; it is not supplied from a default.

## Migration, validation, compatibility, and tests

- `simulator/battery/enums.py`: add the enum token and its dimensionless unit. Keep `LOG10_KF` and its aliases unchanged; keep K* mapped only to `LOG10_K_STAR`.
- `simulator/battery/identity.py`: add a reaction-constant profile requiring `reaction`, `temperature_K`, applicable `standard_pressure_Pa`, and complete per-term standard states. Do not add the new token to `FORMATION_QUANTITIES`. Exclude the species anchor for this profile. Make reaction identity comparison order-independent while preserving coefficient scale and orientation.
- `simulator/battery/records.py`: add per-term standard-state support to existing `ReactionTerm`; extend the existing `ValueKind.BOUND` payload with direction and nullable inclusivity. Existing comparator-based bounds keep their current meaning.
- `simulator/battery/migrate.py`: add only the exact `reaction_equilibrium_constant` source alias and the value-field mapping for the printed log-K number. Lift the Williams reaction into the existing structured reader; map the 39 points to POINT and the seven “Min.” / “min” / “max” rows to direction bounds. Preserve exact source qualifiers and reaction text. Do not infer temperature or conditions from mismatched field names.
- `simulator/battery/validate.py`: reuse `reaction_atom_balance`; reject invalid coefficient/phase combinations and inconsistent term standards. Validate the new direction-only bounds without treating an unknown-inclusivity boundary as strict or inclusive.
- `simulator/battery/score.py`: return `quantity_not_predicted` for `LOG10_K_REACTION` before opening a generic engine path. Store the measured point/bound unchanged. Do not send this quantity through the formation-K scorer.

Focused implementation tests belong in `tests/battery/test_migrate.py` (isolated Williams rows, exact reaction terms, temperatures, raw provenance, ambiguous/unbalanced refusal, point/bound mapping); `tests/battery/test_migrate_r7.py` and `tests/battery/test_schema_admission.py` (closed token, unit, and unchanged LOG10_KF aliases/profile); `tests/battery/test_score.py` (explicit `quantity_not_predicted` before any engine call); and `tests/battery/test_compilation_tier.py` (formation-K behavior unchanged). Run `tests/test_yaml_flow_scalar_commas.py` if any extract flow mappings change.

Back-compatibility gate: serialize a baseline migration of all existing extract rows, then compare against the new migration by observation ID. Require byte-identical serialized identity/value/provenance for every non-target row; permit changes only to the explicitly retyped Williams rows (and later, separately scoped K* or ΔᵣH rows). Assert that the observation/work/experiment ID sets do not change. The new enum, optional species, and optional reaction-term state must leave every old row's serialized form unchanged. No battery-store regeneration belongs in this design task.

Mutation checks for implementation: changing one Williams coefficient sign or phase must fail the reaction/provenance assertion; mapping `reaction_equilibrium_constant` to `LOG10_KF` must fail the enum/profile assertion; converting “Min.” to a point or to a strict comparator must fail the bound assertion; routing this quantity into any generic engine must fail the `quantity_not_predicted` test. These are proposed checks, not executed mutations.

## Prediction decision

No current battery scorer predicts K for an arbitrary structured reaction. `log10K_from_delta_fG_kJ_mol` converts a supplied formation ΔfG into formation log K; the JANAF helper is table/species-specific; the OpenIMCC `_e15_binding_log10_k` route covers a closed oxide-plus-listed-gas family, not arbitrary terms and standards. Therefore this quantity's current result is `quantity_not_predicted`, with the source value stored and never dropped. K* also remains `quantity_not_predicted`: αₑ and γᵢ are part of its fit, not derivable from the reaction's pure-species Gibbs energies alone.

A future engine route, only for reactions whose every species, phase, standard state, temperature, and pressure convention resolve, would compute

`ΔᵣG° = Σᵢ νᵢ ΔfGᵢ°`

with products positive, then

`log10 K = −ΔᵣG° / (R T ln 10)`.

Unit check: for ΔᵣG° in J/mol, `R = 8.314462618 J mol⁻¹ K⁻¹` and `T` in K make the denominator J/mol and the ratio dimensionless; for kJ/mol inputs multiply the numerator by 1000 (or use `R = 0.008314462618 kJ mol⁻¹ K⁻¹`). A future independent JANAF anchor is `H2(g) + 1/2 O2(g) → H2O(l)` at 298.15 K: using ΔᵣG° ≈ −237.13 kJ/mol gives log10 K ≈ +41.56. This is a proposed unit/sanity check, not a current scorer result.

## Corpus impact

- `aeca46ee3` Williams extract: `rg` finds 46 `schema_gap_reaction_equilibrium_constant` refusals; 39 are points and 7 are printed direction bounds (three `min`, three `max`, one `Min.`). These are the first 46 rows this quantity would admit after structured reaction mapping.
- Current `data/literature/extracts` search finds 12 more `schema_gap_reaction_equilibrium_constant` items in Shornikov 2020. They are source-level refusal entries and need their own per-source structured mapping before any store impact is claimed.
- Sossi 2019: `rg` finds 33 literal `quantity: log10_K_star_equilibrium_fit` declarations in the legacy extract; t-912's qualified-token inventory reports 34 K* records. Reconcile the one-count difference before that separate migration. They do not become `LOG10_K_REACTION` rows.
- t-1515 names 9 SiO2 and 5 AlO reaction-enthalpy rows (14 total); they require `DELTA_RH`, not this log-K quantity.

Expected store delta for this design-only task: zero. If the first consumer is later migrated, expect 46 existing Williams observation IDs to change from typed-unavailable to 39 POINT plus 7 BOUND values; do not add duplicate observations or regenerate/commit the battery store here.

No code or tests were changed or run. The controller's primary-source extract and the prior worker report were read; no source-page re-audit was performed, so standard-state and phase assignments remain requirements for the implementation, not assumptions in this proposal.

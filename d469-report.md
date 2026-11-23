# d-469 report (d469-1810)

Melt fO2 is bounded below by the most oxidizing condensed-metal couple present. The owner is `simulator/metal_saturation_floor.py`, not `simulator/fe_redox.py`. `fe_redox` stays a leaf: builtin providers already import it, and a floor that also imported the vapour provider would cycle. The new module calls `fe_metal_saturation_log10_fO2_bar` and does not reimplement the Holzheid line. Si, Cr, Ti, Mn, and Ni use `simulator.chemistry.ellingham_thermo` only. No new thermo was added. Na, K, and Mg are omitted when `ellingham_metal_phase_kind` is gas. Al, Ca, and P are not in the named set. P has `n_ox = 1/5`, so a trace of P2O5 would sit above Si and hide the shuttle.

`MELT_DISSOCIATION_PO2_MIN_BAR` (1e-30 bar) remains the float guard inside `physical_melt_dissociation_pO2_bar`. It is not a published melt state.

Engines: `engines/engines.local.toml` is present in the seat and gitignored. MAGEMin and alphaMELTS were not started. No pytest was run. A regolith-wide CI hold is in force, and that hold overrides the usual remote-CI rule.

## Writer

The 1e-30 bar value is `physical_melt_dissociation_pO2_bar` clamping a published log at or below −30. Two publishers could put that log there after `c3_na_shuttle_reduction`.

The redox-source tangent, `simulator/core.py:7016`, wrote the Euler candidate with no metal floor. That is the writer that could sit below every real couple. The d-463 ledger path, now `simulator/core.py:6416`, floored on Fe only. Fe does not bind once the shuttle has collapsed a_FeO.

At 1423.15 K the pure stoichiometric Fe line is −12.886. The activity term is `2 log10(a_FeO)`. The Fe line crosses −30 at `a_FeO ≈ 2.8e-9`, and it drops below the Si line at `a_SiO2 = 0.41` (−24.499) once `a_FeO ≈ 1.6e-6`. The unit test uses `1e-6`, where Fe is −24.886 and Si binds. Kress does not send a_FeO to 0 just because fO2 is low. The Fe couple is absent only when the ledger activity is non-positive. Either case leaves Si as the floor when Cr, Mn, and Ni are gone. Residual FeO or Cr2O3 still beats Si: 0.05 wt% FeO keeps a_FeO near 1.6e-3 and a Fe floor near −18.5.

When the floor binds, the notice is `melt_fO2_metal_saturation_floor:<metal>` with the metal, T, the floor, and the unfloored log. The result is published. Nothing is refused. A candidate already within 1e-9 dex of the floor does not emit a second notice.

## Writer and consumer inventory

Routed means the published number passes through `melt_metal_saturation_floor_log10_fO2_bar` (via `apply_melt_metal_saturation_floor` or the ledger, which calls it).

| Site | Line | Routed |
| --- | --- | --- |
| `core._re_reference_melt_fO2_to_temperature` | assign 6032, floor 6030 | yes |
| `core._refresh_oxygen_reservoir_without_exchange` | assign 6168, floor 6159 | yes |
| ferric-lock republish | assign 6545, ledger 6544 | yes |
| redox-source copy of the re-referenced base | assign 6847, re-reference 6815 | yes |
| reducing-books publish | assign 6932, ledger 6931 | yes |
| redox-source tangent | assign 7016, floor 7009 | yes |
| O2 bubbler | assign 7469, floor 7465 | yes |
| Fe respeciation publish | assign 7925, ledger 7924 | yes |
| exchange constructor copy of the base | assign 8101, re-reference 8042 | yes |
| exchange tangent | assign 8248, floor 8241 | yes |
| `runner/__init__.py` keyword into refresh | 4726 | yes, inside refresh |
| vapour intrinsic dissociation | floor 3048, clamp 3073 | yes |
| `EquilibriumMixin` dissociation | floor 620, clamp 641 | yes |

Not routed, on purpose:

- `core.py:9212` (restored at 9217) is the native-Fe trial probe. It writes fO2 and puts the previous value back. Native onset still calls `fe_metal_saturation_log10_fO2_bar` directly.
- `_finite_oxygen_reservoir_fO2_log` is the finite-value guard. Callers pass an already floored log. The temperature-shift candidate is floored after that guard returns, before the assign at 6032.
- `_ferric_release_aim_fO2_log` (6320) is the split aim. Flooring the aim would stop at q(floor) and leave Fe2O3 unbooked.
- `diagnostic_helpers/binary_pot_battery.py:3808`, `scripts/sso_r_validation_map.py:866`, and `benchmarks/melt_activity_benchmark.py:587` plant an observed or scenario fO2. They are not the melt publisher.
- `OxygenReservoirState` default −9.0 in `state.py` is the dataclass seed, not a runtime writer.

Vapour activities come from `_dissociation_metal_floor_activities` (`vapor_pressure.py:1418`), which calls `_high_t_activity_for_parent` and passes stoichiometric a_FeO evaluated at the unfloored log. Equilibrium and the core ledger use `activities_for_metal_saturation_floor`, which is `melt_oxide_activity(...).thermodynamic_parent_activity()` plus the caller's a_FeO. With no OpenIMCC authority those two activity owners are the same function. The core Fe activity is the existing 8-step fixed point (`_consistent_stoich_feo_activity`), not table-gamma FeO.

A 298.15 K seed was probed locally. `kress91_ferrous_feo_activity` does not raise. The Fe floor at a_FeO = 0.2 is −80.5, so a −9 seed is not lifted. The Si segment starts at 1100 K and is skipped.

While iron remains, q outside (0, 1) still snaps to `min(floor, effervescence ceiling)` (d-463). With total Fe ≤ 0 the current log is only raised. It is not pulled down onto the Si line.

## Derivation

Owner: `ellingham_delta_g_kj_per_mol_o2("Si", T)` and `feo_iw_log10_fO2_bar`. R = 8.31446261815324 J/(mol·K).

```
log10(fO2_sat,Si) = ΔG°_f(SiO2) / (R T ln 10) + log10(a_SiO2)
```

with a_Si = 1. The same algebra with the segment's `n_ox` covers Cr, Ti, Mn, and Ni. The comment on `_ellingham_metal_saturation_log10_fO2_bar` holds the unit check.

| T | Basis | ΔG° kJ/mol O2 | log10 fO2 at a=1 | IW | dex below IW |
| --- | --- | --- | --- | --- | --- |
| 1423.15 K | Si(s) + O2 → SiO2(II), range (1100, 1685) K | −656.956480 | −24.112156 | −12.275019 | 11.837 |
| 1800 K | Si(l) + O2 → SiO2(l), range (1696, 2500) K | −589.452907 | −17.105144 | −8.731302 | 8.374 |

R T ln 10 is 27245.861893 J/mol at 1423.15 K and 34460.563825 J/mol at 1800 K. The 1423.15 K segment is solid, so the gap is wider than the usual 9–10 dex liquid claim. The 1800 K liquid line is 8.374 dex below IW. `a_SiO2 = 0.41` moves the 1423.15 K line by `log10(0.41)` to −24.499372.

Fe saturation, Holzheid wüstite, not reimplemented: a_stoich = 1 is −12.885722 at 1423.15 K (IW − 0.611) and −9.223516 at 1800 K. a_stoich = 0.45 is −13.579297 at 1423.15 K, and that Fe line still binds over Si at 0.41.

Crystal JANAF Antoine for pure Si at 1423.15 K (`crystal_janaf`, valid 100–1685 K, A = 12.645587188241636, B = 23452.94759360651, C = −0.0261074340068919), evaluated by `_antoine_log10_pressure`: log10(P/Pa) = −3.83431854, P_sat = 1.464473e-4 Pa.

## Hour-18-like Si(g)

Local provider dispatch, not pytest. Composition mol: SiO2 7.5, Al2O3 1.5, CaO 2.2, MgO 2.5, Na2O 1.3. No FeO, Cr, Ti, Mn, Ni, or P. T = 1423.15 K, `intrinsic_fO2_log = −40`, `high_t_melt_activity = constant_gamma`.

| | Before (1e-30 clamp) | After (Si floor) |
| --- | --- | --- |
| dissociation log10(fO2/bar) | ≤ −30, clamped | −24.487515, clamp false |
| dissociation pO2 | 1e-30 bar | 3.255e-25 bar |
| a_SiO2 on the vapour path | 0.421348 | 0.421348 |
| P_Si | 65.32 Pa | 2.007e-4 Pa |
| notice | none | `melt_fO2_metal_saturation_floor:Si` |

65.32 Pa scaled to a_SiO2 = 1 is 155.0 Pa. That is the brief's 155 Pa at a = 1. The brief's 63.95 Pa is the same CEA channel at a_SiO2 ≈ 0.41. This composition's activity is 0.421, so the unfloored pressure is 65.32 Pa.

Pure SiO2 publishes the same floored P_Si, 2.007e-4 Pa. a_SiO2 cancels between the floor and the pO2 exponent of −1. The CEA reaction SiO2(l) → Si(g) + O2 at that floor is 1.371 times the JANAF crystal Antoine (0.137 dex). A second P_sat cap was not added and the 1e-30 guard was not retuned. The regression therefore checks the notice kind, the Ellingham line at the published activity, `P_Si < 1` Pa, and `P_Si / P_sat < 10`. Both pressure bounds reject 65 Pa. Removing the Si row makes the notice missing and P_Si tens of pascals, so the regression goes red.

The b491 account (FeO 0.30, MgO 0.20, SiO2 0.50, Na2O 0.02 mol) at 1700 K binds on Fe, not Si. An intrinsic log of −400 or −30 is published at −10.116. P_Si falls from the inverted multi-GPa / 1e21-ratio regime to 9.32e-11 Pa. The clamp flag is false and the old pO2-floor-inversion notice is absent. An intrinsic log of −9 stays unfloored, because the Fe line is below it.

## Tests

`tests/test_metal_saturation_floor.py`:

- `test_fe_floor_binds_while_feo_is_present` — FeO 0.45 and SiO2 0.41 at 1423.15 K. The metal is Fe and the value equals `fe_metal_saturation_log10_fO2_bar`.
- `test_si_floor_binds_once_feo_activity_collapses` — FeO 1e-6. The metal is Si. The value is the Ellingham owner's ΔG, not a copy of the floor body. Pure Si at 1423.15 K is more than 11 dex below IW. Pure Si at 1800 K lies between 8 and 10 dex below IW.
- `test_post_shuttle_si_pressure_stays_under_pure_si_saturation` — the dispatch above.

`scripts/mutation_melt_fo2_si_couple.py` deletes the unique Si row and expects that regression to fail, then restores the file. It was not executed.

Not submitted, for when the hold lifts. Whole file, no `-k`, no node id, no `--timeout`:

```
"$GOALFLIGHT_REMOTE_RUN" --kind gate --cmd-id regolith-focused-tests -- python3 -m pytest -q -p no:cacheprovider tests/test_metal_saturation_floor.py
```

The mutation script shells pytest itself (`python3 scripts/mutation_melt_fo2_si_couple.py`) with `-n 0` and a node id, which the gate command shape does not accept. Run it on the studio as its own command after the hold lifts. Do not run it on the laptop.

`tests/import_layers.toml` lists `simulator.metal_saturation_floor` at layer 1, beside `fe_redox`. No baseline entry was added. `tests/test_import_boundary.py` was not run.

## Expected moves

No golden or receipt in this seat pins hour-18 Si(g) or a ≤ −30 melt fO2. `tests/goldens/` has no such pin. Do not hand-edit goldens. Campaign walks that published a melt log below the binding couple will move: fO2 rises to that couple, and Si(g) at the old 1e-30 clamp falls by about `10^(floor − (−30))`. On the shuttle-shaped state that factor is about 3.3e5, from 65.32 Pa to 2.007e-4 Pa.

These tests supply an intrinsic log at or below −30 with FeO and SiO2 present, and they assert the 1e-30 inversion. They will go red until rebaselined. They were not edited.

- `tests/test_b491_po2_floor_inversion.py::test_provider_flags_floor_inversion_and_completes`
- `tests/test_b491_po2_floor_inversion.py::test_provider_flags_exact_minus_30_without_clamp_warning`
- `tests/test_b491_po2_floor_inversion.py::test_si_mg_na_mass_action_ratio_at_floor_vs_1e9`
- `tests/test_b491_po2_floor_inversion.py::test_floor_notice_reaches_carrier_extra_and_pareto`
- `tests/test_b491_po2_floor_inversion.py::test_early_floor_notice_survives_clean_timestep_wall_merge` (it takes the Si inversion notice from the −30 dispatch)

Helper tests that call `physical_melt_dissociation_pO2_bar` or `melt_dissociation_pO2_floor_inversion_notice` directly do not move. `test_b984_sio_uses_actual_oxygen_beyond_evaluator_band` does not pass `intrinsic_fO2_log`, so it stays on transport pO2. `test_clean_in_band_timestep_publishes_without_floor_notice` uses −9, which is above the Fe floor on that account.

## Refactor

Consolidated: the Fe saturation formula was not copied. `_fe_metal_floor_log10_fO2` was deleted. Grep shows no remaining reference. One owner publishes the max over the named couples.

Left in place, and why:

- `physical_melt_dissociation_pO2_bar` and `MELT_DISSOCIATION_PO2_MIN_BAR`. Float guard, required to stay.
- The equilibrium `min(a_M, 1) * P_sat` rail. Different bound. Standard-reaction rows return before it. Deleting it would change species that do not use the dissociation floor.
- The scenario planters and the native-Fe trial probe listed above.
- The ferric-release aim and the finite-log guard.

No behaviour-preserving golden commit. The change is the new max-over-couples rule, and pytest is forbidden under the hold. Physics was not added to `core.py` beyond one call into the owner.

A commit cannot contain its own sha. The worker COMPLETE line names the tip commit that adds this file.

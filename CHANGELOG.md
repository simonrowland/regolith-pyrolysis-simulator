# Changelog

Notable changes to the regolith-pyrolysis-simulator. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); the project is research-stage (pre-1.0),
so minor versions may carry significant changes.

## [Unreleased]

Post-0.5.4 hardening + audit closures. Will land as `0.5.4.1` on the next
release boundary; commits land on `main` between pushes. 20 commits in
the range, all reviewed by codex / `gstack /review` per chunk-review
protocol + midflight cumulative review (HOLD-MAJOR → fixed inline) +
post-batch morning review (HOLD, V2O5 P1 → fixed inline).

### Added — defensive guards + structured seams

- **A2** — `simulator/overhead.py::OverheadGasModel._pipe_conductance`
  fail-closed on `T_K <= 0`, `L <= 0`, `d <= 0`, negative `p_mean_Pa`.
  Closes the 0.5.4 codex /challenge P3.
- **B3 / M1** — `simulator/chemistry/kernel/registry.py::ProviderRegistry.replace_for_test`
  public test-seam replacing direct `_authoritative` mutation.
  `tests/test_extraction_ledger.py` migrated.
- **B5 / CW1** — `simulator/extraction.py::_build_mre_voltage_sequence`
  wired through `data/setpoints.yaml § mre_voltage_sequence.sequence`
  with the canonical fallback ladder. Midflight P1: hardened YAML with
  explicit `min_hold_hours` per species matching legacy hardcoded values.
  Published YAML now adds Na2O / K2O to the default ladder — Q1 documents
  the choice. V2O5 was initially added but removed in the morning P1
  fix (see "Fixed" section) because V is absent from the supporting
  simulator tables.
- **B1-tunable / CW3** — `simulator/condensation.py::apply_setpoints_condensation_temperature_overrides`
  merges operator-supplied per-species condensation temperatures from
  `data/setpoints.yaml § condensation_train.condensation_temperatures_C`
  into the in-source fallback dict. Idempotent on first sim build.
  SiO=1050 °C is now explicitly documented as the engineering midpoint
  of the documented 900-1200 °C zone, NOT a literature-derived constant
  (per codex /review corpus scan).
- **E3** — `HourSnapshot.knudsen_regime_summary` field carrying per-tick
  Knudsen-regime visibility from the latest condensation route
  (`status` / `knudsen_number` / `knudsen_regime` / `regime_factor` /
  `warnings`). Complements the F3 hard refusal at `Kn ≥ 10` with
  earlier-warning visibility.

### Fixed — post-push review findings

- **`simulator/melt_backend/base.py::EquilibriumResult.liquidus_T_C`** —
  moved to the END of the dataclass to preserve positional-constructor
  ABI. Post-push codex /review P2.
- **Midflight P1 — B5 hold-hours**: published YAML now carries explicit
  `min_hold_hours` per species so the YAML-driven ladder reproduces
  the historical hardcoded MRE_BASELINE step-advance behavior
  (Al2O3=8, MgO=5, CaO=10 vs the default-3 silent shift).
- **Milestone P1 — `simulator/campaigns.py:172`**: future-campaign
  `campaign_override pO2_mbar` was being applied at
  `configure_campaign()` transition time without the W5 atmosphere
  switch. Now mirrors the active-path fix.

### Added — coverage

- **A1** — W5 + W7 + W8 live-path integration tests
  (`tests/test_0_5_4_live_path_integration.py`)
- **W4** — Phase A atmosphere × headspace branch-test matrix
  (`tests/test_overhead_accounting.py`, parametrized; partition guard)
- **E1a** — north-star recipe-correctness baseline diagnostic harness
  (`tests/test_north_star_baseline.py`; mass-balance hard gate;
  threshold tightening deferred to E1b post-Phase-D)
- **E7** — `web/events.py` pO2 cross-layer integration tests
  (direct `session.adjust("pO2_mbar")` lever)
- **B1-tunable tests** — apply/restore round-trip + defensive paths
  + end-to-end simulator construction
  (`tests/test_condensation_temperature_overrides.py`)
- **W3** — evap-engine defensive axial-clamp on dict input + bool
- **W2** — `clamp_stir_state` UserWarning on unknown dict keys
- **B5 / MRE voltage sequence YAML** parsing + fallback tests
  (`tests/test_mre_voltage_sequence_yaml.py`)

### Hygiene — docs

- **W1 / F1** — `tests/test_sio_yield_regression.py` Stage 4 SiO routing
  invariant rewritten honest to post-Phase-A flip (Stage 4 > Stage 3
  under default `radial=1.0` per CHANGELOG 0.5.3 "Known limitation")
- **B2 / CW4** — `simulator/condensation.py` Stage 3 docstring polish
  for post-0.5.3 routing (uses canonical `StirState(axial=6.0, radial=1.0)`
  vocabulary; honest about the routing trade-off)
- **E8 partial** — `docs/output-interpretation.md` documents the new
  W8 + E3 HourSnapshot diagnostic surfaces
- **B4 / CJ2015** — already-tracked corpus fixture confirmed has
  `intents_exercised` field (closure)

### Added — north-star product classifier (E6)

- **E6a** — `simulator/three_product_report.py::classify_products(sim)`
  projects `PyrolysisSimulator.product_ledger()` output onto the four
  north-star product classes documented in `CLAUDE.md § 5`: metals + O₂,
  pure silica glass (Stage 3 capture), industrial mixed glass
  (cleaned_melt residual), refractory ceramic rump (by physics). 5-bucket
  output dict with `unclassified` future-proofing bin.
- **E6b** — `simulator/three_product_report_markdown.py::format_three_product_markdown`
  human-readable markdown report wrapping the E6a classifier. 1-line
  totals snapshot + per-class expansion + per-species kg breakdown.
  Operator-noise reduction: unclassified section appears only when
  non-empty; sub-noise values render as `—`; values <1 kg use scientific
  notation. 13+13 tests across the E6 series.
- **E6c** — `simulator/three_product_runner.py` runner CLI wrapping E6a +
  E6b. Argparse entry point `python -m simulator.three_product_runner
  --feedstock <id> --campaign <label> --hours <N> --output <path>
  --format {markdown,json}`. Programmatic `run(...)` entry point for
  test + library use. JSON output carries metadata header
  (`feedstock_id`, `campaign`). 8 tests covering markdown / JSON
  output, subprocess invocation, `--hours 0` baseline, argparse
  rejection of invalid formats.

### Fixed — morning review findings (post-batch HOLD)

- **Morning P1 — `data/setpoints.yaml`**: V2O5 had been included in the
  published MRE voltage ladder via B5, but V is absent from
  `simulator/state.py::OXIDE_SPECIES` / `OXIDE_TO_METAL` / `MOLAR_MASS`
  + `simulator/electrolysis.py` energy tables. `_step_mre` was silently
  no-op'ing on V2O5 — operator-visible "running" lie with zero output.
  V2O5 removed; YAML carries an explicit comment explaining the
  prerequisite work for re-adding.
- **Morning P2 — `tests/test_mre_voltage_sequence_yaml.py`**: NEW
  YAML-vs-tables support matrix test (`test_yaml_ladder_species_all_
  supported_by_simulator_tables`) guards future YAML edits — adding a
  species to the YAML now requires landing the matching simulator
  tables, fail-loud at test time before the recipe ships a silent no-op.
- **Morning P2 — `tests/test_condensation_temperature_overrides.py`**:
  NEW end-to-end propagation test refutes the reviewer's concern that
  B1-tunable's YAML override didn't flow through to
  `_species_condensation_temperature_C`; documents the canonical
  read path through the mutated `CONDENSATION_TEMPS_C` module dict.

## [0.5.4] — 2026-05-28

D5/D6 cleanup wave: defensive hardening + historical-audit closures + post-push
P2 fix. Eight chunks (W1–W8) bundled into four commits (Wave 1 + Wave 2 + Wave 3
+ milestone-review fixes). No physics-shifting feature work; no fixture regen
required. Mass-balance closure ≤5×10⁻¹² % invariant unaffected.

Three reviewers (Wave 1 chunk-review codex + R1 milestone codex /review + R2
milestone codex /challenge) converged clean post-fix: 0 P0/P1 remaining, all
P2s fixed inline, deferred P3s tracked in `docs-private/`.

### Changed — atmosphere switch on campaign-override pO2 (post-push P2 + milestone P1)

- **`simulator/session.py::SimSession.adjust("campaign_override", …, field="pO2_mbar")`** (W5, post-push P2). Mirror of the Phase C P2 direct-adjust fix on the campaign-override write path: when the operator commands a positive pO₂ via this path on the active campaign, also switch `melt.atmosphere` to `CONTROLLED_O2` so the 1/√pO₂ Ellingham SiO suppression goes live. `pO2_mbar = 0` leaves atmosphere alone (clearing the setpoint, NOT requesting controlled-O₂). Inactive-campaign overrides write to the dict only — no live melt touch.
- **`simulator/campaigns.py::configure_campaign()`** (milestone-review P1, codex /challenge unique finding). Future-campaign `campaign_override pO2_mbar` was being applied at campaign-transition time without the atmosphere switch — the same class of bug as the active-path fix above, on a different code path. Same fix pattern applied: when the stored override pO2 is positive, switch atmosphere to `CONTROLLED_O2` after the bare pO2 write. This closes the last remaining `melt.pO2_mbar` writer that could leave atmosphere stale in a non-O2-controlled mode.

### Changed — MRE current efficiency

- **`simulator/electrolysis.py::current_efficiency()`** now uses bounded FeO/electronic-loss bands instead of the FeO-independent 0.75-asymptote curve. The replacement remains grounded-heuristic and uncertified; `data/corpus_version.yaml` was bumped so cached MRE rows resweep without a cache-key schema change.

### Added — `EquilibriumResult.liquidus_T_C` structured field (W6, M3 historical-audit closure)

- **`simulator/melt_backend/base.py::EquilibriumResult.liquidus_T_C`** — new `Optional[float]` field. Populated by backends that compute a liquidus alongside the per-T equilibration; `LiquidusSolidusResult` (in `simulator/melt_backend/liquidus.py`) remains the canonical surface for the dedicated liquidus-finder workflow.
- **`simulator/melt_backend/alphamelts.py`** — AlphaMELTS subprocess parser now writes the value to BOTH the structured field AND the legacy `AlphaMELTS liquidus_C=...` warning string. Backward-compat: any external log consumer that scrapes raw warnings remains unaffected.
- **`engines/alphamelts/parser.py::project_equilibrium_to_diagnostics`** — flipped precedence to prefer the structured `liquidus_T_C` field, falling back to the legacy warning regex. Pre-W6 the parser had a comment anticipating exactly this migration ("today-hook adapter writes it as a warning string ... until the adapter grows a structured field").

### Changed — live mole-weighted M_avg for pipe conductance (W7, CW5 historical-audit closure)

- **`simulator/overhead.py::_mean_molar_mass_kg_mol(species_kg)`** — new module-private helper. Mole-weighted mean molar mass `Σ kg_i / Σ (kg_i / M_i)` from a species→kg mapping (or fallback to `DEFAULT_PIPE_M_AVG_KG_MOL = 0.040` when input is empty / None / all-unknown). Defensive against NaN, non-coercible, and zero / negative kg entries.
- **`simulator/overhead.py::OverheadGasModel._pipe_conductance`** gains an optional `species_kg_for_M_avg` kwarg. `estimate_transport_state` threads `evap_flux.species_kg_hr` through so the ideal-gas density uses the live mole-weighted average instead of the legacy hardcoded `M_avg = 0.040` "mix of SiO, Fe, Na vapors ~40 g/mol" placeholder. Real recipes span M_avg ≈ 0.023 (alkali sweep, Na-dominant) to ~0.046 (Fe vapor mid) — a factor-of-2 swing the placeholder was hiding. Backward-compat: callers without the kwarg get the documented fallback bit-for-bit.

### Added — metal-projection drift audit (W8, M2 historical-audit closure)

- **`simulator/extraction.py::ExtractionMixin._audit_metal_projection_drift()`** — per-species drift between `process.metal_phase` (AtomLedger account) and the sum across `train.stages[*].collected_kg` (UI projection). Iterates the UNION of species across both surfaces (milestone P2 fix), so projection-only stale states surface with negative drift instead of being silently invisible.
- **`HourSnapshot.metal_projection_drift_kg`** — new `Dict[str, float]` field carrying the audit dict. Diagnostic only; the global `mass_balance_error_pct ≤ 5e-12 %` invariant remains the hard gate. Empty dict means in sync across both surfaces.

### Hardening — defensive clamps + audits + branch coverage

- **`engines/builtin/evaporation_flux.py`** (W3): direct-provider callers (ACP probes, tests, ad-hoc IntentRequest construction) bypassed the canonical `simulator/evaporation.py::_pack_controls` clamp on `stir_factor`. Now both dict-form (`{"axial": …}`) and scalar paths apply `clamp_stir_factor` defensively. Main sim path unchanged (clamps were already idempotent).
- **`simulator/state.py::clamp_stir_state`** (W2): UserWarning naming unknown dict keys + valid schema + obvious typo hint (e.g., `{'radail': 8}` → "Common typos: 'radail' → 'radial'"). Behaviour unchanged; only the warning is new.
- **`tests/test_overhead_accounting.py`** (W4): parametrized branch unit tests covering the full 6-atmosphere × 2-headspace decision matrix for the Phase A commanded-pO₂ floor, plus an `_O2_FLOORED | _O2_NOT_FLOORED == set(Atmosphere)` partition guard so future enum additions can't silently miss matrix coverage.
- **`tests/test_sio_yield_regression.py`** (W1): rewrote the stale Stage 4 < Stage 3 SiO comment block to be honest about the post-0.5.3-Phase-A routing trade-off (Stage 4 > Stage 3 under default `radial=1.0`). Added an explicit ordering invariant assertion so a future defaults change that restores Stage 3 dominance forces a CHANGELOG update.

### Carried forward — deferred follow-ons (P3s, tracked in `docs-private/`)

- W8 audit completeness: future "union-key audit" with explicit projection-only coverage already landed inline as the milestone-review P2 fix; downstream integration coverage on the real-run paths (W7 live M_avg + W5 pO2 switch) remains mostly indirect via golden fixtures, defer to 0.5.5.
- `_pipe_conductance` `T_K ≤ 0` numerical edge-case hardening — pre-existing, low priority.
- Phase D feature flips deferred to dedicated sessions (D2 class-4 stochastic flip-on sim → D3 freeze-gate default-on flip; D4 class-2 cross-engine consistency).

## [0.5.3] — 2026-05-28

Physics-correctness sweep over 0.5.2. Two file-disjoint chunks dispatched as
parallel background workers; each chunk passed independent codex chunk-review
(Phase A: 1 P1 + 1 P2 + 1 P3; Phase B: 2 P1 + 1 P2 + 2 P3) with all P1+P2
findings fixed inline.

### Changed — finite-headspace default-on (Phase A)

- **`simulator/overhead.py::DEFAULT_HEADSPACE_CONFIG['enabled']`** + **`data/setpoints.yaml § overhead_headspace.enabled`** flipped to `True`/`true`. Pre-flip the simulator wrote a synthetic conductance-ratio-derived `gas.composition['O2']` whenever the recipe pO₂ setpoint was set under any atmosphere, which masked the real holdup-feedback the finite-headspace model was meant to surface. User-pinned per `Q1 DEFAULT-ON GLOBALLY` in `docs-private/goal-finite-headspace-2026-05-21.md` (private).
- **Commanded-pO₂ floor mirror** at `simulator/overhead.py::_update_finite_headspace` + `simulator/equilibrium.py::_commanded_pO2_bar`. When atmosphere is in the `_O2_CONTROLLED_ATMOSPHERES` family (`CONTROLLED_O2` / `CONTROLLED_O2_FLOW` / `O2_BACKPRESSURE`), recipe `melt.pO2_mbar` survives the holdup overwrite. `HARD_VACUUM` and `PN2_SWEEP` are deliberately excluded — an uncontrolled run gets the real trajectory rather than a synthetic floor.
- **Wall-sweep CLI atmosphere switch** at `simulator/runner.py::_apply_sio_wall_sweep_controls`. Operator commanding pO₂ via the wall-sweep mode now also switches `melt.atmosphere` to `CONTROLLED_O2`, restoring the SiO suppression lever ("1 mbar pO₂ glass / clean-alkali mode") that became a no-op under finite-headspace default-on with a PN2_SWEEP base atmosphere (Phase A chunk-review P2 fix).
- **`P_total ≥ pO2` invariant** at `simulator/overhead.py`. Pre-fix the controlled-O₂ floor wrote `gas.composition['O2']` to the commanded pO₂ but left `gas.pressure_mbar` at the holdup-derived value (often 0.0 mbar), creating an impossible gas state. The floor now also raises the reported total pressure to honour the commanded pO₂ (Phase A chunk-review P1 fix).

### Net behaviour shift (Phase A)

- SiO yield at C2A with default stirring: **+146% evolved** (real holdup-derived vacuum-floor pO₂ vs synthetic floor) per regenerated `lunar_mare_low_ti_c2a.json` / `mars_basalt_c2a.json` fixtures.
- C2A wall-deposit at 1050 °C cold liner: `8.28e-06 → 2.00e-05 kg` (+142%) per `test_sio_step_wall_deposit.py:89` numeric pin update. Fouling-threshold structure (deposit at 1050 °C, none at 1400/1500 °C) unchanged.
- Mass-balance closure stays ≤5×10⁻¹² % per tick (max observed 6.59×10⁻¹³ %).

### Changed — 2-axis turbulent stirring (Phase B, user-requested)

- **`simulator/state.py::StirState`** dataclass replaces the scalar `MeltState.stir_factor` field. `StirState(axial=6.0, radial=1.0)` decomposes induction stirring into two physically distinct axes:
  - **Axial** (vertical EM stirring) drives melt-side surface renewal → linear multiplier on the H-K-L evaporation rate at `engines/builtin/evaporation_flux.py`. Default `6.0` preserves the 0.5.2 C2A operator-tuned stirring intensity.
  - **Radial** (in-plane EM vortex) drives gas-side bulk-to-wall mass-transport → stir-Sherwood enhancement at `simulator/condensation.py::_stirring_enhanced_sherwood`. Default `1.0` is the laminar pipe asymptote (Sh = 3.66, no stirring).
  - Office-hours framing rationale: this maps directly to industrial multi-coil EM stirrer design where each coil winds gets independent phase control; pre-0.5.3 the single scalar `stir_factor` drove BOTH consumers, leaving operators with no way to tune one without the other.
- **`clamp_stir_state(value)`** helper accepts dict / scalar / `StirState` / `bool` / `None` / non-finite inputs, routes each axis through `clamp_stir_factor` (the per-axis operator-boundary clamp to `[0.0, MAX_STIR_FACTOR=10.0]`). Mapping inputs with missing axis keys default the missing axis to `1.0` (laminar baseline) — partial dicts signal "operator only meant to touch one axis".
- **`MeltState.stir_factor`** preserved as a property+setter aliasing `stir_state.axial` (backward-compat for pre-0.5.3 attribute-read/write callers). Construction-time `MeltState(stir_factor=X)` is NOT supported in 0.5.3+ (TypeError); migrate to `StirState(axial=X)` or post-construction `melt.stir_factor = X`.
- **Operator boundary writers** (`simulator/session.py`, `simulator/campaigns.py`) accept the legacy scalar `stir_factor` (→ axial only) AND a new `stir_state` dict / dataclass (→ both axes via `clamp_stir_state`). Campaign YAML override precedence is per-axis: when both `stir_factor` and `stir_state` are supplied, `stir_state.axial` wins only when explicitly named in the dict; otherwise the `stir_factor` value carries through (Phase B chunk-review P2 fix).
- **`CondensationModel.radial_stir_factor`** initialised to `None` (not-configured sentinel). Legacy direct-construction callers (`configure_operating_conditions(stir_factor=X)` without `radial_stir_factor=`) get the pre-0.5.3 Sh-enhancement behaviour via the helper's `radial_stir_factor is None → fall back to stir_factor` branch. The operating-history snapshot distinguishes `radial_stir_factor: None` (never configured) from `radial_stir_factor: 0.0` (explicit halt signal) (Phase B chunk-review P1 fix).
- **Per-axis `MAX_STIR_FACTOR`**. The 10× "melt-flying-out-of-the-pot" ceiling now applies to each axis independently — industrial multi-coil EM stirrers carry independent budgets per axis, so the ceiling is set by the worst single axis, not the L2 sum.

### Known limitation — Stage 4 SiO carryover exceeds Stage 3 product in C2A baseline

Under default C2A conditions with `stir_state = StirState(axial=6.0, radial=1.0)`, the Phase A finite-headspace flip surfaces +146% more SiO into the gas phase than 0.5.2 did. Most of that extra SiO still carries over downstream past the designated Stage 3 SiO zone into Stage 4 (alkali/Mg cyclone) at C2A — `tests/fixtures/sio_yield/lunar_mare_low_ti_c2a.json` shows `stage_4_alkali_mg_carryover ≈ 5.07e-4 > stage_3_sio_zone_product ≈ 3.00e-4 kg`. Mars fixture similar. This is a **routing trade-off**, not a physics regression: Phase B's 2-axis split lets the operator drive Stage 3 capture up by raising `stir_state.radial` (the gas-side Sherwood enhancement directly amplifies cold-wall mass-transport), or by tuning Stage 3 temperatures down to widen the cold-wall ΔP. The F1 stage-routing-purity ledger still reports the per-stage breakdown honestly; mass balance closure stays ≤5×10⁻¹² %. Default retune to force Stage 3 dominance was rejected per the project "no fudging" mandate — the +146% SiO yield is the physics-correct answer, and the routing limitation belongs in the recipe surface, not in the simulator defaults. Phase C milestone codex review (2026-05-28) P2.

### Fixed — milestone review (Phase C)

- **`simulator/runner.py::_build_per_hour_summary`** (P1): per-hour summary used to mix two gas-state sources — `P_total_bar` from the holdup-derived snapshot, `pO2_bar` from the live commanded `melt.pO2_mbar` setpoint. Under finite-headspace + HARD_VACUUM/PN2_SWEEP, a commanded setpoint could exist without the floor firing (atmosphere excluded), producing impossible `P_total < pO2` lines in the audit trail. Both fields now read from the same overhead-gas snapshot composition.
- **`simulator/session.py::SimSession.adjust("pO2_mbar", x)`** (P2): operator commanding positive pO₂ via `session.adjust("pO2_mbar")` now also switches `melt.atmosphere` to `CONTROLLED_O2` so the 1/√pO₂ Ellingham SiO suppression becomes live (mirrors the wall-sweep CLI Phase A P2 fix for the generic operator-API path). `pO2_mbar=0` leaves atmosphere alone (operator clearing the setpoint, NOT requesting controlled-O₂).

### Carried forward — deferred follow-ons (P3s, tracked in `docs-private/`)

- Phase A branch-test coverage gap for the floor split (`equilibrium.py` controlled vs HARD_VACUUM/PN2_SWEEP).
- Phase B P3 #1: mapping-axial defensive clamp in `engines/builtin/evaporation_flux.py`.
- Phase B P3 #2: extra `stir_state` dict-key audit (typo handling).
- Phase C P3: `MeltState(stir_factor=X)` constructor-shape compat broken (intentional API break, documented above).

## [0.5.2] — 2026-05-27

Physics-correctness sweep over 0.5.1. Phase A landed three controller-direct
commits (`ed7ef76`, `5e22bf8`, `e54d12d`) covering Chapman-Enskog `D_AB(T, P)`,
the Mn high-T linear refit on the Mn(l) basis, the VapoRock provider status
pass-through, and the `fit_target` convention docs. Phase B (this commit) is
the user-directed unlock: replace the v1 additive viscous-MT blend with the
canonical Bird/Stewart/Lightfoot series-resistance composition and lift the
Sherwood number off the laminar pipe asymptote via operator-controlled
induction stirring.

### Changed — viscous-regime mass-transfer canonical form (Phase B)

- **Series-resistance deposition flux**
  (`simulator/condensation.py::_series_resistance_deposition_flux_mol_m2_s`).
  Replaces the v1 additive blend `f·J_HKL + (1−f)·J_MT`. The new form is
  `1/k_total = 1/(α_s·k_HKL) + (1−f)/k_MT`, where `f = regime_factor =
  Kn/(Kn+0.01)`. The `(1−f)` factor weights the boundary-layer resistance
  OUT in free-molecular regime (no continuum boundary layer) so the
  series form degenerates cleanly to pure HKL when `f → 1`, and weighted
  IN in viscous regime (where gas-phase diffusion is rate-limiting). The
  pre-0.5.2 codex P0 #1 challenge had flagged the additive blend as
  wrong physics at C2A viscous regime — HKL's absolute magnitude
  dominated 95% of the blend even at the small regime-factor weight,
  because HKL is the *free-molecular impingement* limit (essentially
  unbounded by gas-phase transport). The series form rate-limits at the
  slower of the two physical mechanisms, which is the canonical
  Bird/Stewart/Lightfoot treatment of coupled resistances.
- **Induction-stirring-enhanced Sherwood number**
  (`simulator/condensation.py::_stirring_enhanced_sherwood`).
  `Sh_eff = Sh_laminar × √stir_factor`, a Frössling-style forced-convection
  correction that does not commit to a specific pipe-vs-tank correlation
  (the geometry is hybrid melt-on-pot + duct-to-baffles). With
  `stir_factor = 1` (no stirring) the laminar asymptote `Sh = 3.66`
  applies; with C2A default `stir_factor = 6` (`setpoints.yaml § C2A
  induction_stirring: continuous 4-8× acceleration`), `Sh_eff ≈ 9`;
  hard-clamped at `MAX_STIR_FACTOR = 10.0` by the "melt-flying-out-of-
  the-pot" upper bound (both the helper and `configure_operating_
  conditions` apply the clamp so a bad campaign override cannot
  silently inflate Sh — codex pre-0.5.2 Phase B P2). **Scope**: Phase
  B affects the **stage-vs-wall allocation** of the per-tick capture,
  not the absolute total capture (the latter is still governed by
  `_pressure_isolated_capture_budget_kg`, which is rate-cap-driven,
  not flux-derived). Net effect at C2A viscous regime: within the
  capture budget, more SiO lands at the designated Stage 3 condenser
  (+14.8% allocation shift per the regenerated
  `lunar_mare_low_ti_c2a.json` / `mars_basalt_c2a.json` fixtures) and
  less escapes downstream, because the series-resistance + stir-Sh
  flux balances better between stage capture and wall deposition than
  the v1 additive blend did. Replacing the rate-cap with a
  series-resistance-derived budget is queued for a follow-on release.
- **`melt.stir_factor` wired through to the condensation model**.
  `simulator/core.py::_configure_condensation_operating_conditions` now
  passes `stir_factor=float(getattr(self.melt, 'stir_factor', 1.0))`
  into `CondensationModel.configure_operating_conditions(...)`. The
  operating-history snapshot records the per-tick value so a downstream
  audit can correlate Sh enhancement with the recipe campaign override
  (`simulator/campaigns.py:152`).

### Changed — Phase A (already in this minor; recap from `ed7ef76`/`5e22bf8`/`e54d12d`)

- **Per-species Chapman-Enskog binary diffusion** replaces the legacy
  `1e-2 m²/s` constant in the boundary-layer flux. Adds the Neufeld
  1972 collision-integral correlation and a 14-species Lennard-Jones
  table (Na, K, Ca, Fe, Mg, Mn, Cr, Al, Ti, SiO + N₂/Ar/CO₂/O₂ carriers).
  At the SiO/N₂ typical operating point (10 mbar, ~1973 K bulk gas)
  the proper `D_AB ≈ 5 × 10⁻²` m²/s — about 5× the constant; at higher
  pressures the constant overshoots by ~30×.
- **Mn high-T linear Ellingham refit on the Mn(l) basis** for the
  1517-1700 K liquid-phase window. The pre-0.5.2 fit used a
  Mn(s)-derived linearisation and missed the 12.05 kJ/mol fusion
  enthalpy. Updated entry in both `simulator/equilibrium.py` and
  `engines/builtin/vapor_pressure.py`.
- **VapoRock provider status pass-through**
  (`engines/vaporock/provider.py:208-222`). Removes a silent whitelist
  coercion that mapped unrecognised backend statuses (`timeout`,
  `partial`, `no_data`, `failed`, empty) to `'ok'`. Raw status now
  passes through verbatim (lowercased + stripped); the core-level
  VapoRock-unavailable gate already refuses any non-`ok` status with
  empty pressures under `allow_fallback_vapor=False`.
- **Vapor-pressure `fit_target` convention documented**
  (`docs/model-limitations.md`). Two modes: `pure_component_psat`
  (Fe / Mg / Ca / Al / Ti / Mn / Cr) where Antoine reproduces pure-metal
  `P_sat` and the melt vapor partial is `a_M(l)·P_sat`; and
  `pseudo_psat_backsolved_from_vaporock` (Na / K / Cr / Mn) where the
  Antoine coefficient A is back-solved on a VapoRock calibration grid
  so that `a_M·P_sat_pseudo ≈ P_metal_VapoRock` at the calibration
  point. Both modes are single-counted by construction.

### Verified — non-issues

- **S1c stale reagent carryover after C3_NA → C4**. Codex S1c challenge
  flagged this as a state-contamination / accounting risk. Verified
  that `_unspent_additive_reagents_kg()` at `simulator/core.py:3687`
  already accounts for the carryover via `process.reagent_inventory`
  + `reservoir.reagent.<species>` lookups and reports under
  `unspent_<reagent>_reagent` in the product ledger. Codex correctly
  characterised this as "not mass-breaking; state-contamination /
  accounting risk" — but accounting is honest. No code change.

## [0.5.1] — 2026-05-27

Post-0.5.0 physics-correctness hardening. Closes five of the eight
deferred items the 0.5.0 release listed; two architectural items
(finite-headspace default-on flip + freeze-gate default-on flip) remain
deferred to dedicated sessions. Gate stable at **938 passed / 77 skipped
/ 6 xfailed / 0 failed**; mass-balance closure preserved at
**2.19 × 10⁻¹⁴ %**.

### Added

- **S1c — intra-C3 self-re-flux** (`simulator/extraction.py::_step_shuttle`).
  At the start of every C3 tick the previously-condensed alkali on the
  train is moved back into `process.reagent_inventory` via the existing
  `_transfer_condensed_species` helper. Implements the "same Na
  inventory amplifies across multiple batches before final recovery"
  pattern CLAUDE.md §4 describes — read across inject/bakeout
  sub-phases within a single C3 phase as well as across batches.
  C3_K path stays in place but is dead code under post-V1c-JANAF
  (S1b shuttle gate refuses K → FeO at any practical melt T).
  Closes Review D P1-3 (S1b documented the design; S1c lands it).
- **Viscous-regime mass-transfer model** (`simulator/condensation.py`).
  Sherwood-number boundary-layer flux companion to the HKL surface
  deposition: Bird/Stewart/Lightfoot `Sh = 3.66` (laminar pipe,
  constant wall concentration); `k_c = Sh × D_AB / D_pipe`; combined
  with HKL via the existing `regime_factor = Kn/(Kn+0.01)` split so
  that at deep viscous (Kn → 0) the mass-transfer term dominates and
  at free-molecular (Kn → ∞) the HKL term dominates. Closes tickler
  §5 follow-on; HKL alone is the free-molecular limit and was
  under-predicting viscous-regime stage capture by ~30× since F3.

### Changed — thermo data refresh

- **MnO source decision**: `_ELLINGHAM_THERMO['Mn']` updated from
  legacy `(-770.0, -0.165)` to NIST-JANAF standard-formation values at
  298 K `(-770.440, -0.149752)` (Mn-008 Chase 1998). Intercept
  essentially unchanged; slope corrected (10% drift in the legacy
  value). ΔG(1600 °C) shifts -461 → -490 kJ/mol O₂; Mn stays in the
  moderate-oxide tier with Fe/Cr. High-T linear refit deferred as
  V1c-Mn-followon (Mn passes through its solid → liquid transition
  at 1517 K mid-band).
- **V1e-followon — Na/K alpha reconciliation** documented. Sossi 2019
  (open-furnace mass-loss, α ≈ 1) vs Fedkin 2006 (KEMS sealed
  equilibrium chamber, α ≈ 0.13) — 5-8× methodological disagreement.
  The simulator pins Sossi (already in `data/vapor_pressures.yaml`);
  the rationale (mbar-sweep regime closer to open-furnace than KEMS)
  and the Fedkin alternative are now documented in the
  `competing_sources` block.

### Fixed

- **Autoreview r8 P1 — VapoRock runtime failures no longer silently
  reuse backend vapor pressures** (`simulator/core.py::_apply_kernel_vapor_pressures`).
  Previously when `VAPOR_PRESSURE` dispatch returned `status='unavailable'`
  (the import succeeded but the adapter call yielded no result), the
  empty kernel payload was treated identically to `status='ok'` with no
  evaporation expected — silently falling back to internal-analytical/AlphaMELTS
  pressures with no operator-visible signal. Now ANY non-'ok' kernel
  status with no pressures raises `RuntimeError` loud when
  `allow_fallback_vapor=False` (the production default) and surfaces
  an explicit diagnostic warning when fallback IS allowed. Closes
  autoreview round 8 + the autoreview pre-0.5.1 P1 follow-on
  (which surfaced that the original r8 fix missed
  `not_converged` and `out_of_domain` statuses) + the codex challenge
  pre-0.5.1 P1 (malformed status `None`/`""` was treated as `ok`;
  now coerced to `'unknown'` bucket and treated as failure mode).
- **`allow_fallback_vapor` boolean parsing — `bool("false") == True`
  silent opt-in** (`simulator/core.py:339`). The prior `bool(...)`
  coercion treated any non-empty string as truthy, so a setpoints
  override carrying the string `"false"` would silently opt the run
  into fallback mode. Now string-aware coercion treats
  `{'', 'false', '0', 'no', 'off', 'none'}` (case-insensitive) as
  False; everything else passes through `bool(...)`. Codex challenge
  pre-0.5.1 P2.
- **S1c C3_K branch now recycles Na as well as K** — the C3_K
  dispatch injects BOTH K (for the K → FeO path, refused post-V1c)
  AND Na (for the cool-window `feo_cleanup` path), so the recycle
  hook must transfer both alkalis. The original S1c only transferred
  K in C3_K, silently leaving condensed Na idle. Autoreview pre-0.5.1
  P2 + codex challenge confirm.
- **Viscous mass-transfer flux now uses bulk gas T in the ideal-gas
  denominator** (`simulator/condensation.py::_viscous_mass_transfer_flux_mol_m2_s`).
  The original implementation used `T_surface_K` for both the P_sat
  call (correct — saturation pressure is a function of cold-wall T)
  AND the `P/(R·T)` ideal-gas conversion (wrong — bulk gas
  concentration uses bulk gas T). In cold-wall scenarios this
  overstated the boundary-layer flux by `T_gas/T_wall`. Now the
  helper accepts a separate `T_gas_K` parameter and both call sites
  pass `self.gas_temperature_C + 273.15`. Autoreview pre-0.5.1 P2.
- **Defensive warning-append on `kernel_vapor_pressure_warnings`** —
  `setdefault('kernel_vapor_pressure_warnings', []).append(...)`
  would raise if the diagnostic dict already had the slot as
  `None`/str/dict (provider diagnostic is free-form per contract).
  Now coerces to list before append. Codex challenge pre-0.5.1 P2.
- **Bounded exception message on VapoRock raise** — the prior
  exception text dumped the full diagnostic dict, which can include
  the full vapor-pressure map and full speciation. Now logs only
  the diagnostic KEYS in the exception; full dict remains on
  `self._last_vapor_pressure_diagnostic` for callers that need it.
  Codex challenge pre-0.5.1 P2.

### Known limitations carried forward to post-0.5.1

- **Viscous-regime mass-transfer (v1 approximation)** — the
  Sherwood-number boundary-layer flux landed this release uses an
  additive regime_factor blend
  (`J_total = J_HKL × w + J_MT × (1−w)`) rather than the canonical
  series-resistance form (`1/k_total = 1/k_HKL + 1/k_MT`). At
  C2A_continuous viscous regime (Kn ≈ 3.7 × 10⁻⁴), this means the
  HKL term still dominates ~95% of the blended flux because HKL is
  hundreds of times larger than the MT term in absolute magnitude;
  the regime_factor weighting alone cannot bring HKL down enough.
  A proper series-resistance refit is queued for the next minor
  release: `1/k_total = 1/(α_s × k_HKL_eff) + 1/k_MT_eff`. The
  current implementation is directionally correct (some MT capture
  added in viscous regime where there was none) but the magnitude
  recovery toward pre-F3 levels is small (a few %, not the ~3×
  the tickler envisioned). Codex challenge pre-0.5.1 viscous-MT
  P1+P2+P3+P4+P5+P8.
- **D_AB fixed at 1.0 × 10⁻² m²/s default** — Chapman-Enskog
  cross-check shows the actual binary diffusion coefficient varies
  from ~3 × 10⁻³ to ~3 × 10⁻¹ m²/s across the simulator's
  operating envelope (10 mbar → 100 mbar, 1373 K → 1973 K).
  Species-keyed D_AB(P, T) is open work; the current constant is
  off by up to ~30× at extremes. Codex challenge pre-0.5.1 P4.
- **Ca-extraction-mode investigation scoping doc** filed
  (`docs-private/scoping-ca-extraction-mode-2026-05-27.md`,
  private). Documents the three plausible mechanisms (aluminothermic
  at >2200 K, vacuum thermal dissociation at deep vacuum, silicide
  byproduct chemistry) plus their freeze-gate/finite-headspace
  dependencies; investigation-only this round.

### Validated

- Post-0.5.0 full gate: 938 passed / 77 skipped / 6 xfailed / 0 failed
  in 2:17 (post-viscous-MT landing).
- 5 substantive code+data commits since 0.5.0 (`1d768a1`, `97b9718`,
  `a62bc8a`, `70c9b39`, plus this VERSION bump), each chunk-reviewed.

### Deferred from 0.5.0 → still deferred to dedicated sessions

- **Finite-headspace default-on flip** (#10). Attempted 2026-05-27;
  rolled back at the 14-test blast-radius point. Three interaction
  surfaces need careful triage: pO₂ lever semantics under HARD_VACUUM
  (intentionally excluded from `_O2_CONTROLLED_ATMOSPHERES` floor),
  freeze-gate test expectations, golden cluster. Detailed triage
  notes: `docs-private/finite-headspace-flip-attempt-2026-05-27.md`.
- **Freeze-gate default-on flip** (#9). Naturally follows
  finite-headspace landing; similar architectural blast radius.

## [0.5.0] — 2026-05-27

**Physics-truth release.** The simulator now anchors every authority surface
to either a literature-validated thermochemical engine or a documented
self-consistent fallback. Major physics-truth findings landed; recipes were
retuned to follow the physics (per "recipes follow physics, not the other
way around"). Mass-balance closure under the full default-on stack: **2.19 × 10⁻¹⁴ %**.

### Added — silicate-equilibrium kernel + freeze-gate

- **`simulator/chemistry/kernel`** — explicit per-intent authority surface:
  - `SILICATE_EQUILIBRIUM` (AlphaMELTS/ThermoEngine), `SILICATE_LIQUIDUS`
    + `SILICATE_SOLIDUS` (MAGEMin liquidus finder), `EQUILIBRIUM_CRYSTALLIZATION`
    (continuous `liquid_fraction(T)`), `VAPOR_PRESSURE` (builtin
    Antoine/Ellingham authoritative; VapoRock diagnostic shadow),
    `GATE_LIQUID_FRACTION` (freeze-gate consumer of EC path).
  - L0–L5 commits route legacy callers through the kernel diagnostically before
    flipping authority.
- **Freeze-gate (FG1–FG4)** — default-off `simulator/condensation` evaporation
  gate that suppresses sub-liquidus evaporation. Cache-key quantization
  (~100× speedup); MAGEMin non-monotone three-band tolerance; intrinsic fO₂
  threading + GATE_LIQUID_FRACTION intent; live ThermoEngine activity-key
  mapping. `data/setpoints.yaml §15 freeze_gate.enabled: false` (flip is
  post-0.5.0; see Deferred below).
- **`simulator/melt_backend.ThermoEngineTransport`** — first-class activity-corrected
  μ → a conversion (L4); supersedes the legacy MELTS pseudo-activity path
  where ThermoEngine is reachable. Default backends transparently negotiate
  the available transport with a documented fallback chain.

### Added — vapor-pressure physics

- **NIST-JANAF Ellingham refit (V1c-constants)** for 8 species
  (Na, K, Fe, Cr, Mg, Ca, Al, Ti) in `simulator/equilibrium.py::_ELLINGHAM_THERMO`
  and `engines/builtin/vapor_pressure.py`. Crossovers shifted:
  K/Fe **1216 → 832 °C** (−384 °C); Na/Fe **1331 → 1173 °C** (−158 °C);
  Mg/Al **~1573 °C** newly explicit. Citations inline; multi-source defensible
  (JANAF + NASA CEA + USGS Robie-Hemingway).
- **Per-species evaporation_alpha (V1e-impl)** — Ca/Ti proxy values; Al skip;
  Cr/Mn/CrO₂ fail-loud at engine layer. Operational override via
  `data/setpoints.yaml §16 chemistry_kernel.allow_unmeasured_alpha_fallback`
  (true by default; engine code remains strict).
- **Vapor-pressure convention annotation (V1b)** — explicit `fit_target:`
  schema field per species; Path A annotation per V1a determination
  (chain is single-counted, implicit back-solve).
- **`vapor_pressures_source` provenance dict (E3)** on
  `EquilibriumResult` — every species carries `vaporock | thermoengine |
  alphamelts_python_api | builtin_fallback` so downstream consumers can
  verify which authority computed the value.

### Added — shuttle physics

- **Shuttle T-acceptance gate (S1b)** in `simulator/shuttle.py` — refuses
  metallothermic reduction outside the species-pair crossover band.
  Reports `status="refused"` with a structured diagnostic (operator can see
  the reason). Per the new JANAF-derived crossovers, K → FeO is refused at
  any practical melt T (post-V1c) and Na → FeO is refused above 1173 °C.

### Added — condensation routing honesty (F1–F6 cluster)

- **Canonical species → stage registry (F1)** — `simulator/condensation_routing.py`
  declares the expected stage per species; `stage_purity_report` exposes
  drift to the runner output.
- **Per-pipe-segment wall temperatures (F2)** — `simulator/condensation.py`
  carries an explicit segment graph with per-segment T; the cold-spot ledger
  warns when an upstream segment is below the local condensation T (the
  failure mode that would otherwise show up only as fouling at the wrong stage).
- **Knudsen-regime enforcement (F3)** — `KnudsenRegime` enum +
  `KnudsenRegimeRefusal` exception when any pipe segment Kn ≥ 10 under a
  campaign that requires viscous flow. Band-integration HKL flux now
  consistently applies `regime_factor = Kn/(Kn + 0.01)` (closes a
  pre-existing code/docstring inconsistency). Operating-history entries
  carry the diagnostic; `run_metadata.knudsen_regime_diagnostic` exposes it
  to the operator.
- **By-species + by-class rump composition (F4)** — terminal payload now
  exposes the residual ceramic by species (Ca, REEs, refractory oxides,
  Al-not-thermited) and by class (alkali / iron / silica / refractory).
- **Route-conditional rump assertions (F5)** — recipe-level invariants that
  enforce the rump is a *physics floor*, not a recipe choice. Refractory
  rump emerges by physics whenever the recipe achieves full extraction of
  the lower-Ellingham species.

### Added — review-driven hardening (E1–E3, autoreview)

- **Writer-purity audit extension (E1)** — `tests/chemistry/test_writer_purity.py`
  now detects `atom_ledger.move()` and `atom_ledger.transfer()` paths,
  not just `apply()`. FactSAGE WRITER-EXEMPT path in `simulator/core.py`
  preserved (the L0 lesson).
- **Default-on freeze-gate closure pytest (E2)** — pins C2A_staged
  mass-balance closure under `freeze_gate.enabled: True`. Catches the
  regression class flagged by Review E P2.
- **Seven autoreview rounds landed before push** — r1 fix: pytest-xdist +
  pytest-timeout declared in `[dev]` extras; r2 fix: also in
  `requirements.txt` so a clean checkout doing `pip install -r requirements.txt`
  doesn't abort pytest argument parsing; r2 doc fix: `docs/recipe-playbook.md`
  migrated from K-shuttle (now refused) to C3_NA Na-only; r3 fix:
  `simulator/extraction.py` shuttle paths no longer silently swallow
  `status='refused'` from the kernel — refusals are recorded on
  `sim._last_shuttle_refusal_diagnostic` + `sim._shuttle_refusal_history`
  and surfaced verbatim in the runner's new top-level
  `shuttle_refusal_history` field (see Schema below); r4 fixes:
  per-segment wall-deposit candidates now use the per-segment vapor
  supply as the rate budget (was over-stating downstream candidates
  with the full-train ``rate_kg_hr``, diverting capture from
  designated stages into walls), AND ThermoEngine no longer fabricates
  a bulk-composition "liquid" for subsolidus assemblages (was
  defeating the freeze-gate diagnostic by making fully-crystallized
  states indistinguishable from real liquid states); r5 fixes:
  `requirements.txt` ships vaporock + petthermotools so the documented
  installer flow installs the full operational chain, AND the
  RunnerError failure envelope carries `shuttle_refusal_history: []`
  for schema parity with the happy-path output; r6 fix: C2A_STAGED →
  C3_NA campaign transition now honors the `staged_duration_h`
  override (3h cool cleanup) for `record.path == 'A_staged'` instead
  of falling into the default 35h C3_NA arm; r7 fixes: equal-temperature
  wall-routing fast path filters to reachable (upstream-of-designated-
  stage) pipe segments and caps by per-segment supply (was crediting
  unreachable downstream walls), AND the AlphaMELTS EC sample wrapper
  allows zero-liquid endpoints (`liquid_fraction == 0` with empty
  composition is the right signal for the solidus endpoint after the
  r4 ThermoEngine fix, not a sample error).

### Changed — dependencies

- **VapoRock and petthermotools are now `[project.dependencies]` (required),
  not optional extras.** The full model suite is the operational chain; the
  pure-Antoine × Ellingham fallback cannot reproduce VapoRock's γ_M
  corrections (verified at lunar mare 1500 °C IW: chain is 124 × off for K).
  FactSAGE / ChemApp remains the only optional engine (paid license).
- **MAGEMin** documented as a compiled-binary build path
  (`engines/magemin/bin/MAGEMin`) per the published manual-compilation
  recipe; no pure-PyPI package exists.

### Changed — recipes follow physics

- **C2A_staged**: `cool_for_k_shuttle` → `cool_for_na_shuttle` at 1150 °C.
  The K-shuttle is no longer a recipe step (S1b refuses K → FeO at any
  practical melt T post-V1c-JANAF). Operator knob `K_additive_kg` is
  accepted by the runner but ignored by the post-V1c shuttle gate; only
  `Na_additive_kg` is effective.
- **C3 default campaign**: C3_K → **C3_NA (Na-only)**. Path A and Path B
  sequences in `docs/recipe-playbook.md` updated.
- **Golden-fixture regeneration** for V1c-JANAF Ellingham shifts +
  F3 regime-factor band integration. C2A_continuous SiO stage-3 product
  drops ~30 × (3.4 × 10⁻⁴ → 1.0 × 10⁻⁴ kg) and wall_deposit @ 1050 °C
  rises ~6.75 × (2.2 × 10⁻⁶ → 1.5 × 10⁻⁵ kg) — total SiO budget
  conserved; routing redistributed by the physics-consistent fix.

### Changed — test infrastructure

- **pytest-xdist + pytest-timeout** declared in `[project.optional-dependencies.dev]`
  AND `requirements.txt`. `[tool.pytest.ini_options] addopts = "-n auto --timeout=300"`
  is the default for every `pytest` invocation. Speedup: ~10×, plus
  individual-test hangs are bounded.

### Documentation

- **Diataxis user docs** under `docs/` — getting-started, concepts,
  recipe-playbook, glossary, runner-output-schema, melt-backends,
  model-limitations, output-interpretation, process-model,
  session-script-protocol.
- **Two senses of Ellingham disambiguation** in `docs/concepts.md` +
  `docs/glossary.md` + `CLAUDE.md §4` cross-reference. Sense 1: oxygen-affinity
  ladder (metallothermic reduction). Sense 2: pressure-modified dissociation
  threshold under vacuum (with `−1/n_M` species-specific slopes; tabulated).

### Validated

- **936 tests pass / 77 skipped / 6 xfailed / 0 failed** in 1 min 35 sec
  (post-F3 authoritative gate; deselects the pre-existing slow alphaMELTS
  live EC test which takes ~3 min/param under live ThermoEngine and is
  re-introduced as a slow-marked test post-0.5.0).
- **Mass-balance closure**: 2.19 × 10⁻¹⁴ % under default-on `freeze_gate` +
  V1c-JANAF + V1e-impl + S1b + F1–F6 + E3 (Review E + autoreview r2 sweep
  confirmed no leaks).

### Deferred to post-0.5.0 (documented; not blocking)

- **S1c** — self-re-flux honest implementation (intra-C3 recycle); S1b
  documents the design.
- **V1e-followon** — Na/K α reconciliation (Sossi vs Fedkin disagreement
  flagged but unresolved; setpoints opt-in covers the operational case).
- **MnO source decision** — pick a defensible high-T basis (Mn legacy
  retained post-V1c-constants).
- **S1b-followon** — properly rewrite
  `test_na_shuttle_metals_are_reported_from_process_metal_phase` (currently
  xfailed with reason; the production code path is correct).
- **Freeze-gate DEFAULT-ON flip** — needs milestone review of blast
  radius across the recipe catalog.
- **Finite-headspace** (B1 + B2 phases) — overhead inventory tracking
  beyond the current open-system approximation.
- **Ca-extraction-mode investigation** — uses the freeze-gate / liquidus
  plumbing landing in this release.
- **Viscous-regime mass-transfer model** (post-F3 follow-on) — F3 finishes
  HKL-only attenuation honestly, but HKL is the free-molecular limit. Real
  refineries collect viscous-regime product via boundary-layer mass
  transfer; a Sherwood-number compensating term will recover the
  pre-F3 magnitudes via correct physics rather than HKL-overreach. See
  `docs-private/tickler-2026-05-18.md §5`.

## [0.1.0] — 2026-05-20

First formal tagged release. Marks completion of the **WEB-THIN-DRIVER consolidation**:
the web UI and the batch runner now drive one shared command core, and the simulator is
fully testable headlessly. Cross-surface scientific parity verified **exact**.

### Added
- `simulator/session.py::SimSession` — synchronous command core (verbs:
  `start`/`advance`/`decide`/`adjust`/`pause`/`resume`/`snapshot`) with `StepResult` +
  `DecisionPolicy`; web and batch runner both drive it.
- Headless CLI `python -m simulator`: `run` (one-shot batch → JSON result document) and
  `session --script <file|->` (newline-delimited JSON, one frame per command) — the
  browser-free operator/test surface. Protocol pinned in `docs/session-script-protocol.md` (v1.0.0).
- `simulator/backends.py` — unified `resolve_backend` + `BackendSelectionPolicy`
  (`WEB_AUTODETECT` | `RUNNER_STRICT`, no default; runner-strict rejects `auto`).
- Deterministic web socket-trace golden harness (`tests/test_web_socket_trace.py`).

### Changed
- `web/events.py` is now a thin Socket.IO adapter over `SimSession` (socket trace
  byte-identical vs the pre-refactor stream).
- `simulator/runner.py` reimplemented on `SimSession`; removed the dead
  `iter_hours()` / `simulator=` reuse seam.
- Lunar-operator nav link hidden (the operator game is a stub; route + code intact).

### Fixed
- Web `backend='stub'` now deterministically selects `InternalAnalyticalBackend` (previously routed
  through autodetect and returned AlphaMELTS when installed).

### Validated
- 809 tests pass (+96 skipped; the 1 failing `test_artifact_guards` case is an
  environmental `rg`-not-on-PATH artifact, not a code defect).
- **Cross-surface scientific parity EXACT**: batch = CLI = web ledgers agree to `0.0 mol`
  over a full `lunar_mare_low_ti` pyrolysis run; max mass-balance error ≤ `9.6e-13 %`.

### Baseline capabilities (already on `main` before 0.1.0)
- Mol-native atom ledger; `commit_batch` is the sole transition writer (with documented
  seeding/exempt exceptions); per-intent engine authority.
- Builtin Antoine/Ellingham authoritative for vapor pressure; VapoRock diagnostic
  shadow triply benchmarked (Wolf-2022 adapter 0.008 dex, literature 0.08 dex,
  MAGEMin shadow no-divergence); MAGEMin shadow engine wired;
  AlphaMELTS diagnostic-only.
- Per-species `wall_deposit_kg` ledger + fouling-rate verdict; band-aware
  Hertz-Knudsen-Langmuir condensation law.

# Changelog

Notable changes to the regolith-pyrolysis-simulator. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); the project is research-stage (pre-1.0),
so minor versions may carry significant changes.

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
  evaporation expected — silently falling back to stub/AlphaMELTS
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
    (continuous `liquid_fraction(T)`), `VAPOR_PRESSURE` (VapoRock authoritative),
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
- Web `backend='stub'` now deterministically selects `StubBackend` (previously routed
  through autodetect and returned AlphaMELTS when installed).

### Validated
- 809 tests pass (+96 skipped; the 1 failing `test_artifact_guards` case is an
  environmental `rg`-not-on-PATH artifact, not a code defect).
- **Cross-surface scientific parity EXACT**: batch = CLI = web ledgers agree to `0.0 mol`
  over a full `lunar_mare_low_ti` pyrolysis run; max mass-balance error ≤ `9.6e-13 %`.

### Baseline capabilities (already on `main` before 0.1.0)
- Mol-native atom ledger; `commit_batch` is the sole transition writer (with documented
  seeding/exempt exceptions); per-intent engine authority.
- VapoRock authoritative for vapor pressure — triply validated (Wolf-2022 adapter 0.008
  dex, literature 0.08 dex, MAGEMin shadow no-divergence); MAGEMin shadow engine wired;
  AlphaMELTS diagnostic-only.
- Per-species `wall_deposit_kg` ledger + fouling-rate verdict; band-aware
  Hertz-Knudsen-Langmuir condensation law.

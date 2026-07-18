# Developer Map

This guide is for contributors and coding agents that need to find the right file quickly.

## Entry Points

- `regolith-pyrolysis-run.py` starts the local Flask and Socket.IO server.
- `app.py` creates the Flask application and Socket.IO wiring.
- `web/templates/simulator.html` defines the main simulator page.
- `web/static/js/simulator-socket.js` creates the Socket.IO client.
- `web/static/js/simulator-controls.js` handles user controls and feedstock/additive requests.
- `web/static/js/simulator-ticks.js` updates live charts and status panels.

## Simulator Engine

- `simulator/core.py` owns `PyrolysisSimulator`, batch lifecycle, campaign transitions, decisions, and snapshots.
- `simulator/state.py` owns constants, enums, and dataclasses.
- `simulator/decision_tree.py` routes operator decisions (Path A/B, Branch one/two, C6 yes/no, etc.).
- `simulator/equilibrium.py` owns fallback thermodynamic equilibrium and vapor-pressure estimates.
- `simulator/evaporation.py` owns Hertz-Knudsen evaporation, condensation routing, and melt composition updates.
- `simulator/extraction.py` owns MRE, alkali shuttle, and Mg thermite helper methods.
- `simulator/campaigns.py` owns campaign setpoints, ramp targets, endpoint checks, and decision prompts.

## Subsystems

- `simulator/overhead.py` models gas pressure, pipe capacity, turbine load, and background atmosphere effects.
- `simulator/condensation.py` routes evaporated species through the staged train.
- `simulator/electrolysis.py` models simplified MRE reduction.
- `simulator/energy.py` tracks electrical energy.
- `simulator/equipment.py` sizes representative refinery equipment.
- `simulator/mass_balance.py` checks input and output accounting.
- `simulator/persistence.py` handles YAML persistence.

## Accounting

- `simulator/accounting/__init__.py` re-exports the public ledger API.
- `simulator/accounting/ledger.py` owns `AtomLedger`, the canonical mol-native store; per-transition `validate_conservation()`; `assert_balanced()`.
- `simulator/accounting/formulas.py` owns the species formula registry and oxide/molecule atom counts.
- `simulator/accounting/lots.py` provides lot-tracking helpers.
- `simulator/accounting/exceptions.py` defines `UnbalancedTransitionError` and related errors.

## Backends

- `simulator/melt_backend/base.py` defines `MeltBackend` ABC, `EquilibriumResult` DTO, and `InternalAnalyticalBackend` fallback (the `internal-analytical` model; legacy backend name `stub`).
- `simulator/backend_names.py` defines canonical analytical-backend naming (`stub` and `diagnostic_stub` are accepted input aliases that fold onto the `internal-analytical` serialization token).
- `simulator/melt_backend/alphamelts.py` wires PetThermoTools and subprocess paths.
- `simulator/melt_backend/magemin.py` is the MAGEMin today-hook adapter (`MeltBackend` subclass), a shadow silicate solver alongside alphaMELTS.
- `simulator/melt_backend/vaporock.py` is the VapoRock today-hook adapter for equilibrium vapor speciation over silicate melts.
- `simulator/melt_backend/installer.py` installs engine binaries and dependencies.
- FactSAGE/ChemApp adapter files were removed/archived; `backend=factsage` is not a supported active backend.

## Engine Source Trees

- `engines/__init__.py` documents the chemistry-engine refactor: kernel-shadow provider source lives here; today-hook adapters stay in `simulator/melt_backend/`.
- `engines/builtin/` is the live authoritative chemistry plane — fifteen kernel-registered `ChemistryProvider` classes: fourteen rows in `PyrolysisSimulator._BUILTIN_PROVIDER_REGISTRATIONS` plus the separately wired `BuiltinVaporPressureProvider`. Every `LedgerTransitionProposal` they emit is routed through `ChemistryKernel.commit_batch` (the sole writer to `AtomLedger`); no `simulator/*.py` module mutates the ledger directly anymore.
  - `engines/builtin/_common.py` provides shared helpers: `reject_wrong_intent`, `unpack_controls`, and `composition_wt_pct_from_account_view` (fail-closed via `UnknownSpeciesError`, mirroring `_load_ledger_account`).
  - `engines/builtin/README.md` documents the migration plan and per-provider conventions.

  Provider to intent mapping (registered by `PyrolysisSimulator._build_chemistry_kernel` in `simulator/core.py`):

  | Provider module | Class | `ChemistryIntent` | Authority | Declared accounts |
  |-----------------|-------|-------------------|-----------|-------------------|
  | `engines/builtin/vapor_pressure.py` | `BuiltinVaporPressureProvider` | `VAPOR_PRESSURE` | authoritative, diagnostic (no transition) | `process.cleaned_melt` |
  | `engines/builtin/evaporation_flux.py` | `BuiltinEvaporationFluxProvider` | `EVAPORATION_FLUX` | authoritative, diagnostic (no transition) | `process.cleaned_melt` |
  | `engines/builtin/evaporation_transition.py` | `BuiltinEvaporationTransitionProvider` | `EVAPORATION_TRANSITION` | authoritative, ledger-mutating | `process.cleaned_melt`, `process.overhead_gas`, `process.condensation_train` |
  | `engines/builtin/condensation_route.py` | `BuiltinCondensationRouteProvider` | `CONDENSATION_ROUTE` | authoritative, ledger-mutating | `process.overhead_gas`, `process.condensation_train` |
  | `engines/builtin/electrolysis_step.py` | `BuiltinElectrolysisStepProvider` | `ELECTROLYSIS_STEP` | authoritative, ledger-mutating | `process.cleaned_melt`, `process.metal_phase`, `terminal.oxygen_mre_anode_stored` |
  | `engines/builtin/metallothermic_step.py` | `BuiltinMetallothermicStepProvider` | `METALLOTHERMIC_STEP` | authoritative, ledger-mutating | `process.cleaned_melt`, `process.metal_phase`, `process.reagent_inventory` |
  | `engines/builtin/ca_aluminothermic_step.py` | `BuiltinCaAluminothermicStepProvider` | `CA_ALUMINOTHERMIC_STEP` | authoritative, ledger-mutating | cleaned melt, metal/C7-Al product, overhead/condensation/wall deposits, terminal slag |
  | `engines/builtin/native_fe_saturation.py` | `BuiltinNativeFeSaturationProvider` | `NATIVE_FE_SATURATION` | authoritative, ledger-mutating | `process.cleaned_melt`, `terminal.drain_tap_material`, `process.overhead_gas` |
  | `engines/builtin/fe_redox_respeciation.py` | `BuiltinFeRedoxRespeciationProvider` | `FE_REDOX_RESPECIATION` | authoritative, ledger-mutating | `process.cleaned_melt`, `process.overhead_gas`, `reservoir.fo2_buffer` |
  | `engines/builtin/stage0_pretreatment.py` | `BuiltinStage0PretreatmentProvider` | `STAGE0_PRETREATMENT` | authoritative, ledger-mutating | nine Stage 0 feed/sink accounts (`process.stage0_*`, `reservoir.stage0_*`, `terminal.offgas`, `terminal.stage0_salt_phase`, `terminal.oxygen_stage0_stored`) |
  | `engines/builtin/overhead_gas_equilibrium.py` | `BuiltinOverheadGasEquilibriumProvider` | `OVERHEAD_GAS_EQUILIBRIUM` | authoritative, diagnostic (no transition) | `process.overhead_gas` |
  | `engines/builtin/overhead_bleed.py` | `BuiltinOverheadBleedProvider` | `OVERHEAD_BLEED` | authoritative, ledger-mutating | `process.overhead_gas` plus terminal offgas/O₂ accounts |
  | `engines/builtin/oxygen_bubbler.py` | `BuiltinOxygenBubblerProvider` | `OXYGEN_BUBBLER` | authoritative, ledger-mutating | `process.overhead_gas`, `reservoir.fo2_buffer` |
  | `engines/builtin/oxygen_reservoir_exchange.py` | `BuiltinOxygenReservoirExchangeProvider` | `OXYGEN_RESERVOIR_EXCHANGE` | authoritative, ledger-mutating | `process.overhead_gas`, `reservoir.fo2_buffer` |
  | `engines/builtin/backend_equilibrium.py` | `BuiltinBackendEquilibriumProvider` | `BACKEND_EQUILIBRIUM` | authoritative, validates/forwards backend transitions | `process.cleaned_melt`, `process.metal_phase`, `process.overhead_gas`, `reservoir.fo2_buffer` |

- `engines/magemin/__init__.py` re-exports the MAGEMin shadow provider surface (`MAGEMinShadowProvider`, `MAGEMinDomainGate`, `MAGEMinParityComparator`, `ParityReport`).
- `engines/magemin/provider.py` is the registered `MAGEMinShadowProvider` kernel shadow provider. Its capability profile declares `SILICATE_LIQUIDUS`, `SILICATE_EQUILIBRIUM`, and `GATE_LIQUID_FRACTION`; only `GATE_LIQUID_FRACTION` is fallback-authoritative, and `dispatch()` returns `IntentResult` diagnostics, including `unsupported` for wrong intents.
- `engines/magemin/domain.py` is the `MAGEMinDomainGate` composition-range gate (14-oxide MELTS basis).
- `engines/magemin/parity.py` is the `MAGEMinParityComparator` shadow-vs-authoritative comparator (±50 K liquidus, ±2 wt% modal).
- `engines/magemin/README.md` documents the deliberate two-path split: `simulator/melt_backend/magemin.py` remains the backend adapter, while `engines/magemin/` is the registered kernel-shadow provider package that delegates to it.

## Chemistry Kernel

- `simulator/chemistry/kernel/__init__.py` is the public kernel surface: `ChemistryKernel`, `ChemistryProvider`, `ProviderRegistry`, intents, DTOs, and the kernel error hierarchy.
- `simulator/chemistry/kernel/planner.py` owns `ChemistryKernel.commit_batch` — the sole authorized writer to `AtomLedger`. Every provider-emitted `LedgerTransitionProposal` passes through three validation gates here: `validate_intent_authority`, `validate_proposal_accounts`, `validate_atom_balance`.
- `simulator/chemistry/kernel/validation.py` implements those gates plus `validate_control_audit`.
- `simulator/chemistry/kernel/registry.py` is `ProviderRegistry` (with `register_idempotent`); routes intents to authoritative + shadow providers.
- `simulator/chemistry/kernel/account_filters.py` builds a scoped `ProviderAccountView` from the live `AtomLedger` before any provider sees it.
- `simulator/chemistry/kernel/capabilities.py` defines `ChemistryIntent` and `CapabilityProfile`.
- `simulator/chemistry/kernel/dto.py` defines the request/result DTOs (`IntentRequest`, `IntentResult`, `LedgerTransitionProposal`, `ControlAudit`, `ProviderAccountView`).
- `simulator/chemistry/kernel/provider.py` defines the `ChemistryProvider` ABC.
- `simulator/chemistry/kernel/errors.py` defines `KernelError` and the per-gate error subclasses.

## Data

- `data/feedstocks.yaml` is the main feedstock library.
- `data/setpoints.yaml` contains process setpoints and campaign metadata.
- `data/vapor_pressures.yaml` contains vapor-pressure data.
- `data/custom_compositions.yaml` is a local extension point.

## Testing

- `tests/test_mass_balance.py` checks input/output mass accounting and process-inventory totals.
- `tests/test_molar_accounting.py` enforces the mol-native accounting contract across modules.
- `tests/test_extraction_ledger.py` verifies MRE/alkali/thermite extractions update the ledger correctly.
- `tests/test_overhead_accounting.py` exercises gas-train mass and lot bookkeeping under load.
- `tests/test_reagent_reservoirs.py` checks reagent reservoir lots and ledger contracts.
- `tests/test_stage0_atmosphere.py` covers Stage 0 hard-vacuum and Mars-backpressure feedstocks.
- `tests/test_feedstock_inventory.py` validates feedstock inventory loading and balance enforcement.
- `tests/test_alphamelts_backend.py` exercises the alphaMELTS backend wiring and subprocess paths.
- `tests/test_magemin_backend.py` defends the MAGEMin today-hook adapter (`MeltBackend`) contract.
- `tests/test_magemin_shadow_provider.py` covers the `engines/magemin/` kernel-shadow provider surface (domain gate, parity comparator, capability profile, diagnostic shape, and dispatch-level writer purity).
- `tests/test_vaporock_backend.py` exercises the VapoRock vapor-speciation backend adapter.
- `tests/test_backend_kg_adapters.py` checks backend kg adapters against the mol-native contract.
- `tests/test_debug_feedstocks.py` checks debug feedstocks stay hidden by default behind the debug env flags.
- `tests/test_public_payload_contract.py` pins the public Socket.IO payload contract.
- `tests/test_web_events.py` covers web event handlers, launcher defaults, and simulation restarts.
- `tests/test_artifact_guards.py` keeps generated/local engine artifacts and licenses out of git.

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

- `simulator/melt_backend/base.py` defines `MeltBackend` ABC, `EquilibriumResult` DTO, and `StubBackend` fallback.
- `simulator/melt_backend/alphamelts.py` wires PetThermoTools and subprocess paths.
- `simulator/melt_backend/factsage.py` wraps ChemApp via lazy import; requires license and .cst.
- `simulator/melt_backend/magemin.py` is the MAGEMin today-hook adapter (`MeltBackend` subclass), a shadow silicate solver alongside alphaMELTS.
- `simulator/melt_backend/vaporock.py` is the VapoRock today-hook adapter for equilibrium vapor speciation over silicate melts.
- `simulator/melt_backend/installer.py` installs engine binaries and dependencies.
- `simulator/melt_backend/factsage_config.py` loads FactSAGE configuration.
- `simulator/melt_backend/factsage_doctor.py` runs FactSAGE diagnostics.

## Engine Source Trees

- `engines/__init__.py` documents the chemistry-engine refactor: kernel-shadow provider source lives here; today-hook adapters stay in `simulator/melt_backend/`.
- `engines/magemin/__init__.py` re-exports the MAGEMin shadow scaffold (`MAGEMinShadowProvider`, `MAGEMinDomainGate`, `MAGEMinParityComparator`, `ParityReport`).
- `engines/magemin/provider.py` is the forward-declared `MAGEMinShadowProvider` scaffold; not yet wired and `dispatch()` raises `NotImplementedError` pending the kernel carve-out.
- `engines/magemin/domain.py` is the `MAGEMinDomainGate` composition-range gate (14-oxide MELTS basis).
- `engines/magemin/parity.py` is the `MAGEMinParityComparator` shadow-vs-authoritative comparator (±50 K liquidus, ±2 wt% modal).
- `engines/magemin/README.md` documents the deliberate two-path split: the `simulator/melt_backend/magemin.py` today-hook adapter is the live call site, while `engines/magemin/` is the kernel-shadow provider scaffold that delegates to it.

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
- `tests/test_factsage_backend.py` exercises the FactSAGE backend with mocked ChemApp.
- `tests/test_alphamelts_backend.py` exercises the alphaMELTS backend wiring and subprocess paths.
- `tests/test_magemin_backend.py` defends the MAGEMin today-hook adapter (`MeltBackend`) contract.
- `tests/test_magemin_shadow_provider.py` covers the `engines/magemin/` kernel-shadow scaffold (domain gate, parity comparator, `dispatch()` raising).
- `tests/test_vaporock_backend.py` exercises the VapoRock vapor-speciation backend adapter.
- `tests/test_backend_kg_adapters.py` checks backend kg adapters against the mol-native contract.
- `tests/test_debug_feedstocks.py` checks debug feedstocks stay hidden by default behind the debug env flags.
- `tests/test_public_payload_contract.py` pins the public Socket.IO payload contract.
- `tests/test_web_events.py` covers web event handlers, launcher defaults, and simulation restarts.
- `tests/test_artifact_guards.py` ensures local FactSAGE exports and licenses stay gitignored.

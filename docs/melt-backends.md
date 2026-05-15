# Melt Chemistry Backends

The simulator can run without external thermodynamic software. The fallback path combines simplified Ellingham equilibrium logic with Antoine vapor-pressure data, which is useful for comparative exploration but not for validated melt chemistry.

## Per-call result status

`EquilibriumResult.status` records the per-call backend outcome: `'ok'` (engine ran and produced a usable result), `'not_converged'` (engine ran but did not produce one), `'out_of_domain'` (a DomainGate or account filter rejected the input), or `'unavailable'` (engine / library / binary absent for this call). It is descriptive only — `core.py::_get_equilibrium` continues to drive fallback decisions from `is_available()` and the raised-exception handlers, and surfaces the most recent value on `_last_backend_status` for diagnostics.

## Backend Order

`web/events.py::_get_backend` is the single source of truth for active-backend selection. The active-backend eligibility policy
(see `\goal BACKEND-DEFAULT-SWITCH`, 2026-05-14) is:

1. **AlphaMELTS** is probed first. If `is_available()` (PetThermoTools or the project-local `alphamelts` binary at `engines/alphamelts/run_alphamelts.command`, or `alphamelts` on `PATH`) — selected as the active backend.
2. **FactSAGE / ChemApp** is probed second under the strict-config gate: `FACTSAGE_CONFIG` must point at a JSON that loads, declares a ChemApp module + `.cst` datafile, and `FactSAGEBackend.initialize()` must return True. Without a strict config FactSAGE stays diagnostic-only and selection drops to the `StubBackend` fallback. The user's explicit `factsage` choice is never silently replaced with a different primary.
3. **`StubBackend`** is the always-available fallback (built-in Ellingham/Antoine path inside `simulator/core.py`).

**`VapoRockBackend` and `MAGEMinBackend` are explicitly refused as the active `MeltBackend`.** Their honest call sites are now per-intent kernel `ChemistryProvider` registrations (VapoRock authoritative for `VAPOR_PRESSURE` under `\goal VAPOROCK-AUTHORITY-PROMOTION`; MAGEMin shadow for `SILICATE_LIQUIDUS` / `SILICATE_EQUILIBRIUM` under `\goal MAGEMIN-SHADOW-PARITY`), not the active-backend `_get_equilibrium` path. Selecting either as the active `MeltBackend` would still fail closed inside `simulator/core.py::_get_equilibrium` because their populated `phase_masses_kg` (MAGEMin) or vapor-only (VapoRock) returns leave `EquilibriumResult.ledger_transition=None`, which trips the "backend returned post-equilibrium phase material without an AtomLedger transition" reject. `_get_backend('vaporock')` and `_get_backend('magemin')` raise `BackendUnavailableError`. The kernel itself was carved out in `\goal CHEMISTRY-KERNEL-CARVE-OUT` and is the canonical home for VapoRock's `VAPOR_PRESSURE` ownership; see the "VapoRock authority promotion (goal #10)" section below.

There is **no silent cross-backend fallback at runtime**. If the selected primary throws inside `_get_equilibrium` after selection, the existing fail-closed path in `simulator/core.py` handles it; `_get_backend` does not re-probe a different primary mid-run.

On every selection `_get_backend` emits one log line of the form
`engine selection: <BackendClassName> (capabilities: silicate_melt=..., gas_volatiles=...) -- VapoRock/MAGEMin not eligible until kernel`
so the active-backend choice is visible in the launcher's normal stdout stream. The log line's wording predates the goal-#9 / goal-#10 kernel-provider promotions; the message refers to active-`MeltBackend` eligibility (still gated), not to kernel `ChemistryProvider` registration (which VapoRock and MAGEMin now have via `engines/vaporock` and `engines/magemin`).

The VapoRock wrapper still checks whether the canonical `vaporock` Python package is importable (with legacy `VapoRock` import fallback for older local installs); this controls whether the kernel-registered `VapoRockProvider` reports available at dispatch time -- when it does not, `\goal VAPOROCK-AUTHORITY-PROMOTION` requires the kernel to either raise `ProviderUnavailableError` (default) or delegate to the registered builtin Antoine fallback (opt-in via `setpoints['chemistry_kernel']['allow_fallback_vapor'] = True`).

## Local alphaMELTS Path

For local binary use, put the executable at:

```text
engines/alphamelts/run_alphamelts.command
```

The `engines/` directory is ignored by git so local licensed or platform-specific binaries are not published.

## Python Packages

Install optional Python melt tooling with:

```bash
pip install -e ".[melts]"
```

The `melts` extra currently includes `PetThermoTools`. The `vapor` extra installs the upstream VapoRock source tag because no PyPI release is available.

## VapoRock adapter notes

`simulator/melt_backend/vaporock.py` imports VapoRock lazily inside `initialize()`. It tries canonical lowercase `vaporock` first, then legacy uppercase `VapoRock`; import failure marks the backend unavailable and returns warnings instead of crashing the simulator.

The adapter receives the cleaned silicate melt only. It projects mol-native simulator oxide inventory into VapoRock oxide wt% over the simulator `OXIDE_SPECIES` basis; metal, sulfide, salt, and halide accounts are not passed to VapoRock.

The documented upstream path is `vaporock.System().set_melt_comp(...)` followed by `eval_gas_abundances(T, logfO2)`. The adapter also probes legacy helper names used by older forks. System log10(bar) output is converted to Pa.

`EquilibriumResult.vapor_pressures_Pa` is the primary output. VapoRock does not mutate `AtomLedger` directly; it owns the `VAPOR_PRESSURE` intent at the kernel level after `\goal VAPOROCK-AUTHORITY-PROMOTION` (#10), but the intent itself is read-only -- the downstream `EVAPORATION_TRANSITION` provider consumes the vapor-pressure dict and produces the ledger transition.

For legacy helper outputs, pressure values with max `< 1e3` are treated as bar and scaled to Pa; larger values are treated as already-Pa. `capabilities()` keeps `vapor_melt_equilibrium=True` as a VapoRock instance-level extension, leaving `DEFAULT_BACKEND_CAPABILITIES` at the canonical five shared keys.

See `docs-private/chemistry-engine-binding-spec-2026-05-14.md` §4 for the VapoRock input/output contract.

### VapoRock authority promotion (goal #10)

Under `\goal VAPOROCK-AUTHORITY-PROMOTION` (2026-05-15), `engines/vaporock/provider.py::VapoRockProvider` is registered as the **authoritative** `ChemistryProvider` for the `VAPOR_PRESSURE` intent in `simulator/core.py::_build_chemistry_kernel`. The original `engines/builtin/vapor_pressure.py::BuiltinVaporPressureProvider` is demoted to the registry's **fallback** slot (a new slot added to `simulator/chemistry/kernel/registry.py` for this goal).

The fallback only runs when both of these hold:

1. The authoritative `VapoRockProvider` raised `ProviderUnavailableError` at dispatch time (the upstream `vaporock` library is missing on the host), and
2. The simulator was constructed with `setpoints['chemistry_kernel']['allow_fallback_vapor'] = True`. The flag is read at `PyrolysisSimulator.__init__` and threaded into `ChemistryKernel.allow_fallback_intents`.

Otherwise the kernel re-raises `ProviderUnavailableError` -- **silent fallback is forbidden**.

When the fallback path fires, the kernel tags the result's `diagnostic` map with `kernel_fallback_used = 'builtin-vapor-pressure'` so trace consumers can tell the authoritative slot did not answer.

The authority swap is visible on a single read of `sim._chem_registry.capability_summary()`:

```python
>>> sim._chem_registry.capability_summary()['vapor_pressure']
{'authoritative': 'vaporock', 'fallback': 'builtin-vapor-pressure', 'shadows': ()}
```

The six other kernel-authoritative builtins (`EVAPORATION_FLUX`, `EVAPORATION_TRANSITION`, `CONDENSATION_ROUTE`, `ELECTROLYSIS_STEP`, `METALLOTHERMIC_STEP`, `STAGE0_PRETREATMENT`) are unchanged by the swap; their `authoritative` slot still names the `builtin-<intent>` provider.

The `VapoRockProvider` filters its output to the species universe `data/vapor_pressures.yaml` declares (the intersection of the YAML's `metals` section with `engines.builtin.vapor_pressure._ELLINGHAM_THERMO` keys, plus the entire `oxide_vapors` section). VapoRock's broader ~30-species output (`O2`, `Si2`, `Al2O2`, ...) is a richer chemistry surface than the downstream `EVAPORATION_FLUX` step is wired for; pinning the filter to the builtin's effective species set keeps the mass balance hard constraint (0.000%) intact across the swap. Future work can widen this set as the downstream stoichiometry validators learn each new species.

Test coverage: `tests/chemistry/test_vaporock_authority_promotion.py` binds the five acceptance scenarios (available + no flag, unavailable + no flag, unavailable + flag, available + flag, capability_summary truth). `tests/chemistry/test_kernel_registry.py` covers the registry's new fallback semantics (mutual exclusivity with shadow, authority-capable requirement, idempotent re-registration).

## AlphaMELTS Adapter Notes

- Python path imports `petthermotools` and preloads `meltsdynamic.MELTSdynamic` during initialization.
- Inputs are gated to `process.cleaned_melt` silicate oxides and normalized to the 14-oxide MELTS basis.
- Gas, metal, salt, sulfide, halide, and low-major-oxide material is rejected before the engine.
- `FeO_total` requires `QFM`, `NNO`, `IW`, `HM`, or configured `Fe3Fet`; no silent split.
- `fO2_offset` is buffer-relative, and parsed results fill diagnostics only; AlphaMELTS emits no ledger transition.

## MAGEMin adapter notes

`simulator/melt_backend/magemin.py` wraps MAGEMin, an open-source Gibbs free-energy minimiser for silicate phase equilibria. The adapter probes for MAGEMin lazily inside `initialize()`; a missing binary marks the backend unavailable and returns warnings instead of crashing the simulator.

**Bridge choice.** MAGEMin has no pure-PyPI package — the upstream clone ships zero Python files, no `setup.py`, no `pyproject.toml`. Its primary interface is Julia (`MAGEMin_C.jl`); from Python it is reached either through that Julia bridge or by driving the compiled `MAGEMin` binary over a subprocess. The adapter's supported default is the **subprocess bridge**: `initialize()` locates the compiled binary (a sibling clone at `../MAGEMin/MAGEMin`, or `engines/magemin/{,bin/}MAGEMin`, or `MAGEMin` on `PATH`) and `_call_magemin` invokes it with `--Verb=0` single-point arguments, parsing the compact `Phase :` / `Mode :` stdout block. The optional `pymagemin` and `julia` bridges are still probed first when a caller has them installed; the `ctypes` bridge is opt-in only (`python_bridge="ctypes"`) because its struct marshaling is unimplemented and auto-preferring it would shadow the working subprocess path. See `pyproject.toml` `[magemin]` for the build path.

**Oxide basis.** MAGEMin and alphaMELTS share the 14-oxide MELTS basis (`simulator.state.OXIDE_SPECIES`: SiO2, TiO2, Al2O3, FeO, Fe2O3, MgO, CaO, Na2O, K2O, Cr2O3, MnO, P2O5, NiO, CoO), so shadow comparisons are a straight rename. The adapter projects the mol-native simulator melt inventory into oxide wt% over that basis. For the binary's igneous (`ig`) database the wt% vector is folded onto MAGEMin's `ig` bulk order (`SiO2, Al2O3, CaO, MgO, FeOt, K2O, Na2O, TiO2, O, Cr2O3, H2O`): FeO and Fe2O3 combine into the single FeOt total-iron component, the free `O` redox component is zeroed (fO2 is set by the `--buffer` argument instead), and the oxides outside the `ig` system (MnO, P2O5, NiO, CoO) are dropped. Metal, sulfide, salt, and halide accounts are never passed to MAGEMin — when called with the layered ABC's `composition_mol_by_account`, only `process.cleaned_melt` is consumed and every other account is reported as a dropped-account warning.

**Pressure unit.** The binding-spec contract is pressure in **GPa, not bar** (binding spec §4). The adapter converts `pressure_bar` to `P_GPa` with 1 GPa = 10000 bar, then `P_GPa` to kilobar with 1 GPa = 10 kbar at the binary boundary (the MAGEMin CLI's `--Pres` argument takes kilobar). Both conversion legs are named (`_pressure_bar_to_GPa`, `_GPa_to_kbar`) so the unit chain is auditable — a wrong factor here is a silent order-of-magnitude pressure error.

**Shadow / diagnostic posture.** MAGEMin is shadow-only for `SILICATE_LIQUIDUS` and `SILICATE_EQUILIBRIUM` (binding spec §3 authority matrix); it runs alongside the authoritative alphaMELTS path, never instead of it. `ledger_account_policies()` returns no ledger-authoritative policy and `equilibrate()` never populates `EquilibriumResult.ledger_transition` — MAGEMin holds no `AtomLedger` authority and must not be granted any. "Diagnostic" does not mean "harmless if mis-selected": `equilibrate()` populates `phase_masses_kg` with a phase assemblage but leaves `ledger_transition` as `None`, and `simulator/core.py::_get_equilibrium` rejects exactly that combination with a `RuntimeError`. So selecting `MAGEMinBackend` as the active melt backend **fails closed by design** rather than being silently ignored — the honest consumer is the dedicated shadow comparator at `engines/magemin/parity.py`.

See `docs-private/chemistry-engine-binding-spec-2026-05-14.md` §4 for the MAGEMin input/output contract, and `engines/magemin/README.md` for the kernel-shadow scaffold.

## FactSAGE / ChemApp

FactSAGE support lives in `simulator/melt_backend/factsage.py`. ChemApp is imported only inside backend initialization, so the repo still imports, runs, and tests without FactSAGE, ChemApp, licenses, or local thermodynamic data.

The simulator does not load bundled FactSAGE databases. A user with a licensed FactSAGE/ChemApp environment should export the needed generally distributed database selection into a ChemApp-readable `.cst` or `.dat` file and point the local config at that file. The configured export determines the backend capabilities; selecting FactSAGE in the UI does not imply whole-regolith coverage.

The adapter targets the ChemApp for Python `chemapp.friendly` API:

1. `ThermochemicalSystem.load(datafile_path)`
2. `Units.set(P=bar, T=K, A=mol, E=J)`
3. `EquilibriumCalculation.set_IA_cfs(...)`
4. `EquilibriumCalculation.set_eq_T(...)`
5. `EquilibriumCalculation.set_eq_P(...)`
6. `EquilibriumCalculation.set_eq_AC_pc("GAS", "O2", 10**fO2_log)` unless `control_fO2` is explicitly disabled
7. `EquilibriumCalculation.calculate_eq(return_result=True)`

### Local Configuration

Create a local JSON config outside version control, then point the web app at it:

```bash
export FACTSAGE_CONFIG=config/factsage.local.json
```

`config/factsage.local.json` should be machine-local. Do not commit ChemApp binaries, license files, generated/commercial databases, or absolute paths from a real workstation.

The file may contain:

```json
{
    "chemapp_module": "chemapp.friendly",
    "datafile_path": "config/local-factsage-export.cst",
    "component_map": {"SiO2": "SiO2", "FeO": "FeO", "Fe2O3": "Fe2O3"},
    "species_map": {"Na": "Na", "K": "K", "SiO": "SiO"},
    "phase_map": {"liquid": ["LIQUID", "SLAG"], "gas": ["GAS"]},
    "amount_unit": "mol",
    "capabilities": {"silicate_melt": true},
    "timeout_s": 10
}
```

`datafile_path` and `database_path` are aliases for the local ChemApp-readable export. No default absolute path is hard-coded. Mappings are database-specific, so docs snippets are preferred over committed example config files.

Before starting the web app, a FactSAGE user can run:

```bash
python -m simulator.melt_backend.factsage_doctor --config "$FACTSAGE_CONFIG"
```

The doctor imports ChemApp, loads the configured data file, initializes the backend, and runs one smoke equilibrium. It does not install software or create generated artifacts.

### Mapping and Units

Simulator input composition is the cleaned silicate melt mol inventory from `AtomLedger`. Kg payloads are only external projections for reports, UI, and legacy helper calls. The FactSAGE/ChemApp adapter defaults to `amount_unit: "mol"` and sends mol amounts directly to ChemApp. If a local export is configured for `kg`, `g`, or `tonne`, the adapter can project the mol inventory into that unit at the boundary, but result parsing still requires species-resolved phase constituents before any mol-to-kg projection is accepted.

Default component mappings cover:

```text
SiO2 TiO2 Al2O3 FeO Fe2O3 MgO CaO Na2O K2O Cr2O3 MnO P2O5 NiO CoO
```

Many FactSAGE databases use different phase or constituent names. Override `component_map`, `species_map`, and `phase_map` for the local database. A component map value of `null` disables that simulator oxide; if that oxide is present in a run, the backend raises, marks itself unavailable, and the simulator falls back to the built-in Ellingham/Antoine path for that tick.

Backend capability metadata uses these names:

```text
silicate_melt gas_volatiles salt_phase sulfide_matte metal_alloy
```

FactSAGE defaults to `silicate_melt` only. Add capabilities only when the supplied `.cst`/`.dat` export actually supports them. The current `MeltBackend` call still receives cleaned melt oxides only; gas, salt, sulfide, and alloy inventories are preserved in simulator process state for future higher-level chemistry.

The backend returns simulator-facing units:

```text
temperature_C: Celsius
pressure_bar: bar
phase_masses_kg: kg
liquid_composition_wt_pct: wt%
vapor_pressures_Pa: Pa
fO2_log: log10(fO2 / 1 bar)
```

Gas phase constituent `AC` values are interpreted as fugacity in the active pressure unit and converted from bar to Pa for configured vapor species. Missing vapor species are omitted and recorded in `FactSAGEBackend.warnings`; they are not invented as zero pressures.

ChemApp uses process-global state in the documented friendly API. The adapter serializes the set-conditions/set-composition/calculate/read-result transaction with a process-local lock so concurrent web simulations do not interleave equilibrium inputs.

When FactSAGE/ChemApp is selected in the web UI but ChemApp or the configured data file is unavailable, the existing simulation status line reports that the built-in fallback is active. When it is active, the status line reports the configured export coverage, for example `FactSAGE/ChemApp export active: silicate melt only`.

### Current Limitations

- No local ChemApp/FactSAGE install or data file is included.
- No local installer or database generator is included.
- No FactSAGE database export is bundled; users provide local `.cst`/`.dat` files.
- `timeout_s` is accepted and recorded, but ChemApp runs in-process, so the adapter does not forcibly kill long calculations.
- Direct log-fO2 control is not exposed by name in the ChemApp docs. The adapter uses gas-phase O2 activity/fugacity control when available.
- Liquid viscosity is not calculated by FactSAGE here; the default `EquilibriumResult` viscosity remains in use.
- Phase and constituent naming are database-specific and should be configured for each local FactSAGE data file.

# Melt Chemistry Backends

The simulator can run without external thermodynamic software. The fallback path combines simplified Ellingham equilibrium logic with Antoine vapor-pressure data, which is useful for comparative exploration but not for validated melt chemistry.

## Per-call result status

`EquilibriumResult.status` records the per-call backend outcome: `'ok'` (engine ran and produced a usable result), `'not_converged'` (engine ran but did not produce one), `'out_of_domain'` (a DomainGate or account filter rejected the input), or `'unavailable'` (engine / library / binary absent for this call). It is descriptive only — `core.py::_get_equilibrium` continues to drive fallback decisions from `is_available()` and the raised-exception handlers, and surfaces the most recent value on `_last_backend_status` for diagnostics.

## Backend Order

The simulator checks melt backends in this order:

1. `PetThermoTools` Python package.
2. Project-local alphaMELTS binary at `engines/alphamelts/run_alphamelts.command`.
3. `alphamelts` executable on `PATH`.

The VapoRock wrapper checks whether the canonical `vaporock` Python package is importable, with legacy `VapoRock` import fallback for older local installs.

The FactSAGE/ChemApp backend is optional. It imports ChemApp only during initialization and falls back to the built-in Ellingham/Antoine path when unavailable. This fallback is adequate for process sequencing and feedstock comparison.

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

`EquilibriumResult.vapor_pressures_Pa` is the primary output. VapoRock does not mutate `AtomLedger`, does not produce phase assemblages, and does not own evaporation flux or ledger transitions before the VAPOROCK-AUTHORITY-PROMOTION goal.

For legacy helper outputs, pressure values with max `< 1e3` are treated as bar and scaled to Pa; larger values are treated as already-Pa. `capabilities()` keeps `vapor_melt_equilibrium=True` as a VapoRock instance-level extension, leaving `DEFAULT_BACKEND_CAPABILITIES` at the canonical five shared keys.

See `docs-private/chemistry-engine-binding-spec-2026-05-14.md` §4 for the VapoRock input/output contract.

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

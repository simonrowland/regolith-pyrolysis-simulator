# Melt Chemistry Backends

The simulator can run without external thermodynamic software. The fallback path combines simplified Ellingham equilibrium logic with Antoine vapor-pressure data, which is useful for comparative exploration but not for validated melt chemistry.

## Backend Order

The simulator checks melt backends in this order:

1. `PetThermoTools` Python package.
2. Project-local alphaMELTS binary at `engines/alphamelts/run_alphamelts.command`.
3. `alphamelts` executable on `PATH`.

The VapoRock wrapper checks whether the `VapoRock` Python package is importable.

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

The `melts` extra currently includes `PetThermoTools`. VapoRock may be installed separately if needed.

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

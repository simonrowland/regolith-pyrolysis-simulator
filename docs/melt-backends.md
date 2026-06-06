# Melt Chemistry Backends

In 0.5.0 the operational chain — VapoRock for `VAPOR_PRESSURE`, PetThermoTools / AlphaMELTS for `SILICATE_EQUILIBRIUM`, MAGEMin for `SILICATE_LIQUIDUS` and `GATE_LIQUID_FRACTION` — is the always-on production path; `vaporock` and `petthermotools` are `[project.dependencies]` (required), not optional extras. MAGEMin is a compiled C/Fortran binary built from source per `pyproject.toml [magemin]`. The pure-Antoine × Ellingham fallback is retained as a documented diagnostic path, gated behind `chemistry_kernel.allow_fallback_vapor: true` and the upstream `vaporock` import being unavailable; without both, the kernel re-raises rather than silently downgrading. FactSAGE / ChemApp is archived/removed in this checkout and is not selectable.

## Per-call result status

`EquilibriumResult.status` records the per-call backend outcome: `'ok'` (engine ran and produced a usable result), `'not_converged'` (engine ran but did not produce one), `'out_of_domain'` (a DomainGate or account filter rejected the input), or `'unavailable'` (engine / library / binary absent for this call). It is descriptive only — `core.py::_get_equilibrium` continues to drive fallback decisions from `is_available()` and the raised-exception handlers, and surfaces the most recent value on `_last_backend_status` for diagnostics.

## Backend Order

`web/events.py::_get_backend` is the single source of truth for active-backend selection. The active-backend eligibility policy
(see `\goal BACKEND-DEFAULT-SWITCH`, 2026-05-14) is:

1. **AlphaMELTS** is probed first. If `is_available()` (PetThermoTools or the project-local `alphamelts` binary at `engines/alphamelts/run_alphamelts.command`, or `alphamelts` on `PATH`) — selected as the active backend.
2. **`StubBackend`** is the always-available fallback for `auto` when AlphaMELTS is unavailable (built-in Ellingham/Antoine path inside `simulator/core.py`).
3. **FactSAGE / ChemApp** is not probed. The adapter was removed/archived; explicit `backend=factsage` is an unknown backend and raises instead of falling through to `auto`.

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

`vaporock` and `petthermotools` are declared in `[project.dependencies]` and are installed by the standard `pip install -e .` (and by `install-dependencies.py`). The VapoRock dependency is pinned to the upstream GitLab source tag because no PyPI release is available — `pyproject.toml` carries the canonical pin. Optional extras (`[magemin]`, `[sulfur]`, `[dev]`) install diagnostic engines and test tooling that are not on the production hot path.

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

1. The authoritative `VapoRockProvider` raised `ProviderUnavailableError` at dispatch time (the upstream `vaporock` library is missing on the host — uncommon in 0.5.0 since `vaporock` is in `[project.dependencies]`, but possible on partially-provisioned dev hosts), and
2. The simulator was constructed with `setpoints['chemistry_kernel']['allow_fallback_vapor'] = True`. The flag is read at `PyrolysisSimulator.__init__` and threaded into `ChemistryKernel.allow_fallback_intents`.

Otherwise the kernel re-raises `ProviderUnavailableError` -- **silent fallback is forbidden**. The pure-Antoine × Ellingham chain is ~124× off VapoRock at lunar mare 1500 °C IW for K (γ_K lives inside VapoRock and cannot be reproduced by `a_M × P_sat` alone), so the fallback is a diagnostic surface, not an operational substitute.

When the fallback path fires, the kernel tags the result's `diagnostic` map with `kernel_fallback_used = 'builtin-vapor-pressure'` so trace consumers can tell the authoritative slot did not answer.

The authority swap is visible on a single read of `sim._chem_registry.capability_summary()`:

```python
>>> sim._chem_registry.capability_summary()['vapor_pressure']
{'authoritative': 'vaporock', 'fallback': 'builtin-vapor-pressure', 'shadows': ()}
```

The six other kernel-authoritative builtins (`EVAPORATION_FLUX`, `EVAPORATION_TRANSITION`, `CONDENSATION_ROUTE`, `ELECTROLYSIS_STEP`, `METALLOTHERMIC_STEP`, `STAGE0_PRETREATMENT`) are unchanged by the swap; their `authoritative` slot still names the `builtin-<intent>` provider.

The `VapoRockProvider` filters its output to the species universe `data/vapor_pressures.yaml` declares (the intersection of the YAML's `metals` section with `engines.builtin.vapor_pressure._ELLINGHAM_THERMO` keys, plus the entire `oxide_vapors` section). VapoRock's broader ~30-species output (`O2`, `Si2`, `Al2O2`, ...) is a richer chemistry surface than the downstream `EVAPORATION_FLUX` step is wired for; pinning the filter to the builtin's effective species set keeps the mass balance hard constraint (0.000%) intact across the swap. Future work can widen this set as the downstream stoichiometry validators learn each new species.

The fallback Antoine convention is schema-bound in `data/vapor_pressures_schema.md`: each YAML row declares whether its raw Antoine term is `pure_component_psat`, `pseudo_psat_backsolved_from_vaporock`, or `standard_reaction_term`. This keeps Ellingham activity, VapoRock back-solve provenance, and oxide-vapor reaction exponents auditable without changing fallback math.

Test coverage: `tests/chemistry/test_vaporock_authority_promotion.py` binds the five acceptance scenarios (available + no flag, unavailable + no flag, unavailable + flag, available + flag, capability_summary truth). `tests/chemistry/test_kernel_registry.py` covers the registry's new fallback semantics (mutual exclusivity with shadow, authority-capable requirement, idempotent re-registration).

## AlphaMELTS Adapter Notes

- Transport selection is `thermoengine` -> `python_api` -> `subprocess` when
  available. `thermoengine` is a transport behind the existing
  `AlphaMELTSProvider`, not a new provider or authority.
- The PetThermoTools fallback imports `petthermotools` and preloads `meltsdynamic.MELTSdynamic` during initialization.
- Inputs are gated to `process.cleaned_melt` silicate oxides and normalized to the 14-oxide MELTS basis.
- Gas, metal, salt, sulfide, halide, and low-major-oxide material is rejected before the engine.
- `FeO_total` requires `QFM`, `NNO`, `IW`, `HM`, or configured `Fe3Fet`; no silent split.
- `fO2_offset` is buffer-relative, and parsed results fill diagnostics only; AlphaMELTS emits no ledger transition.
- Silicate requests carry `fe_redox_policy='intrinsic'` by default. Today that means the simulator derives intrinsic `fO2_log` from the cleaned melt composition and surfaces the applied `Fe3Fet` split on `LiquidusDiagnostics`; it does not run a Kress91 ferric/ferrous glass model.

### MELTS activity convention

MELTS/ThermoEngine activity data is a chemical-potential surface. The correct pure-endmember convention is:

```text
a_i = exp((mu_i - mu_i0) / (R T))
```

where `mu_i` is the melt chemical potential in J/mol and `mu_i0` is the pure-endmember reference at the same `T,P` (`gibbs_energy(T, P, pure_oxide)`). This is the VapoRock convention: its gas abundance path builds `ln(a)` from `(mu - mu0) / RT` before evaluating vapor reactions.

Do not interpret a MELTS chemical potential as an activity coefficient `gamma`, and do not compute `P_i = gamma_i * x_i * P_i0` from a chemical-potential engine. That mixes conventions: the mole-fraction term is already embedded in the activity defined by `mu - mu0`. The error is silent and can be O(10^n) in vapor pressure because it exponentiates through `RT`.

ThermoEngine mode populates the AlphaMELTS activity field from live `mu/mu0`
API calls. The PetThermoTools fallback still reports activities absent unless
it exposes a verified live `mu/mu0` pair. VapoRock remains the vapor authority
and performs its own `mu -> a` conversion internally; the AlphaMELTS activity
field is diagnostic transport metadata, not ledger authority.

## MAGEMin adapter notes

`simulator/melt_backend/magemin.py` wraps MAGEMin, an open-source Gibbs free-energy minimiser for silicate phase equilibria. The adapter probes for MAGEMin lazily inside `initialize()`; a missing binary marks the backend unavailable and returns warnings instead of crashing the simulator.

**Bridge choice.** MAGEMin has no pure-PyPI package — the upstream clone ships zero Python files, no `setup.py`, no `pyproject.toml`. Its primary interface is Julia (`MAGEMin_C.jl`); from Python it is reached either through that Julia bridge or by driving the compiled `MAGEMin` binary over a subprocess. The adapter's supported default is the **subprocess bridge**: `initialize()` locates the compiled binary (a sibling clone at `../MAGEMin/MAGEMin`, or `engines/magemin/{,bin/}MAGEMin`, or `MAGEMin` on `PATH`) and `_call_magemin` invokes it with `--Verb=0` single-point arguments, parsing the compact `Phase :` / `Mode :` stdout block. The optional `pymagemin` and `julia` bridges are still probed first when a caller has them installed; the `ctypes` bridge is opt-in only (`python_bridge="ctypes"`) because its struct marshaling is unimplemented and auto-preferring it would shadow the working subprocess path. See `pyproject.toml` `[magemin]` for the build path.

**Oxide basis.** MAGEMin and alphaMELTS share the 14-oxide MELTS basis (`simulator.state.OXIDE_SPECIES`: SiO2, TiO2, Al2O3, FeO, Fe2O3, MgO, CaO, Na2O, K2O, Cr2O3, MnO, P2O5, NiO, CoO), so shadow comparisons are a straight rename. The adapter projects the mol-native simulator melt inventory into oxide wt% over that basis. For the binary's igneous (`ig`) database the wt% vector is folded onto MAGEMin's `ig` bulk order (`SiO2, Al2O3, CaO, MgO, FeOt, K2O, Na2O, TiO2, O, Cr2O3, H2O`): FeO and Fe2O3 combine into the single FeOt total-iron component; explicit Fe2O3 (or the spectroscopic total-iron-as-FeO convention when no Fe2O3 split is present) provisions the free `O` redox component so the qfm buffer can engage; oxides outside the `ig` system (MnO, P2O5, NiO, CoO) are dropped. Metal, sulfide, salt, and halide accounts are never passed to MAGEMin — when called with the layered ABC's `composition_mol_by_account`, only `process.cleaned_melt` is consumed and every other account is reported as a dropped-account warning.

**Pressure unit.** The binding-spec contract is pressure in **GPa, not bar** (binding spec §4). The adapter converts `pressure_bar` to `P_GPa` with 1 GPa = 10000 bar, then `P_GPa` to kilobar with 1 GPa = 10 kbar at the binary boundary (the MAGEMin CLI's `--Pres` argument takes kilobar). Both conversion legs are named (`_pressure_bar_to_GPa`, `_GPa_to_kbar`) so the unit chain is auditable — a wrong factor here is a silent order-of-magnitude pressure error.

**Shadow / diagnostic posture.** MAGEMin is shadow-only for `SILICATE_LIQUIDUS` and `SILICATE_EQUILIBRIUM` (binding spec §3 authority matrix); it runs alongside the authoritative alphaMELTS path, never instead of it. `ledger_account_policies()` returns no ledger-authoritative policy and `equilibrate()` never populates `EquilibriumResult.ledger_transition` — MAGEMin holds no `AtomLedger` authority and must not be granted any. "Diagnostic" does not mean "harmless if mis-selected": `equilibrate()` populates `phase_masses_kg` with a phase assemblage but leaves `ledger_transition` as `None`, and `simulator/core.py::_get_equilibrium` rejects exactly that combination with a `RuntimeError`. So selecting `MAGEMinBackend` as the active melt backend **fails closed by design** rather than being silently ignored — the honest consumer is the dedicated shadow comparator at `engines/magemin/parity.py`.

See `docs-private/chemistry-engine-binding-spec-2026-05-14.md` §4 for the MAGEMin input/output contract, and `engines/magemin/README.md` for the kernel-shadow scaffold.

## FactSAGE / ChemApp

FactSAGE / ChemApp support is archived/removed in this checkout. There is no live `simulator/melt_backend/factsage.py`, no `factsage_doctor`, and no supported `FACTSAGE_CONFIG` path.

`backend=factsage` is an explicit unknown backend and raises a backend-selection error. Use `alphamelts`, `stub`, or the web/API `auto` path.

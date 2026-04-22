# Regolith Pyrolysis Simulator — Implementation Plan

## Context

This is a companion simulator for the Oxygen Shuttle lunar regolith refinery design. It models the six-campaign pyrometallurgical ISRU process (C0–C6) that extracts metals + O₂ from regolith through selective vacuum pyrolysis, alkali metallothermic shuttles, limited MRE, and Mg thermite reduction.

Two web interfaces share one backend:
- **Simulator** (`/`) — full parameter control, engine selection, detailed assumptions
- **Operator game** (`/lunar-operator`) — 10–15 line refinery, autonomous with player intervention at key decisions

The simulator runs hour-by-hour continuous simulation with evolving melt composition, overhead gas dynamics, and condensation train modeling.

**Code readability tiers:**
- **Tier 1 (chemist-readable)**: `core.py` — heavily commented, explains the chemistry, readable data structures and loop
- **Tier 2 (engine-readable)**: `condensation.py`, `overhead.py`, `electrolysis.py`, and the evaporation/vortex code — clear, concise, accurate simulation code with physics explanations, equation references, and unit conventions. Written so a scientist can verify the physics.
- **Tier 3 (just works)**: `web/`, `game/`, `melt_backend/` wrappers — functional code with docstrings explaining what it does, not how

---

## Key Decisions

| Decision | Choice |
|----------|--------|
| Web stack | Flask + HTMX + Flask-SocketIO (hybrid: SocketIO for real-time push, HTMX for forms/navigation) |
| Time resolution | Hour-by-hour continuous |
| Game scope | Multi-line simplified (autonomous lines, player intervenes on decisions/harvests, full parameter control available) |
| Charts | Plotly interactive (temperature, composition, pressure, mass flow) |
| Melt engine | Require AlphaMELTS (auto-install in project subfolder if not found). FactSAGE optional. Engine selection in analysis mode only, game auto-selects. |
| Data persistence | Local YAML files (single-user) |
| Energy tracking | kWh per stage and per batch; avg kW in hourly sim (turbine, volatiles condenser, electrolysis). Solar assumed for hot-walls. |
| Thermal model | Simplified: solar concentrator maintains melt at target T. Auto-size concentrator based on batch mass + peak T. Don't model heat input/output in detail. |
| Equipment auto-sizing | Auto-design tool: given batch mass + feedstock → size pipes, condensers, turbine, concentrator appropriately. |
| Game code org | Separate `/game` folder tree — game-specific code treats the main simulator as a library. |

---

## Architecture Overview

```
regolith-pyrolysis-simulator/
├── app.py                          # Entry point, Flask app, port 3000
├── requirements.txt
├── pyproject.toml
├── data/
│   ├── feedstocks.yaml             # ~15 built-in feedstock compositions
│   ├── setpoints.yaml              # Campaign params, condensation train config
│   ├── vapor_pressures.yaml        # Antoine params for pure-component vapor pressures
│   ├── custom_compositions.yaml    # User-created (initially empty)
│   └── test_runs.yaml              # Run history (initially empty)
├── engines/                        # Auto-installed melt engines live here
│   └── alphamelts/                 # AlphaMELTS binary auto-downloaded here
├── simulator/
│   ├── __init__.py
│   ├── core.py                     # ★ THE readable file — state, loop, mass balance
│   ├── campaigns.py                # Campaign-specific logic (C0–C6)
│   ├── condensation.py             # 6-stage condensation train model
│   ├── overhead.py                 # Overhead gas + turbine flow rate
│   ├── mass_balance.py             # Input/output tracking + impurity partitioning
│   ├── energy.py                   # kWh tracking per stage/batch
│   ├── equipment.py                # ★ Auto-design: pipe, condenser, turbine, concentrator sizing
│   ├── electrolysis.py             # MRE model: Nernst + Faraday + current efficiency
│   ├── decision_tree.py            # Path A/B, Branch One/Two, Root Branch logic
│   ├── persistence.py              # YAML save/load for runs + custom compositions
│   └── melt_backend/
│       ├── __init__.py
│       ├── base.py                 # Abstract MeltBackend + EquilibriumResult
│       ├── alphamelts.py           # AlphaMELTS wrapper (PetThermoTools or subprocess)
│       ├── factsage.py             # FactSAGE/ChemApp wrapper (stub)
│       ├── vaporock.py             # VapoRock integration for vapor pressures
│       └── installer.py           # Auto-download AlphaMELTS binary into engines/
├── web/
│   ├── __init__.py
│   ├── routes.py                   # Flask routes + HTMX partials for simulator UI
│   ├── events.py                   # SocketIO event handlers for simulator UI
│   ├── templates/
│   │   ├── base.html               # Shared layout (HTMX, SocketIO, Plotly CDN)
│   │   ├── simulator.html          # Simulator interface
│   │   └── partials/               # HTMX fragments (feedstock cards, controls, disclosures)
│   └── static/
│       ├── css/style.css           # Light theme
│       └── js/
│           └── simulator.js        # Simulator UI + Plotly charts
├── game/                           # Game-specific code (uses simulator/ as a library)
│   ├── __init__.py
│   ├── refinery.py                 # Multi-line manager + shared inventory
│   ├── routes.py                   # Flask routes for /lunar-operator
│   ├── events.py                   # SocketIO events for game mode
│   ├── templates/
│   │   ├── operator.html           # Game interface
│   │   └── partials/               # Game-specific HTMX fragments
│   └── static/
│       ├── css/game.css            # Game-specific styles
│       └── js/
│           └── operator.js         # Game UI logic
└── docs/
    ├── README.md                   # Setup + usage
    ├── ARCHITECTURE.md             # System overview
    └── CHEMISTRY.md                # Oxygen Shuttle process for developers
```

---

## Thermodynamic Library Stack

| Layer | Library | Purpose |
|-------|---------|---------|
| Melt equilibria | **PetThermoTools** → alphaMELTS for Python | Phase assemblage, activities, liquid composition at (T, comp, fO₂) |
| Vapor pressures | **VapoRock** (ENKI) | 34 vapor species over silicate melts using MELTS + JANAF tables |
| Evaporation flux | **Our code** (Hertz-Knudsen-Langmuir) ★ | Mass transfer from melt surface using VapoRock vapor pressures |
| Condensation | **Our code** (species routing by T) ★ | Route vapor to 6 condensation stages by condensation temperature |
| Cyclone/vortex | **Our code** (Lapple/Leith-Licht) ★ | Vortex dust filter collection efficiency |
| Electrolysis | **Our code** (Nernst + Faraday + Schreiner empirical) ★ | MRE voltage-species selectivity, current efficiency, energy |

★ = **"Engine" modules**: written for clarity and accuracy like `core.py`. Well-commented with physics explanations, unit conventions, equation references. These get a dedicated documentation pass in Phase 6.
| Electrolysis (hi-fi) | **COMSOL** via MPh (optional) | Full multiphysics MRE cell model — requires COMSOL license |
| Math | **NumPy + SciPy** | Numerical computation |
| Fallback (no Python API) | alphaMELTS subprocess | Write `.melts` files, run binary, parse `*_tbl.txt` output |

AlphaMELTS binary auto-installed into `engines/alphamelts/` subdirectory if not found on system.

---

## Phase 0: Project Skeleton

**Goal**: Bootable Flask app on port 3000 with feedstock data loaded and displayed.

**Files**: `app.py`, `requirements.txt`, `pyproject.toml`, `data/feedstocks.yaml`, `data/setpoints.yaml`, `data/vapor_pressures.yaml`, `web/routes.py`, `web/events.py`, `web/templates/base.html`, `web/templates/simulator.html`, `web/templates/operator.html`, `web/static/css/style.css`

**Key details**:
- `requirements.txt`: flask, flask-socketio, pyyaml, plotly, numpy, scipy, PetThermoTools
- `data/feedstocks.yaml`: All ~15 feedstocks from context-feedstocks.yaml, converted to simulator format with midpoint compositions + ranges
- `data/setpoints.yaml`: Campaign parameters from context-setpoints.yaml
- `data/vapor_pressures.yaml`: Antoine equation parameters for Na, K, Mg, Fe, SiO, Ca, Al, Si, Ti, Cr from NASA Glenn/JANAF tables
- `web/templates/base.html`: HTMX (CDN), socket.io.js (CDN), Plotly (CDN), shared nav with routes to `/` and `/lunar-operator`
- Light CSS theme

**Verify**: `python app.py` → open `localhost:3000` → see feedstock dropdown populated from YAML

---

## Phase 1: Core Data Structures + Simulation Loop

**Goal**: The heart of the simulator. `simulator/core.py` with all data classes and the hour-by-hour `step()` loop. This file is heavily commented for chemist readability.

### `simulator/core.py` — structure

```
SECTION 1: CONSTANTS
  - OXIDE_SPECIES list (13 species)
  - METAL_SPECIES, GAS_SPECIES lists
  - MOLAR_MASS dict, physical constants
  - CampaignPhase enum, DecisionPoint enum

SECTION 2: DATA STRUCTURES
  - MeltState: temperature_C, composition_kg, atmosphere, campaign, hour, fO2_log, stir_factor
  - CondensationStage: stage_number, label, temp_range, collected_kg, target_species
  - CondensationTrain: 6 stages, total_by_species()
  - HourSnapshot: full system state at one moment (melt + train + flux + pressure + turbine + energy)
  - BatchRecord: full run history (snapshots, decisions, products, energy_total_kWh)
  - RefineryState: multi-line game state + shared inventory

SECTION 3: SIMULATION ENGINE (PyrolysisSimulator class)
  - __init__(melt_backend, setpoints, feedstocks)
  - load_batch(feedstock_key, mass_kg, additives_kg)
  - start_campaign(campaign)
  - step() → HourSnapshot  [THE CORE LOOP — 8 steps, each well-commented]
  - _calculate_evaporation(equilibrium) → Dict[str, float]  [Hertz-Knudsen]
  - _route_to_condensation(evap_flux)
  - _update_melt_composition(evap_flux)
  - _update_overhead_gas() → (pressure_mbar, turbine_flow_kg_hr)
  - _calculate_energy_this_hour() → Dict[str, float]  [kWh for turbine, condenser, MRE]
  - _check_campaign_endpoint(settings)
  - get_decision_options() → Optional[DecisionPoint]
  - apply_decision(decision, choice)
```

### Supporting files

- `simulator/campaigns.py` — CampaignManager: campaign sequencing, ramp rates, endpoint detection, transitions
- `simulator/condensation.py` — CondensationModel: models vapor flow through the 6-stage train with physically meaningful separation. Key features:

  **Flow architecture per stage:**
  - Baffle geometry (count, spacing, surface area) → determines residence time
  - Condensation efficiency: fraction of each species that condenses here vs passes through
  - Temperature profile within the stage (inlet T, outlet T, gradient across baffles)
  - Chevron/demister separators at stage exits to catch entrained droplets

  **Inter-stage boundaries:**
  - Radiation gap + insulated wall → sharp temperature step between stages
  - Gate valves between stages — can isolate sections during specific campaigns
  - Flow restriction geometry → controls vapor velocity at boundary

  **The Fe → SiO separation problem (Stage 1 → Stage 2):**
  - Stage 1 held at 1200–1400°C; Fe condenses as liquid draining to sump
  - SiO passes through (condensation T 900–1200°C, mostly below Stage 1 operating T)
  - Residence time in Stage 1 must be long enough for >99% Fe condensation
  - Chevron separator at Stage 1 exit catches entrained Fe droplets
  - Sharp T boundary (1400→1200°C gap) prevents SiO premature condensation in Stage 1
  - Impurity: some Fe (~0.1–1%) will pass to Stage 2; some SiO (~0.5–2%) will condense early in Stage 1

  **Condensation train topology:**
  Two trains branch from the crucible/hot duct:

  1. **Volatiles train** (active during C0 only): cold traps for CHNOPS, H₂O, S, CO₂, halides, perchlorates. For feedstocks needing carbon-reduction-aided C0 (KREEP, Mars, CI), also handles SO₂ sorbent, HCl/HF scrubber, CO₂/CO separator. **Gate valve at the junction seals the volatiles train** once IR spectrometer confirms volatile emissions have stopped (including after any carbon reduction step). This gate stays closed for all subsequent campaigns.

  2. **Metals train** (active C1 onward): Stage 0 hot duct (>1400°C) → Stage 1 Fe condenser (1100–1400°C) → Stage 2 SiO zone (900–1200°C, removable baffles) → Stage 3 alkali/Mg cyclone (350–700°C) → Stage 4 vortex dust filter (200–350°C) → Stage 5 turbine/compressor → Stage 6 O₂ accumulator (~3 bar).

  Oxygen offtake is at the cold end (Stage 5 turbine). The turbine speed sets the upstream pO₂ — this is the primary process control for SiO suppression (√pO₂ dependence gives >300× suppression at millibar O₂). The metals train stages are NOT isolated by gates during normal operation.

  Hot-walling: Unused sections can be held above condensation temperature to prevent deposition (e.g., during C2B when SiO suppression means essentially no SiO reaches Stage 2 — the zone stays clean via pO₂ control, not physical closure).

  Gate valves in the metals train exist only for: maintenance access, cartridge replacement (Stage 2 removable SiO baffles), and emergency isolation.

  **Campaign-specific flow behavior (via pO₂ and temperature control, not gates):**
  - C0: Hard vacuum, volatiles flow through full train
  - C1/C2A (Path A): pN₂ sweep, Na/K/Fe/SiO all flowing through train
  - C2B (Path B): pO₂ managed — SiO suppressed at source (>300×), barely reaches Stage 2
  - C3 bakeout: High pO₂ drives alkali bakeout; metals suppressed at source
  - C4: Mg vapor dominant; pO₂ managed to suppress SiO
  - C5 MRE: O₂ evolves at anode, flows to accumulator; no metal vapor in train

  **Condensation efficiency model (per species per stage):**
  ```
  η_condense = 1 - exp(-residence_time / τ_condensation)
  τ_condensation = f(T_stage, T_condense_species, surface_area, sticking_coefficient)
  ```
  Where τ_condensation is the characteristic condensation time for species i at the stage temperature. If the stage T is well below the species condensation T, η → 1 (everything condenses). If the stage T is near or above the species condensation T, η → 0 (passes through).

  Impurity fractions are displayed per-stage in the UI.
- `simulator/overhead.py` — OverheadGasModel: gas composition above melt, turbine flow rate calc, pipe pressure, buffer tank
- `simulator/mass_balance.py` — MassBalance: inputs vs outputs conservation check, product purity by stream, impurity partitioning (e.g., 0.5% V in Fe tap)
- `simulator/energy.py` — EnergyTracker: kWh per campaign stage, cumulative per batch, instantaneous kW (turbine compression, condenser cooling, MRE electrolysis). Solar hot-wall energy tracked but marked as solar-thermal.
- `simulator/decision_tree.py` — DecisionTree: Path A/B evaluation (SiO₂ extraction vs CMAS glass preservation), Branch One/Two (MRE scope), and now also Root Branch selection (Pyrolysis Track vs Standard MRE)
- `simulator/electrolysis.py` — MRE electrochemistry model: Nernst equation for decomposition voltages at actual melt conditions, Faraday's law for mass-current, current efficiency model, species selectivity at overlapping voltages. Used by both the C5 limited MRE campaign and the Standard MRE baseline mode.
- `simulator/equipment.py` — EquipmentDesigner: auto-sizes plant given batch mass + feedstock

### `simulator/equipment.py` — Auto-Design Tool

Given a batch mass (e.g., 1 tonne or 100 tonnes) and feedstock, automatically sizes all equipment:

```python
class EquipmentDesigner:
    """
    Auto-sizes the refinery equipment for a given batch.

    Given batch mass + feedstock + peak campaign temperature,
    calculates appropriate sizes for all major equipment.
    """

    def design_for_batch(self, mass_kg, feedstock, peak_T_C) → PlantDesign:
        """Returns a PlantDesign with all equipment dimensions."""

    # --- Individual sizing methods ---

    def size_crucible(self, mass_kg, melt_density) → CrucibleSpec:
        # Volume = mass / density, assume height = 1.5 × diameter
        # Add 20% freeboard for bubbling/stirring

    def size_solar_concentrator(self, mass_kg, peak_T_C, feedstock) → ConcentratorSpec:
        # Power needed = mass × c_p(melt) × dT/dt_peak + radiation_loss(T⁴)
        # Lunar insolation: 1361 W/m², concentrator efficiency ~85%
        # aperture_m2 = power_kW / (1.361 × 0.85)
        # Scale reference: 100 m² → ~136 kW → ~1 tonne batch
        # For 100 tonnes: ~10,000 m² (or parallel concentrators)

    def size_collection_pipe(self, peak_evap_rate_kg_s, pressure_mbar) → PipeSpec:
        # At millibar pressures: viscous flow regime (Kn << 0.01)
        # Conductance: C = π×d⁴×p̄/(128×η×L) [Poiseuille]
        # Require C ≥ peak_evap_rate / acceptable_pressure_drop
        # Reference: 12 cm pipe handles 7-16 g/s SiO at 10 mbar

    def size_condensation_stages(self, peak_evap_rate, species_mix) → List[CondenserSpec]:
        # Per stage:
        #   Volume: sized for residence time ≥ 3×τ_condensation of target species
        #   Baffles: count = ceil(volume / single_baffle_volume), spacing for target Re
        #   Surface area: A = Q / (U × ΔT_lm) where Q = m_dot × ΔH_condensation
        #   Chevron/demister at exit: sized for droplet capture at stage flow velocity
        #   Gate valve: between each stage, sized for pipe diameter
        # Inter-stage boundaries:
        #   Radiation gap: sized for desired ΔT step (typ. 200-400°C between stages)
        #   Insulation: thickness for max heat leak budget
        # Reference: Stage 1 (Fe) at 1100-1400°C needs refractory MZO surfaces
        # Reference: Stage 2 (SiO) uses removable fused silica baffles (self-compatible)

    def size_turbine(self, O2_throughput_kg_hr, inlet_P_mbar, outlet_P_bar) → TurbineSpec:
        # Compression power: isentropic work + efficiency losses
        # Reference: 15-30 kWh per tonne for compression to ~3 bar
        # Scale with batch size

    def size_buffer_tanks(self, mass_kg) → BufferTankSpec:
        # O₂ accumulator at ~3 bar
        # Size for peak 1-hour O₂ production × safety margin

    def size_cold_sinks(self, condensation_rates) → ColdSinkSpec:
        # Radiation cooling sufficient at some stages (lunar vacuum)
        # Active cooling needed at lower-T stages
```

The auto-design runs once when the user sets batch mass/feedstock, and the resulting `PlantDesign` is:
1. Used as parameters for the simulation (pipe conductance limits evap rate, condenser area limits condensation rate)
2. Displayed in the disclosure triangles under "Equipment Sizing"
3. Editable by the user (override any auto-calculated value)

### `simulator/electrolysis.py` — MRE Electrochemistry Model

Models both the C5 limited MRE (pyrolysis track) and the full Standard MRE baseline mode.

```python
class ElectrolysisModel:
    """
    Molten Regolith Electrolysis simulator.

    Models the electrochemical reduction of oxide species from
    a silicate melt at controlled voltage and temperature.

    Physics:
    - Nernst equation: E = E° - (RT/nF) × ln(a_oxide)
      Adjusts standard decomposition voltages for actual melt
      activities (from MELTS) and temperature.
    - Faraday's law: m = (I × t × M) / (n × F)
      Converts current to mass of metal reduced.
    - Current efficiency: empirical model from Schreiner (MIT)
      η = f(V, T, composition, electrode_area)
    - Species selectivity: at overlapping voltage windows,
      current partitions between species proportional to
      their exchange current densities.
    """

    def __init__(self, melt_backend):
        # Standard decomposition voltages (from context THERMO-9)
        self.decomp_voltages = {
            'Na2O': 0.5, 'K2O': 0.5, 'FeO': 0.6,
            'Cr2O3': 0.9, 'V2O5': 0.9, 'MnO': 1.0,
            'SiO2': 1.4, 'TiO2': 1.5, 'Al2O3': 1.9,
            'MgO': 2.2, 'CaO': 2.5
        }

    def nernst_voltage(self, species, T_C, activity) → float:
        # Adjust E° for actual conditions

    def step_hour(self, melt_state, voltage_V, current_A, T_C) → MREResult:
        # For each species with E_nernst < applied voltage:
        #   Calculate fraction of current going to this species
        #   Apply Faraday's law to get mass reduced
        #   Track: metal deposited at cathode, O₂ evolved at anode
        # Returns: mass reduced per species, O₂ produced, energy consumed

    def get_reduction_sequence(self, melt_state, T_C) → List[Tuple[str, float]]:
        # Returns species in order of increasing Nernst voltage
        # at current melt composition — shows what reduces first

    def estimate_energy_kWh(self, melt_state, target_voltage) → float:
        # Total electrical energy to process to target voltage
```

### Root Branch: Standard MRE vs Pyrolysis Track

The decision tree now has a **root-level branch** before any campaigns:

```
Root Decision ────┬── Pyrolysis Track (default)
                  │   C0 → C1/C2A/C2B → C3 → C4 → C5(limited MRE) → C6
                  │   Solar-thermal dominant, ~1200-2000 kWh/t electrical
                  │
                  └── Standard MRE Baseline
                      C0 (+ optional C0b carbon reduction) → MRE ramp to 2.5V
                      Pure electrolysis, ~2650-4050+ kWh/t electrical
                      Baseline comparison for the pyrolysis approach
```

**Standard MRE mode:**
1. C0 devolatilization (same as pyrolysis track)
2. Optional C0b: carbon reduction step for P/halide-rich feedstocks (same controlled-pO₂ cleanup as the pyrolysis track uses for KREEP/Mars/CI — this is shared code)
3. Heat to melting (~1500-1600°C)
4. Ramp voltage from 0 → 2.5V, stepping through the decomposition sequence
5. At each voltage step, track: which species reduce, mass of metal deposited (as a mixed tap or individual skims), O₂ evolved, current efficiency, energy consumed
6. Terminal slag composition after electrolysis exhaustion
7. Product quality: metal mixtures at each voltage step (e.g., Fe-Cr-V alloy at 0.6-1.0V, Si-Ti at 1.4-1.6V, Al at 1.9V, Mg-Ca at 2.2-2.5V)

This gives the user a direct side-by-side comparison of the pyrolysis vs pure-MRE approach for any feedstock.

**Verify**: Test script loads mare basalt, steps 10 hours of C0 with stub backend, verifies temperature ramps and snapshots record correctly. Also: run Standard MRE mode on same feedstock, verify total electrical energy matches literature range (~2650-4050 kWh/t).

---

## Phase 2: Melt Backend + AlphaMELTS Integration

**Goal**: Abstract melt backend interface + working AlphaMELTS wrapper + auto-installer.

### `simulator/melt_backend/base.py`

```python
class EquilibriumResult:
    temperature_C, pressure_bar
    phases_present, phase_masses_kg, phase_compositions
    liquid_fraction, liquid_composition_wt_pct, liquid_viscosity_Pa_s
    vapor_pressures_Pa  # P_sat for each vapor species (from VapoRock or direct)
    activity_coefficients  # in the melt
    fO2_log

class MeltBackend(ABC):
    initialize(config) → bool
    is_available() → bool
    equilibrate(temperature_C, composition_kg, fO2_log, pressure_bar) → EquilibriumResult
    get_vapor_species() → List[str]
```

### `simulator/melt_backend/alphamelts.py`

Two modes:
1. **Python API** (preferred): Use PetThermoTools → alphaMELTS for Python
2. **Subprocess fallback**: Write `.melts` input files, run `alphamelts` binary from `engines/alphamelts/`, parse `*_tbl.txt` output

Vapor pressures: combine MELTS activity coefficients with VapoRock or pure-component Antoine parameters: `P_i_sat = activity_i × P_pure_i(T)`

### `simulator/melt_backend/vaporock.py`

Wrapper around VapoRock's vapor equilibrium calculator. Given melt composition + T + fO₂ → returns vapor species partial pressures for all 34 species in the Si-Mg-Fe-Al-Ca-Na-K-Ti-Cr-O system.

### `simulator/melt_backend/installer.py`

- Detect platform (macOS Intel/ARM, Linux, Windows)
- Download alphaMELTS binary from GitHub releases (`magmasource/alphaMELTS`)
- Extract to `engines/alphamelts/` within the project directory
- Verify installation by running `alphamelts --version`
- Also check/install PetThermoTools and VapoRock via pip if not present
- Report status to user (what's available, what's missing)

### `simulator/melt_backend/factsage.py`

Stub implementation. Checks for ChemApp Python package. Full implementation deferred (user doesn't have FactSAGE).

### Optional: COMSOL MRE backend (Phase 5+)

For high-fidelity electrolysis modeling, support COMSOL via [MPh](https://github.com/MPh-py/MPh) (third-party Python→Java bridge, `pip install MPh`). Requires user to have COMSOL installed with license. We'd ship a `.mph` model file defining MRE cell geometry/physics. From Python: load model → set composition/T/voltage → run → extract results. Much slower (seconds-to-minutes per solve) but gives current distribution, bubble dynamics, thermal coupling that our Nernst/Faraday model doesn't capture.

**Verify**: Run single equilibrium at 1200°C with mare basalt composition. MELTS returns phase assemblage. Hertz-Knudsen produces nonzero Na/K evaporation flux.

---

## Phase 3: Professional Web Interface

**Goal**: The `/` route with real-time Plotly charts, parameter controls, disclosure triangles.

### Layout sections

1. **Header bar**: Engine selector (AlphaMELTS / FactSAGE dropdown — analysis mode only), Start/Pause/Resume buttons, speed control (1 sec/hr default, fast-as-possible option)
2. **Feedstock selector**: Dropdown of all feedstocks (built-in + custom), HTMX-loaded composition table
3. **Parameter panel**: Batch mass (kg), additives (Na/K/Mg/Ca/C from inventory), campaign overrides (dT/dt, pO₂ target, stir factor)
4. **Four Plotly charts** (updated via SocketIO):
   - Temperature profile (T vs hour, campaign boundaries marked)
   - Melt composition (stacked area: each oxide kg vs hour)
   - Overhead pressure (pO₂ mbar vs hour, target range shaded)
   - Mass flow (evaporation rate by species kg/hr vs hour)
5. **Condensation train display**: 6 stages, accumulated kg per species, impurity fractions
6. **Mass balance panel**: kg in (regolith + additives) vs kg out (metals + O₂ + glass + slag)
7. **Energy panel**: kWh per campaign, cumulative, current avg kW (turbine / condenser / MRE)
8. **Disclosure triangles** (nested `<details>` elements):
   - Per campaign: dT/dt, pO₂ range, process controls, equipment sizing
   - Overall: furnace parameters, condensation train config, decision tree logic
9. **Decision point modal**: When Path A/B or Branch One/Two needed, overlay with recommendation + choice buttons
10. **Harvest checkboxes**: After run-to-completion, checkboxes like "Harvest industrial glass at C2", "Continue with Ca thermite C6"

### SocketIO events

- `start_simulation` → creates simulator thread, begins stepping
- `simulation_tick` → pushes HourSnapshot data to charts each hour
- `decision_required` → prompts user for Path/Branch choice
- `pause_simulation` / `resume_simulation`
- `adjust_parameter` → live parameter changes mid-run

### HTMX partials (loaded on demand)

- `partials/feedstock_card.html` — composition table for selected feedstock
- `partials/campaign_controls.html` — override controls for a campaign
- `partials/disclosure_assumptions.html` — nested assumptions for a stage
- `partials/condensation_display.html` — train stage visualization
- `partials/decision_modal.html` — Path/Branch choice
- `partials/results_summary.html` — final inputs/outputs table

**Verify**: Start simulation via UI, watch Plotly charts update live, pause/resume, adjust dT/dt mid-run, reach decision point and choose Path A.

---

## Phase 4: Operator Game Interface

**Goal**: `/lunar-operator` with multi-line refinery overview. All game-specific code lives in `/game` and imports from `simulator/` as a library.

### `/game` folder structure

```
game/
├── __init__.py
├── refinery.py         # RefineryManager + SharedInventory
├── routes.py           # Flask blueprint for /lunar-operator
├── events.py           # SocketIO events for game mode
├── templates/
│   ├── operator.html   # Main game interface
│   └── partials/       # Game HTMX fragments (line cards, inventory, decisions)
└── static/
    ├── css/game.css    # Game-specific styles
    └── js/operator.js  # Game UI logic
```

### `game/refinery.py`

```python
from simulator.core import PyrolysisSimulator, HourSnapshot, BatchRecord
from simulator.equipment import EquipmentDesigner

class RefineryManager:
    lines: Dict[str, PyrolysisSimulator]  # 10-15 lines
    shared_inventory: SharedInventory     # Na, K, Mg, Ca, C, O₂, products
    game_clock: int                       # global hour counter
    equipment: Dict[str, PlantDesign]     # auto-designed per line

    add_line(line_id, feedstock_key, mass_kg)  # auto-designs equipment
    step_all() → Dict[str, HourSnapshot]  # advance all lines 1 hour
    get_lines_needing_decision() → List[str]
    harvest_product(line_id, stage) → Dict[str, float]
    allocate_inventory(from_species, amount_kg, to_line_id)

class SharedInventory:
    additives: Dict[str, float]
    products: Dict[str, float]
    oxygen_store_kg: float
    energy_consumed_kWh: float
    withdraw(species, amount_kg) → float
    deposit(species, amount_kg)
```

### Game UI layout

- **Refinery grid** (3×5): Mini furnace cards showing status badge, campaign label, temperature bar, progress indicator
- **Shared inventory panel**: Current stocks of Na, K, Mg, Ca, C, O₂, and all products
- **Detail view** (click to expand a line): Full Plotly charts for that line, parameter controls, harvest buttons
- **Decision queue**: Alert notifications when any line reaches a decision point
- **Energy dashboard**: Total kW draw, per-line breakdown, solar concentrator utilization

Multi-line update strategy: HTMX polling (`hx-trigger="every 2s"`) for the grid overview, SocketIO for the expanded line detail view. Game auto-selects AlphaMELTS as engine (no engine picker in game mode).

**Verify**: Start game with 3 lines on different feedstocks. Watch autonomous progression. Decision alert appears. Make choice. Shared inventory updates when products are harvested.

---

## Phase 5: Data Persistence + Custom Compositions + Run History

**Goal**: Save/load custom feedstocks and test run histories to YAML.

### `simulator/persistence.py`

```python
class RunHistory:
    save_run(batch: BatchRecord) → str  # returns batch_id
    load_run(batch_id) → BatchRecord
    list_runs() → List[Dict]  # summary: id, feedstock, date, total yields
    delete_run(batch_id)

class CustomCompositions:
    save_composition(key, label, composition_wt_pct, notes)
    load_all() → Dict
    delete_composition(key)
```

### Web routes for data management
- `GET /api/runs` — list saved runs
- `GET /api/runs/<id>` — load a run
- `POST /api/compositions` — save custom composition
- `GET /api/compositions` — list custom + built-in
- HTMX partials for composition editor form and run history browser

**Verify**: Complete a simulation, save to YAML, reload from run history, see same results. Create custom composition, select it in feedstock dropdown, run simulation.

---

## Phase 6: Polish, Tests, Documentation

1. **Error handling**: Backend unavailability, MELTS timeout, NaN compositions, mass balance violations
2. **Mass balance checks**: After every hour, verify conservation (warn if >0.1% discrepancy)
3. **Unit tests** (`tests/`):
   - `test_core.py` — data structures, snapshot serialization
   - `test_mass_balance.py` — conservation laws
   - `test_campaigns.py` — campaign transitions, endpoint detection
   - `test_hertz_knudsen.py` — evaporation calc against known values
   - `test_condensation.py` — species routing
   - `test_energy.py` — kWh calculations
   - `test_melt_backend.py` — mock + integration with real MELTS
4. **Engine documentation pass** (Tier 2 modules):
   - Review and refine inline comments in `condensation.py`, `overhead.py`, `electrolysis.py`, evaporation code
   - Each module gets a header docstring explaining: the physical model, key equations with references, assumptions made, units
   - Each function documents: what physical quantity it computes, the equation used, parameter meanings, return value interpretation
   - Add equation reference tags (e.g., `[HK-1]` for Hertz-Knudsen, `[NERNST-1]`, `[LAPPLE-1]`) so the docs can cite specific implementations
5. **Standard documentation**:
   - `docs/README.md` — setup, installation, usage
   - `docs/ARCHITECTURE.md` — system diagram
   - `docs/CHEMISTRY.md` — Oxygen Shuttle process for developers
   - `docs/MELTS_SETUP.md` — AlphaMELTS and FactSAGE configuration
   - `docs/ENGINES.md` — the evaporation, condensation, vortex, and electrolysis physics models: what equations we use, what assumptions we make, how to verify against literature
6. **UI polish**: Responsive layout, consistent Plotly colors per species, CSV export for batch data, PNG/SVG chart export

---

## Dependency Chain

```
Phase 0 (skeleton + data)
    ↓
Phase 1 (core.py + data structures + simulation loop with stubs)
    ↓
Phase 2 (AlphaMELTS + VapoRock integration)  ──→  Phase 5 (persistence, parallel)
    ↓
Phase 3 (simulator web UI with charts)
    ↓
Phase 4 (game mode multi-line)
    ↓
Phase 6 (polish + tests + docs)
```

## Thermal Model Simplification

The simulator does **not** model heat input/output for the solar concentrator in detail. The approach:
- Solar flux is assumed to maintain the melt at whatever target temperature the campaign requires (the concentrator control system abstracts this)
- `equipment.py` estimates a reasonable concentrator aperture based on: `power_kW = batch_mass × c_p(melt) × dT/dt_peak + σ×ε×A×T⁴` (radiative losses dominate at high T)
- Scaling reference from context: 100 m² aperture → ~136 kW → appropriate for ~1 tonne batch
- For larger batches (100 tonnes), either multiple concentrators or a single large array
- Endothermic reaction energy (oxide reduction, evaporation enthalpy) is not tracked as a heat input requirement — the concentrator just handles it
- The energy we **do** track in `energy.py` is the **electrical** energy: turbine compression, condenser active cooling (where needed), and MRE electrolysis

---

## Risk Items

1. **AlphaMELTS Python API availability**: PetThermoTools wraps alphaMELTS for Python but docs are sparse. Fallback: subprocess mode (write .melts → run binary → parse output). Slower (~1–3s per call) but reliable.
2. **VapoRock maturity**: Relatively new. If integration proves difficult, fall back to Antoine parameters from `vapor_pressures.yaml` combined with MELTS activity coefficients.
3. **HTMX + SocketIO protocol mismatch**: Solved by hybrid approach — SocketIO JS client for real-time push, HTMX for everything non-real-time.
4. **MELTS doesn't natively output vapor pressures**: Solved by combining MELTS activities with VapoRock or pure-component vapor pressure data: `P_i_sat = activity_i × P_pure_i(T)`.
5. **Thread safety in game mode**: SharedInventory needs locks. Flask-SocketIO green threads + `threading.Lock()` suffices.

## Verification Plan

After each phase, run the verification checkpoint described above. End-to-end test after Phase 3:
1. Start `python app.py`
2. Open `localhost:3000`
3. Select "Lunar Mare (High-Ti)" feedstock, 1000 kg batch
4. Select AlphaMELTS engine
5. Click Start — watch hour-by-hour charts update
6. Reach C1→C2 decision point — choose Path A
7. Run to completion
8. Check mass balance panel: kg in ≈ kg out (within 0.1%)
9. Check energy panel: kWh per campaign, total
10. Expand disclosure triangles for each campaign's assumptions
11. Review final outputs: metals, O₂, glass, slag with impurity fractions

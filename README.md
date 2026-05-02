# Regolith Pyrolysis Simulator

Interactive simulator for a solar-thermal regolith refinery that uses controlled overhead oxygen pressure to make vacuum pyrolysis and MRE workflows more practical in space.

The core idea is simple: regolith pyrolysis is attractive because sunlight can provide most of the heat, but hard-vacuum pyrolysis can turn silica into SiO vapor. SiO boiloff can foul ducts, condensers, windows, turbines, and product streams. A small managed oxygen backpressure can suppress SiO formation while still allowing useful volatile and metal extraction. The same pretreatment can also condition melts before molten regolith electrolysis (MRE), reducing volatile load, alkali load, iron load, and corrosive offgas exposure.

This package explores that control problem. It compares hard vacuum, Mars backpressure, pO2-managed pyrolysis, alkali shuttle chemistry, selective Mg extraction, and MRE-like electrolysis as parts of one scalable refining ladder.

It is a process-modeling workbench, not a plant certification tool.

## Why This Exists

Most regolith oxygen discussions jump straight to MRE or carbothermal reduction. Those processes are useful, but they are electrically intensive and force the whole melt inventory through electrodes, cells, and corrosion-limited hardware.

This simulator asks a different question:

> How much useful refining can be done with solar-concentrator heat and pressure control before spending large amounts of electrical power?

The project demonstrates three linked ideas:

- Overhead pO2 is a process control variable, not just an atmosphere label.
- SiO boiloff can be suppressed or redirected by pressure management instead of accepted as a hard-vacuum mess.
- Regolith pyrolysis can be used as MRE pretreatment, producing useful material streams while making later electrolysis less hostile.

## What It Models

The simulator tracks a staged refinery path for one-tonne-class feedstock batches:

- Stage 0 bakeoff: water, CO2, sulfur, halides, CHNOPS, perchlorates, and other volatiles.
- Pressure-managed pyrolysis: Na, K, Fe, Mg, SiO, and oxygen-bearing vapor behavior under hard vacuum, CO2 backpressure, N2 sweep, or pO2 control.
- SiO suppression: pO2 shifts the SiO2 -> SiO + 1/2 O2 equilibrium and reduces the driving force for silica boiloff.
- Gas train behavior: overhead pressure, pipe conductance, turbine load, venting, accumulator flow, and ramp throttling.
- Condensation train products: staged collection of metals, SiO/silica, alkalis, oxygen, salts, and volatile streams.
- MRE comparison: limited or baseline molten regolith electrolysis after thermal pretreatment.
- Shuttle and thermite steps: Na/K metallothermic cleanup and Mg thermite reduction for aluminum-rich residues.

The result is a live process dashboard rather than a static calculator: temperature, pressure, evaporation flux, product inventory, oxygen budget, and mass balance evolve through the run.

## Feedstock Scope

The point is not one idealized lunar basalt. The simulator is meant to compare what different space feedstocks make possible.

### Lunar Feedstocks

Lunar mare and highland materials show the baseline tradeoff: oxygen and iron are accessible, SiO boiloff must be managed, Mg and Al need later stages, and glass or slag composition matters for construction products and MRE cell life.

### Asteroid Feedstocks

Asteroid cases highlight that the "regolith refinery" is not always an oxygen plant first. M-type material can be an Fe-Ni-Co alloy source with silicate byproduct. S-type and C-type material change volatile, sulfur, metal, and magnesium opportunities. The simulator is structured to show which streams are products and which become gas-handling problems.

### Mars Feedstocks

Mars is not hard vacuum. Mars basalt, sulfate-rich soils, phyllosilicates, and perchlorate-bearing material run with a CO2 pressure floor. That changes Stage 0, SiO suppression, sulfur/chlorine handling, salt traps, scrubbers, CO/CO2 behavior, and pump requirements. Mars backpressure is therefore modeled as part of the process, not as an afterthought.

## Materials the Model Tries to Expose

Depending on feedstock and route, the simulator tracks or estimates:

- O2 from pyrolysis, MRE, and accumulator flow.
- Fe and Fe-rich alloy products.
- Si, SiO, silica, and glass-forming residues.
- Na and K as volatile products or shuttle reagents.
- Mg from selective pyrolysis.
- Ti, Cr, Mn, Ca, and Al-bearing products where process conditions allow.
- Water, CO2, sulfur species, halide salts, chlorine/fluorine scrubber loads, and other volatile hazards.
- Residual glass, slag, or refractory concentrate for construction or further refining.

The goal is to make the materials ledger visible: not only "how much oxygen," but what else is produced, preserved, lost, or made dangerous.

## Interfaces

The web app has two entry points:

- `http://localhost:3000/` — detailed simulator with feedstock selection, additives, charts, pressure feedback, product inventory, and process decisions.
- `http://localhost:3000/lunar-operator` — operator-style mode for multi-line refinery management.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then open `http://localhost:3000/`.

## Installed Dependencies

`pip install -r requirements.txt` installs:

- `flask` — web server and templates.
- `flask-socketio` — live simulation updates.
- `pyyaml` — feedstock, setpoint, and vapor-pressure data files.
- `plotly` — browser-side charts.
- `numpy` — numerical helpers.
- `scipy` — scientific calculations.

Optional extras:

- `pytest` via `pip install -e ".[dev]"` for tests.
- `PetThermoTools` via `pip install -e ".[melts]"` for MELTS-backed thermodynamic work.

## Melt Chemistry Backends

The simulator can run without an external melt chemistry package. In that mode it uses the built-in Ellingham/Antoine fallback for comparative vapor-pressure estimates.

Currently compatible backend paths:

- `PetThermoTools` — preferred Python API path for alphaMELTS-family calculations when installed.
- `VapoRock` — optional vapor-pressure backend if the Python package is importable.
- `alphaMELTS` binary — subprocess fallback. The app first checks the project-local path `engines/alphamelts/run_alphamelts.command`, then checks for `alphamelts` on `PATH`.
- `FactSAGE/ChemApp` — stubbed integration point only; requires a commercial license and implementation work before use.

For a local alphaMELTS install, put the executable here:

```text
engines/alphamelts/run_alphamelts.command
```

## Model Status

The simulator includes a fallback Ellingham/Antoine thermodynamic model and can be extended with external melt backends. Results should be read as comparative process estimates, not validated engineering predictions.

Mars feedstocks with `surface_pressure_mbar` run Stage 0/C0 against a CO2 pressure floor instead of hard vacuum. Airless feedstocks still use hard vacuum unless a campaign sets a managed atmosphere.

Useful questions for the current model:

- How much does overhead pO2 suppress SiO boiloff?
- When is pyrolysis a useful MRE pretreatment rather than a competing process?
- Which feedstocks produce useful Fe, Mg, glass, alkalis, salts, sulfur streams, or oxygen?
- Where do volatile streams become hardware or safety constraints?
- How does Mars CO2 backpressure change the first stage of refining?

## Development

```bash
pip install -e ".[dev]"
pytest
```

## Source Layout

- `simulator/state.py` — shared constants, enums, and dataclasses.
- `simulator/core.py` — `PyrolysisSimulator` lifecycle, orchestration, and snapshots.
- `simulator/equilibrium.py` — fallback Ellingham/Antoine equilibrium model.
- `simulator/evaporation.py` — Hertz-Knudsen evaporation, condensation routing, and melt mass updates.
- `simulator/extraction.py` — MRE, alkali-shuttle, and Mg-thermite campaign helpers.
- `web/static/js/simulator-*.js` — browser-side simulator code split by socket setup, charts, tick updates, decisions, and controls.

# Regolith Pyrolysis Simulator

Interactive simulator for a solar-thermal regolith refinery that models controlled overhead oxygen pressure to make vacuum pyrolysis and molten regolith electrolysis (MRE) workflows more practical for extracting metals, glass and ceramics from sample regolith feedstock profiles for the Moon, Mars, and asteriods.

The core idea is simple: regolith pyrolysis is attractive because sunlight can provide most of the heat, but hard-vacuum pyrolysis can turn silica into SiO vapor. SiO boiloff can foul ducts, condensers, windows, turbines, and product streams. A small managed oxygen backpressure can suppress SiO formation while still allowing useful volatile and metal extraction. The same pretreatment can also condition melts before molten regolith electrolysis (MRE), reducing volatile load, alkali load, iron load, and corrosive offgas exposure to make MRE more practical and economical.

This package explores that control problem. It compares hard vacuum, Mars backpressure, pO2-managed pyrolysis, alkali shuttle chemistry, selective Mg extraction, and MRE-like electrolysis as parts of one scalable refining ladder.

It is a process-modeling workbench, which computes molten-reolith pyrolysis workflows using alphaMELTS, PetThermoTools, and Ellingham diagrams.

Author: Simon Rowland, simon@simonrowland.com.

## Why This Exists

The process model uses geologist-standard melt libraries to demonstrates five linked ideas:

- Alkali metals, extracted early and re-injected, are used to shuttle oxygen out of the melt, conditioning the melt for easier processing.
- Overhead pO2 is a key Ellingham process control variable, working alongside temparature and pressure to offer targeted extraction.
- SiO boiloff (noted in recent literature) can be suppressed or redirected by pressure management instead of accepted as a hard-vacuum mess.
- Regolith pyrolysis can be used as MRE pretreatment, producing useful material streams while making later electrolysis less hostile.
- Mg thermite-style reduction can further process terminal ceramics, for example to enrich REE in terminal products.

Current In-Situ Resource Utilisation (ISRU) literature is focused on MRE, hydrogen reduction, halide reduction, or carbothermal reduction. Those processes are useful, but MRE in particular is electrically-intensive and forces the whole melt inventory through electrodes and corrosion-limited hardware. By using pyrolysis to extract alkalis and iron beforehand to condition the melt for MRE, it is possible to save energy and signficantly reduce corrosion.

This simulator seeks to answer the question: How much useful refining can be done with solar-concentrator heat by using overhead pressure control, before spending large amounts of electrical power? We end-to-end workflows that can extract most of the useful metal content of the sample feedstocks, supporting reoglith pyrolisys as a core, self-bootstrapping path for metals, glass, ceramics, and oxygen production in space.

## What It Models

The simulator tracks a staged refinery path for one-tonne-class feedstock batches:

- Stage 0 bakeoff: water, CO2, sulfur, halides, CHNOPS, perchlorates, and other volatiles.
- Pressure-managed pyrolysis: Na, K, Fe, Mg, SiO, and oxygen-bearing vapor behavior under hard vacuum, CO2 backpressure, N2 sweep, or pO2 control.
- Na/K oxygen shuttle loop produces a proportion of fully-reduced metals that can be tapped directly, while improving glass as an intermediate product.
- SiO suppression: pO2 shifts the SiO2 -> SiO + 1/2 O2 equilibrium and reduces the driving force for silica boiloff.
- Gas train behavior: overhead pressure, pipe conductance, turbine load, venting, accumulator flow, and ramp throttling.
- Condensation train products: staged collection of metals, SiO/silica, alkalis, oxygen, salts, and volatile streams.
- Glass: Production of various grades of glass and a range of useful ceramics, including premium REE-enriched terminal ceramics.
- MRE comparison: limited or baseline molten regolith electrolysis after pyrolysis pretreatment.
- Final thermite steps: pyrolysis-extracted Mg can be used to further reduce the refractory ceramic remaining after prior pyrolysis or electrolysis.

The result is a live process dashboard rather than a static calculator: temperature, pressure, evaporation flux, product inventory, oxygen budget, and mass balance evolve through the run.

## Feedstock Scope

While lunar basalt is central to ISRU literature, the simulator is generalised over the characterised small-body, Moon and Mars feedstock types.

### Lunar Feedstocks

Lunar mare and highland materials show the baseline tradeoff: oxygen and iron are accessible, SiO boiloff must be managed, Mg and Al need later stages, and glass or slag composition matters for construction products and MRE cell life.

### Asteroid Feedstocks

Asteroid cases cover the range of feedstock types, including S-tyle feedstock very similar to lunar regolith. M-type material can be an Fe-Ni-Co alloy source with silicate byproduct. C-type material change volatile, sulfur, metal, and magnesium opportunities. The simulator is structured to integrate volatile processing, and to extract sulphates and phosphates in C-type feedstocks early to converge the later pipeline stages into a pure basalt problem.

### Mars Feedstocks

Mars feedstocks benefit from additional reduction as a melt conditioning step. Mars basalt, sulfate-rich soils, phyllosilicates, and perchlorate-bearing material run with a CO2 pressure floor. That changes Stage 0, SiO suppression, sulfur/chlorine handling, salt traps, scrubbers, CO/CO2 behavior, and pump requirements. Mars backpressure is therefore modeled as part of the process, not as an afterthought.

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

From a source checkout, run the dependency installer:

```bash
python3 install-dependencies.py
```

It uses `uv` when available and falls back to `pip` automatically. If you are
not already inside a virtual environment, it creates `.venv` and installs
`requirements.txt` there. Then run the command it prints and open
`http://localhost:3000/`.

The launcher defaults to `127.0.0.1:3000` with Flask debug mode off. For local
development only, override with `REGOLITH_HOST`, `REGOLITH_PORT`, or
`REGOLITH_FLASK_DEBUG=1`; debug mode is rejected unless the host is loopback.
For a public or shared-hosting deployment, run the Flask app through the host's
WSGI/server integration rather than exposing the development server.

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
- `FactSAGE/ChemApp` — optional ChemApp-backed adapter when a licensed local install and `FACTSAGE_CONFIG` mapping to a user-exported `.cst`/`.dat` file are available.

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

## Public Docs

- [Architecture](docs/architecture.md)
- [Process Model](docs/process-model.md)
- [Feedstocks](docs/feedstocks.md)
- [Melt Backends](docs/melt-backends.md)
- [Model Limitations](docs/model-limitations.md)
- [Developer Map](docs/developer-map.md)

## Source Layout

- `simulator/state.py` — shared constants, enums, and dataclasses.
- `simulator/core.py` — `PyrolysisSimulator` lifecycle, orchestration, and snapshots.
- `simulator/equilibrium.py` — fallback Ellingham/Antoine equilibrium model.
- `simulator/evaporation.py` — Hertz-Knudsen evaporation, condensation routing, and melt mass updates.
- `simulator/extraction.py` — MRE, alkali-shuttle, and Mg-thermite campaign helpers.
- `web/static/js/simulator-*.js` — browser-side simulator code split by socket setup, charts, tick updates, decisions, and controls.

# Developer Map

This guide is for contributors and coding agents that need to find the right file quickly.

## Entry Points

- `app.py` starts the Flask and Socket.IO server.
- `web/templates/simulator.html` defines the main simulator page.
- `web/static/js/simulator-socket.js` creates the Socket.IO client.
- `web/static/js/simulator-controls.js` handles user controls and feedstock/additive requests.
- `web/static/js/simulator-ticks.js` updates live charts and status panels.

## Simulator Engine

- `simulator/core.py` owns `PyrolysisSimulator`, batch lifecycle, campaign transitions, decisions, and snapshots.
- `simulator/state.py` owns constants, enums, and dataclasses.
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

## Data

- `data/feedstocks.yaml` is the main feedstock library.
- `data/setpoints.yaml` contains process setpoints and campaign metadata.
- `data/vapor_pressures.yaml` contains vapor-pressure data.
- `data/custom_compositions.yaml` is a local extension point.

## Testing

Focused tests live under `tests/`. Current smoke coverage checks Stage 0 atmosphere behavior for hard-vacuum and Mars-backpressure feedstocks.


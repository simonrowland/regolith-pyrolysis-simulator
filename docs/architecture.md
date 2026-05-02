# Architecture

The simulator is a local Flask application with a Python simulation engine and a browser dashboard. It is designed as a process workbench rather than a packaged web service: data lives in YAML files, the simulation runs in memory per browser session, and the UI streams hour-by-hour updates over Socket.IO.

## Runtime Shape

```text
Browser UI
  |
  | Socket.IO events
  v
Flask + Flask-SocketIO
  |
  | creates and drives
  v
PyrolysisSimulator
  |
  +-- campaign sequencing
  +-- equilibrium and vapor pressure estimates
  +-- evaporation and condensation routing
  +-- MRE, alkali shuttle, and thermite helpers
  +-- overhead gas and turbine feedback
  +-- energy and mass-balance tracking
```

## Main Components

- `app.py` creates the Flask app, registers the simulator and operator-mode routes, and starts Socket.IO.
- `web/routes.py` serves the simulator page, feedstock metadata, setpoints, additive estimates, and disclosure-panel partials.
- `web/events.py` owns live simulator sessions keyed by Socket.IO client ID.
- `simulator/core.py` owns the `PyrolysisSimulator` lifecycle: loading batches, starting campaigns, advancing hourly steps, handling decisions, and producing snapshots.
- `simulator/state.py` defines shared constants, enums, and dataclasses used across the engine.
- `simulator/equilibrium.py`, `simulator/evaporation.py`, and `simulator/extraction.py` split the engine behavior into readable mixins.
- `simulator/campaigns.py` defines campaign atmosphere, ramp, endpoint, and transition logic.
- `simulator/overhead.py`, `simulator/condensation.py`, `simulator/energy.py`, `simulator/electrolysis.py`, and `simulator/equipment.py` model subsystems.
- `web/static/js/simulator-*.js` renders charts, status panels, decisions, controls, and live updates in the browser.

## Data Files

- `data/feedstocks.yaml` defines built-in lunar, asteroid, and Mars feedstock compositions and environment metadata.
- `data/setpoints.yaml` defines campaign setpoints, control assumptions, condensation train details, and operating notes.
- `data/vapor_pressures.yaml` defines pure-component vapor-pressure data used by the fallback model.
- `data/custom_compositions.yaml` and `data/test_runs.yaml` are local mutable placeholders.

## Session Model

Each browser connection gets an independent in-memory simulator. The backend runs a background loop that calls `sim.step()`, emits a `simulation_tick`, and pauses when operator decisions are required. This keeps the current application simple and suitable for local exploration, but it is not yet a multi-user production service.


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

- `regolith-pyrolysis-run.py` starts the local web server.
- `app.py` creates the Flask app, registers the simulator routes, and wires Socket.IO.
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

## Chemistry kernel routing

The simulator's chemistry plane is centralized in the `ChemistryKernel` facade at `simulator/chemistry/kernel/`. Builtin chemistry operations are implemented as `ChemistryProvider` classes under `engines/builtin/`; fourteen are registered from `PyrolysisSimulator._BUILTIN_PROVIDER_REGISTRATIONS`, and the authoritative builtin vapor-pressure provider is wired separately alongside its VapoRock diagnostic shadow. The kernel's `commit_batch(intent, proposal)` method is the sole authorized writer to `AtomLedger`; no `simulator/*.py` module mutates the ledger directly. Every proposal passes three validation gates inside `commit_batch` before any debit or credit lands: intent authority (the registered provider must own the intent), account scope (every account on either side must appear in the provider's `CapabilityProfile.declared_accounts`), and atom balance. The atom gate compares each element with an absolute tolerance of `1e-6 mol` and a relative tolerance of `1e-9`, using the larger tolerance at the element's debit/credit scale. The provider receives a pre-filtered `ProviderAccountView` containing only its declared accounts, so account-scope leaks are caught both at the read side (the filter) and the write side (the commit gate). Provider-to-intent mapping is documented in `docs/developer-map.md` under "Engine Source Trees".

## Session Model

Each browser connection gets an independent in-memory simulator. The backend runs a background loop that calls `sim.step()`, emits a `simulation_tick`, and pauses when operator decisions are required. This keeps the current application simple and suitable for local exploration, but it is not yet a multi-user production service.

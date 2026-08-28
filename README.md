# Regolith Pyrolysis Simulator

Interactive simulator for a solar-thermal regolith refinery that models controlled overhead oxygen pressure to make vacuum pyrolysis and molten regolith electrolysis (MRE) workflows more practical for extracting metals, glass and ceramics from sample regolith feedstock profiles for the Moon, Mars, and asteroids.

The core idea is simple: regolith pyrolysis is attractive because sunlight can provide most of the heat, but hard-vacuum pyrolysis can turn silica into SiO vapor. SiO boiloff can foul ducts, condensers, windows, turbines, and product streams. A small managed oxygen backpressure can suppress SiO formation while still allowing useful volatile and metal extraction. The same pretreatment can also condition melts before molten regolith electrolysis (MRE), reducing volatile load, alkali load, iron load, and corrosive offgas exposure to make MRE more practical and economical.

This package explores that control problem. It compares hard vacuum, Mars backpressure, pO2-managed pyrolysis, alkali shuttle chemistry, selective Mg extraction, and MRE-like electrolysis as parts of one scalable refining ladder.

It is a process-modeling workbench. It computes molten-regolith pyrolysis workflows from a
built-in Ellingham/Antoine model, and can route silicate melt equilibria to external
thermochemistry engines — alphaMELTS (directly or via PetThermoTools), ThermoEngine/VapoRock,
and MAGEMin — when those are installed. See [Melt Chemistry Backends](#melt-chemistry-backends)
for which engine holds which role.

Author: Simon Rowland, simon@simonrowland.com.

## Why This Exists

The process model uses geologist-standard melt libraries to explore five linked ideas:

- Na, extracted early or dosed externally, can strip residual FeO in a narrow cool-window cleanup step, conditioning the melt for easier processing.
- Overhead pO2 is a key Ellingham process control variable, working alongside temperature and pressure to offer targeted extraction.
- SiO boiloff (noted in recent literature) can be suppressed or redirected by pressure management instead of accepted as a hard-vacuum mess.
- Regolith pyrolysis can be used as MRE pretreatment, producing useful material streams while making later electrolysis less hostile.
- Mg thermite-style reduction can further process terminal ceramics, for example to enrich REE in terminal products.

Current In-Situ Resource Utilisation (ISRU) literature is focused on MRE, hydrogen reduction, halide reduction, or carbothermal reduction. Those processes are useful, but MRE in particular is electrically-intensive and forces the whole melt inventory through electrodes and corrosion-limited hardware. By using pyrolysis to extract alkalis and iron beforehand to condition the melt for MRE, it may be possible to save energy and significantly reduce corrosion.

This simulator seeks to answer the question: how much useful refining can be done with solar-concentrator heat by using overhead pressure control, before spending large amounts of electrical power? It models end-to-end workflows aimed at extracting most of the useful metal content of the sample feedstocks, in order to test regolith pyrolysis as a core, self-bootstrapping path for metals, glass, ceramics, and oxygen production in space. Whether the modelled workflows would behave that way in hardware is not something this simulator can settle — see [Model Status](#model-status).

## What It Models

The simulator tracks a staged refinery path for one-tonne-class feedstock batches:

- Stage 0 bakeoff: water, CO2, sulfur, halides, CHNOPS, perchlorates, and other volatiles, with diagnostic foulant-disposition reporting by hour and group before melt-equilibrium backends run.
- Pressure-managed pyrolysis: Na, K, Fe, Mg, SiO, and oxygen-bearing vapor behavior under hard vacuum, CO2 backpressure, N2 sweep, or pO2 control.
- Na-dominated oxygen shuttle chemistry produces a proportion of reduced Fe that can be tapped directly in the cool FeO window, while K remains primarily a volatile product or recyclable alkali stock.
- SiO suppression: pO2 shifts the SiO2 -> SiO + 1/2 O2 equilibrium and reduces the driving force for silica boiloff.
- Gas train behavior: overhead pressure, pipe conductance, turbine load, venting, accumulator flow, and ramp throttling.
- Condensation train products: staged collection of metals, SiO/silica, alkalis, oxygen, salts, and volatile streams.
- Glass and ceramics: grades of glass and a classified ceramics menu from the residue, including REE-enriched terminal ceramics. The taxonomies and their grading policies live in `data/glass_types.yaml` and `data/ceramics_taxonomy.yaml`.
- MRE comparison: limited or baseline molten regolith electrolysis after pyrolysis pretreatment.
- Final thermite steps: pyrolysis-extracted Mg can be used to further reduce the refractory ceramic remaining after prior pyrolysis or electrolysis.

The result is a live process dashboard rather than a static calculator: temperature, pressure, evaporation flux, product inventory, oxygen budget, and mass balance evolve through the run.

## Feedstock Scope

While lunar basalt is central to ISRU literature, the simulator is generalised over the characterised small-body, Moon and Mars feedstock types. The catalog is `data/feedstocks.yaml`; the composition contract each profile has to satisfy is [`docs/feedstocks.md`](docs/feedstocks.md).

### Lunar Feedstocks

Lunar mare and highland materials show the baseline tradeoff: oxygen and iron are accessible, SiO boiloff must be managed, Mg and Al need later stages, and glass or slag composition matters for construction products and MRE cell life.

### Asteroid Feedstocks

Asteroid cases cover the range of feedstock types, including S-type feedstock very similar to lunar regolith. M-type material can be an Fe-Ni-Co alloy source with silicate byproduct. C-type material changes the volatile, sulfur, metal, and magnesium opportunities. The simulator is structured to integrate volatile processing, track sulfate and foulant disposition, and retain phosphorus as phosphate rather than treating it as an early volatile cleanup product.

### Mars Feedstocks

Mars feedstocks benefit from additional reduction as a melt conditioning step. Mars basalt, sulfate-rich soils, phyllosilicates, and perchlorate-bearing material run with a CO2 pressure floor. That changes Stage 0, SiO suppression, sulfur/chlorine handling, salt traps, scrubbers, CO/CO2 behavior, and pump requirements. Mars backpressure is therefore modeled as part of the process, not as an afterthought.

Carbon-rich profiles, including CI/CM chondrites, Ceres, cometary material, and Mars cases, carry literature-converged carbon speciation with interval-bounded provenance rather than treating carbon as a single undifferentiated feedstock term.

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

## What An Operator Can Ask It

Each question below has a surface you can drive today. Where the surface is partial, that is said
here rather than left for you to discover.

**"Moon rock in, ingots out — what does one batch actually produce?"**
`python -m simulator.three_product_runner --feedstock <id> --campaign C2A --hours N` reports the
four product classes of `CLAUDE.md` §5 — metals + O2, pure silica glass, industrial mixed glass,
refractory ceramic rump — alongside an `ingots_metals` / `oxygen` / `glass` / `captured_volatiles`
breakdown. It also reports `unclassified`, which is the honesty account: mass the classifier could
not assign. A growing `unclassified` is a finding about the classifier, not a product.

**"How high can yield go if the furnace is only rated to 1300 °C?"**
`python -m simulator.optimize --constrained-max` switches the study to yield-under-ceilings mode,
where wall coating becomes a furnace-lifespan cost rather than a hard gate. Pair it with the
ceiling you actually have: `--furnace-temp-cap-C`, `--cycle-time-cap-h`, and `--pin DOTTED.PATH`
to freeze a knob the search may not move. These are optimizer gates over the modelled process,
not a hardware qualification.

**"Do we need MRE at all?"**
This is the default rather than a special mode: `c5_enabled` is `False` throughout, so electrolysis
is opt-in and the no-MRE run is the baseline. The web optimizer's **MRE catalog** field
(`mre_preset_id`) selects which species set a study may use, so the comparison is a setting rather
than a rebuild.

**"When is pyrolysis a useful MRE pretreatment rather than a competing process?"**
The same lever, read the other way: run the `mre_baseline` track against a pyrolysis-then-MRE run
on the same feedstock and compare the ledgers. `--track {pyrolysis,mre_baseline}` selects which,
and both write the same output schema, so the two documents are directly comparable.

**"Which feedstocks produce useful Fe, Mg, glass, alkalis, salts, sulfur streams, or oxygen?"**
`data/feedstocks.yaml` carries 29 keys — 19 modelled in-situ compositions and 10 terrestrial
simulants. The simulants carry a `class` key and a `provenance` block naming the XRF composition
citation, because they exist to reproduce lab experiments against the material actually used in
them — they are not stand-ins for in-situ regolith. Sweeping the catalog is a matter of looping the
batch runner; nothing in the runner is feedstock-specific.

**"Can I trade recipe knobs against product grade?"**
Every run emits `stage_purity_report`, the per-stage designated / coproduct / impurity split with a
PURE / MIXED / CONTAMINATED verdict, so a selectivity claim is checkable per condenser stage. The
grading policies live in `data/glass_types.yaml` and `data/ceramics_taxonomy.yaml`. The §5
early-tap option — stop before the SiO release window and tap a mixed industrial glass — is
`--early-tap` on the three-product runner.

**"Could a crude furnace build a better one?"**
Partly. `sintered_regolith` is a selectable liner in `data/furnace_materials.yaml`, consumed by the
campaign, liner-life, and thermal-budget models, so you can run the process under a crude furnace's
temperature ceiling and see what it costs. The refractory rump is reported every run. **Chaining
one generation's rump into the next generation's liner is not a modelled loop** — the bootstrap
chain has to be walked by hand, one run at a time.

**"How much does overhead pO2 suppress SiO boiloff?"**
`python -m simulator.runner.sio_tsweep` and `sio_yield` drive the temperature/ramp grids directly;
two worked sweeps are checked in under `docs/sio_tsweep_*.md`.

**"How does Mars CO2 backpressure change the first stage?"**
Mars feedstocks carrying `surface_pressure_mbar` run Stage 0 against a CO2 pressure floor instead
of hard vacuum. Airless feedstocks stay on hard vacuum unless a campaign sets a managed atmosphere.

**"Where do volatile streams become hardware or safety constraints?"**
Per-hour `wall_deposit_delta_kg` / `wall_deposit_cumulative_kg` nest as `{segment: {species: kg}}`,
so a deposit is attributable to the pipe segment that caught it; `condensation_refusals_by_species`
records what the deposition model refused to route; and Stage 0 reports foulant disposition by hour
and group. Both wall maps are empty on a run that evolves nothing — a cold C0 batch reports `{}`,
which is an absence of flux, not an absence of instrumentation.

## Interfaces

The web app:

- `http://localhost:3000/` — detailed simulator with feedstock selection, additives, charts, pressure feedback, product inventory, and process decisions.

The simulator is also scriptable from the shell, with no web server — useful for batch runs,
reproducible experiments, CI, and cluster work. These run under the project venv
(`./.venv/bin/python`) from the repo root:

| command | what it does |
|---|---|
| `python -m simulator.runner` | deterministic batch run; writes one JSON result document to `--output`. `--output` is always required, and `--feedstock` is required unless a `--preset` supplies one. |
| `python -m simulator session` | non-interactive `SimSession` script harness; emits one NDJSON frame per command. |
| `python -m simulator.optimize` | recipe optimizer study over a feedstock; `--feedstock`, `--fidelity` and `--budget` are required. |
| `python -m simulator {run,session}` | the same runner/session surfaces behind one entry point. |

The batch runner deliberately puts nothing useful on stdout — the JSON document is the artifact.
A failed run is not silent, but you have to look in the right place: it exits non-zero and writes
the failure into that same document as `status: "failed"` with an `error_message`, rather than
printing to the terminal.

Details, flags, and worked examples: [`docs/running-from-shell.md`](docs/running-from-shell.md),
[`docs/optimizer-user-guide.md`](docs/optimizer-user-guide.md),
[`docs/session-script-protocol.md`](docs/session-script-protocol.md), and the output contract in
[`docs/runner-output-schema.md`](docs/runner-output-schema.md).

Model-bearing citations are tracked in [`docs/references/`](docs/references/): a stable `REF-NNN` registry with DOI/authors, verified pull-quotes where available, generated HTML pages, validation, and an automatic `cited_by` index. Regenerate and validate with:

```bash
./.venv/bin/python docs/references/build_references.py            # regenerate HTML
./.venv/bin/python docs/references/build_references.py --check    # validate only; exits non-zero on error
```

## Quick Start

From a source checkout, run the dependency installer:

```bash
python3 install-dependencies.py
```

It uses `uv` when available and falls back to `pip` automatically. If you are
not already inside a virtual environment, it creates `.venv` and installs
`requirements.txt` there. It also downloads alphaMELTS 2.3.1 into
`engines/alphamelts/`. Then run the command it prints and open
`http://localhost:3000/`.

The remaining thermochemistry engines — ThermoEngine, VapoRock, and MAGEMin — are native
builds that `pip` cannot compile from a requirements line, so they have a separate
provisioning script:

```bash
python3 install-engines.py
```

The simulator runs without them, on the built-in analytical model. See
[Melt Chemistry Backends](#melt-chemistry-backends) for what that costs you.

The launcher defaults to `127.0.0.1:3000` with Flask debug mode off. For local
development only, override with `REGOLITH_HOST`, `REGOLITH_PORT`, or
`REGOLITH_FLASK_DEBUG=1`; debug mode is rejected unless the host is loopback.
The old `REGOLITH_ALLOW_UNSAFE_WERKZEUG` escape hatch is rejected outright — setting it raises
rather than being ignored. Incoming `Host` headers are checked against a pinned authority list of
`localhost` and `127.0.0.1`; `REGOLITH_ALLOWED_HOSTNAMES` (comma-separated) is the only way to add
to it, and without it a LAN client fails that check with no recourse. For a public
or shared-hosting deployment, run the Flask app through the host's WSGI/server
integration rather than exposing the development server.

## Installed Dependencies

`pip install -r requirements.txt` installs:

- `flask` — web server and templates.
- `flask-socketio` — live simulation updates.
- `pyyaml` — feedstock, setpoint, and vapor-pressure data files.
- `plotly` — browser-side charts.
- `numpy` — numerical helpers. Pinned `<2`: VapoRock imports `np.bool8`, removed in NumPy 2.0.
- `scipy` — scientific calculations.
- `petthermotools` — the MELTS-family melt-equilibria path. Not optional: without it the
  documented installer flow cannot stand up the silicate-equilibrium chain.
- `pytest`, `pytest-xdist`, `pytest-timeout` — required by the `addopts` in `pyproject.toml`;
  without them `pytest` aborts during argument parsing before any test runs.

Optional extras declared in `pyproject.toml` (`pip install -e ".[<name>]"`):

| extra | contents |
|---|---|
| `dev` | `pytest`, `pytest-xdist`, `pytest-timeout` |
| `optimize` | `optuna` (pinned) — the recipe-optimizer strategies |
| `sulfur` | `pysulfsat` — sulfur saturation models |
| `magemin` | marker only; the MAGEMin binary itself comes from `install-engines.py` |

There is no `melts` extra. PetThermoTools is a core dependency (above), and the alphaMELTS
binary is installed by `install-dependencies.py`.

## Melt Chemistry Backends

The simulator runs without any external melt chemistry package. In that mode it uses the
built-in Ellingham/Antoine model for comparative vapor-pressure estimates.

**Selectable as the active melt backend** by the batch runner's `--backend`:

| name | notes |
|---|---|
| `internal-analytical` | the built-in Ellingham/Antoine model. The default. |
| `alphamelts` | alphaMELTS, via subprocess or the PetThermoTools Python API. |
| `thermoengine` | ThermoEngine; delegates vapor-melt work to VapoRock when VapoRock is importable, and warns and falls back to activity × Antoine rows when it is not. |

`stub` and `diagnostic_stub` are accepted input aliases for `internal-analytical`. They are
folded at every name-keyed boundary, so a run started with either spelling records
`internal-analytical` in its metadata and shares that cache identity. `internal-analytical` is
the only spelling that is ever serialized; the `--help` text advertises only that spelling.

`cached-real` — replaying cached real-engine results — is a fourth backend, but it is **not**
reachable from the runner CLI (`--backend cached-real` is an argparse error). It is reached
through the optimizer's `fast` fidelity tier. By contract it serves cached *real*-engine results
only, and never analytical output dressed as real-engine evidence.

The web UI's engine picker is deliberately narrower: `Auto (AlphaMELTS preferred)`,
`AlphaMELTS (strict)`, and `Built-in analytical`. `auto` is accepted by that web autodetect path
but **rejected** under runner-strict — a batch run must name the engine it wants, so that what
produced a stored artifact is never ambiguous.

**Not selectable as the active backend**, by design:

- `VapoRock` and `MAGEMin` are per-intent providers and diagnostic evidence sources, not active
  melt backends. They are listed in `simulator/backends.py::INELIGIBLE_ACTIVE_BACKENDS`.
- `FactSAGE/ChemApp` — archived/removed adapter. Not selectable in the web UI or runner; an
  explicit `factsage` request raises `BackendUnavailableError: unknown backend 'factsage'`.

**What a backend is allowed to do.** Installing a real engine does not move ledger authority to
it. Silicate liquidus and freeze-path providers, alphaMELTS included, are **diagnostic only** and
may not mutate the ledger; vapor-pressure runtime authority stays with the built-in model until an
explicit source-selection change. The per-intent authority table is in `AGENTS.md`
§"Chemistry-engine policy", with the operator-facing version in
[`docs/melt-backends.md`](docs/melt-backends.md).

**Where the alphaMELTS binary is looked for**, in order:

1. `alphamelts_binary_path` in `engines/engines.local.toml` (machine-local, gitignored).
2. `engines/alphamelts/run_alphamelts.command`.
3. `engines/alphamelts/` and one level of its subdirectories, for `alphamelts2`,
   `alphamelts_macos`, `alphamelts_linux`, or `alphamelts_win64.exe`.
4. `alphamelts` on `PATH`.

`python3 install-dependencies.py` populates (2)/(3) for you.

## Model Status

The simulator includes a built-in Ellingham/Antoine thermodynamic model and can be extended with
external melt backends. **Results should be read as comparative process estimates, not validated
engineering predictions.**

[`docs/model-limitations.md`](docs/model-limitations.md) is the honest accounting, and it is the
document to read before quoting any number out of this simulator. It carries the per-domain error
budget, the known model disagreements, and the reproduction battery: the engine is scored against
published experimental measurements, and **most of the corpus does not score**. The current
measurement is 570 in-scope observations, of which 67 are comparable, producing 105 comparable
residual points and 532 explicit gap records. The gaps are not hidden — every skipped observation
carries a typed refusal reason (missing melt-activity coefficient, not-comparable condensed form,
no numeric rate in the source, and so on), and those reasons are tabulated in that document.
Regenerate the numbers rather than trusting this paragraph:

```python
from simulator.diagnostic_helpers.extract_reproduction import evaluate_all, coverage_summary
coverage_summary(evaluate_all())
```

Two literature stores back that, and the split is load-bearing:

- `data/literature/extracts/` — **experimental measurements**. The engine is validated *against*
  these. They produce the scoring rows.
- `data/literature/compilations/` — **assessed thermodynamic functions** (NIST-JANAF and
  friends). The engine *consumes* these as reference data, so they produce no scoring rows;
  they refuse as `gibbs_table_not_runtime_observable`. Scoring the engine against a table it
  already reads would be circular. See `data/literature/compilations/README.md`.

## Development

```bash
pip install -e ".[dev]"
python3 -m pytest tests/ -v
```

`pyproject.toml` sets `addopts = -n auto --dist loadgroup --no-loadscope-reorder --timeout=300`.
Those defaults are load-bearing, not cosmetic. The staged-freeze mass-balance case spawns the real
MAGEMin binary for a bracket-and-bisect liquidus search that takes minutes; lowering `--timeout`
below ~200 s clips a legitimate run and pytest-timeout fires SIGALRM inside a subprocess poll,
which the xdist gateway cannot recover from. It looks like a hang or a silent die, not a timeout.
Do not pass a lower `--timeout` unless you also mean to skip that test (`-k 'not freeze'`).

## Public Docs

**Start here**

- [Getting Started](docs/getting-started.md)
- [FAQ](docs/faq.md) · [Glossary](docs/glossary.md) · [Concepts](docs/concepts.md)

**What the model does, and what it does not**

- [Model Limitations](docs/model-limitations.md) — error budget, known disagreements, reproduction battery
- [Process Model](docs/process-model.md) · [Chemistry Methods](docs/chemistry-methods.md)
- [Feedstocks](docs/feedstocks.md)
- [Citation Policy](docs/citation-policy.md) · [Lab Validation Whitepaper](docs/lab-validation-whitepaper.md)
- [IMCC SF-04 Spec](docs/imcc-sf04-spec.md) · [MD Target List](docs/md-target-list.md)

**Running it**

- [Running From The Shell](docs/running-from-shell.md) · [Runner Output Schema](docs/runner-output-schema.md)
- [Optimizer User Guide](docs/optimizer-user-guide.md) · [Recipe Playbook](docs/recipe-playbook.md)
- [Session Script Protocol](docs/session-script-protocol.md) · [Output Interpretation](docs/output-interpretation.md)

**Internals and engines**

- [Architecture](docs/architecture.md) — interactive Flask + Socket.IO plane
- [Eval-Runtime Architecture](docs/architecture-eval-runtime-2026-06.md) — batch eval, warm workers, caches
- [Developer Map](docs/developer-map.md) · [Melt Backends](docs/melt-backends.md)
- [ThermoEngine Patches](docs/thermoengine-patches.md) · [CI/Local Divergence](docs/ci-local-divergence.md) · [Performance Ratchet](docs/performance-ratchet.md)

**Reference registry and campaign analyses**

- [`docs/references/`](docs/references/) — the `REF-NNN` registry, source PDFs, and generated pages
- SiO temperature sweeps: [lunar mare, low-Ti](docs/sio_tsweep_lunar_mare_low_ti_2026-05-19.md) · [Mars basalt](docs/sio_tsweep_mars_basalt_2026-05-19.md)

## Source Layout

Modules:

- `simulator/state.py` — shared constants, enums, and dataclasses.
- `simulator/core.py` — `PyrolysisSimulator` lifecycle, orchestration, and snapshots.
- `simulator/equilibrium.py` — built-in Ellingham/Antoine equilibrium model.
- `simulator/evaporation.py` — Hertz-Knudsen evaporation and melt mass updates.
- `simulator/condensation.py` — the staged condensation-train model.
- `simulator/condensation_routing.py` — canonical condenser-stage and extraction-product routing.
- `simulator/extraction.py` — MRE, alkali-shuttle, and Mg-thermite campaign helpers.
- `simulator/thermal_train.py` — downstream thermal-train sizing diagnostics (radiators, cold-end
  cycle, oxygen liquefaction). Deliberately **detached**: it takes recorded flow rates at its
  boundary, never reads or writes the atom ledger, and never takes a simulator-run object. It
  sizes the train that would carry a run's output; it is not part of the run. Parameters live in
  `data/thermal_train_params.yaml`, and the web view is `thermal_train`.

Packages:

- `simulator/accounting/` — the mol-native atom ledger. Kg numbers are external projections only;
  internal state is mols, and every transition is conservation-checked before commit.
- `simulator/chemistry/` — chemistry provider kernel and engine subpackages.
- `simulator/melt_backend/` — melt-backend abstraction layer (alphaMELTS, ThermoEngine, MAGEMin, VapoRock, SulfSat).
- `simulator/runner/` — the deterministic batch CLI harness.
- `simulator/optimize/` — recipe optimizer: CLI, evaluation, studies, worker pool, fidelity tiers, results store.
- `simulator/vapour_rail/` — vapour-rail manifest and typed NASA-CEA polynomial evaluators.
- `simulator/diagnostic_helpers/` — diagnostic-only analysis surfaces. These must not register
  chemistry providers or mutate the atom ledger.

Web and data:

- `web/routes.py`, `web/events.py` — Flask routes and Socket.IO handlers.
- `web/static/js/simulator-*.js` — browser-side code split by socket setup, charts, tick updates, decisions, advisories, and controls.
- `data/*.yaml` — feedstocks, setpoints, vapor pressures, materials, taxonomies, thermal-train parameters.
- `data/literature/` — the literature corpus: `extracts/` (measurements, validated against) and `compilations/` (assessed functions, consumed).

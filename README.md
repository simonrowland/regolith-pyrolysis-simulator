# Regolith Pyrolysis Simulator

Interactive simulator for a solar-thermal regolith refinery that models controlled overhead oxygen pressure to make vacuum pyrolysis and molten regolith electrolysis (MRE) workflows more practical for extracting metals, glass and ceramics from sample regolith feedstock profiles for the Moon, Mars, and asteroids.

The core idea is simple: regolith pyrolysis is attractive because sunlight can provide most of the heat, but hard-vacuum pyrolysis can turn silica into SiO vapor. SiO boiloff can foul ducts, condensers, windows, turbines, and product streams. A small managed oxygen backpressure can suppress SiO formation while still allowing useful volatile and metal extraction. The same pretreatment can also condition melts before molten regolith electrolysis (MRE), reducing volatile load, alkali load, iron load, and corrosive offgas exposure to make MRE more practical and economical.

This package explores that control problem. It compares hard vacuum, Mars backpressure, pO2-managed pyrolysis, alkali shuttle chemistry, selective Mg extraction, and MRE-like electrolysis as parts of one scalable refining ladder.

It is a process-modeling workbench. It computes molten-regolith pyrolysis workflows from a
built-in Ellingham/Antoine model, and can route silicate melt equilibria to external
thermochemistry engines — alphaMELTS (directly or via PetThermoTools), ThermoEngine/VapoRock,
MAGEMin, and our sister project [OpenIMCC](https://github.com/simonrowland/openimcc) for
high-temperature melt activities — when those are installed. See [Melt Chemistry Backends](#melt-chemistry-backends)
for which engine holds which role.

Author: Simon Rowland, simon@directleap.com.

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
The classification-only helper is useful for a quick product-bucket check, but the canonical batch
runner in [Hero run](#hero-run-1-kg-lunar-mare-to-2200-c) also records the backend, furnace endpoint,
wall deposit, and audited mass disposition. It reports `unclassified`, which is the honesty account:
mass the classifier could not assign. A growing `unclassified` is a finding about the classifier,
not a product.

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
PURE / MIXED / CONTAMINATED / INDETERMINATE verdict, so a selectivity claim is checkable per condenser stage. Empty stages are INDETERMINATE, not PURE. The
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
two historical sweeps from an earlier release are checked in under `docs/sio_tsweep_*.md`.

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

### Hero run: 1 kg lunar mare to 2200 °C

This is the smallest end-to-end demonstration of the product story. The command uses the default
`furnace_material: zirconia_ysz` hot-wall configuration (catalog ceiling 2200 °C), runs the
continuous `C2A` campaign long enough to reach that ceiling, and writes the full report to
`hero-run.json`:

```bash
python3 -m simulator.runner \
  --feedstock lunar_mare_low_ti \
  --campaign C2A \
  --hours 225 \
  --mass-kg 1 \
  --backend internal-analytical \
  --include-wall-deposit-rate-diagnostics \
  --output hero-run.json
```

This uses a default recipe.

The furnace reached 2200 °C at hour 213 and held there through hour 225. The largest per-element
mass-closure residual is 1.5e-15, against a limit of 5e-14.

**What leaves the melt.** This is the fraction of each element's feedstock atoms that evaporates out of
the melt by the end of the run:

| element | leaves the melt | stays in the melt |
|---|---:|---:|
| Fe † | 1.000 | 0.000 |
| Na † | 1.000 | 0.000 |
| Si † | 0.999 | 0.001 |
| K † | 0.935 | 0.065 |
| Cr † | 0.256 | 0.744 |
| Mg † | 0.194 | 0.806 |
| Ca | 0.001 | 0.999 |
| Ti | 0.001 | 0.999 |
| Al | 0.000 | 1.000 |

† Na, K, Mg, Fe, Si and Cr carry a flagged hour-to-hour flux artefact while carrier-gas feed rates
are being added to the model, so treat their fractions as indicative. CrO evaporation is not yet
modelled, which further understates Cr.

**Refractory terminal ceramic (the rump).** This is a product, not a loss: 0.348 kg of the 1 kg charge.
It is the `refractory_ceramic_rump` class in `product_classification`. Its composition is Al₂O₃
39.8 wt%, CaO 32.4, MgO 21.4, TiO₂ 4.4, Cr₂O₃ 0.8, MnO 0.5 and P₂O₅ 0.3, with traces including the REEs.
This Ca, Al, Ti and REE-rich ceramic is the material intended to line the next furnace.

**Oxygen.** 0.165 kg of O₂ is released from the melt, counted at the source. Recovering it as
LOX is part of the condenser-train work.

**Where the vapour goes.** Yields here are counted at the melt boundary while the condenser-train
model is being built. The run report already carries the per-stage ledger under `yield_disposition`
and `stage_purity_report`, and the hard-vacuum Mg/Ca crown stage, which pulls most of the remaining
Mg and Ca, is next on the roadmap.

The run's vapour-pressure report covers 32 species. At these temperatures, 30 of them are evaluated
beyond their fitted range, so the high-temperature fractions are extrapolations.

### Install

From a source checkout, run the dependency installer:

```bash
python3 install-dependencies.py
```

It uses `uv` when available and falls back to `pip` automatically. If you are
not already inside a virtual environment, it creates `.venv` and installs
`requirements.txt` there. It also downloads the locked alphaMELTS 2.3.1 app
into `~/repos/engine-bin/alphaMELTS/<archive-sha>/`. Then run the command it prints and open
`http://localhost:3000/`.

Engine provisioning is a second installation stage: PetThermoTools, Thermobar,
PySulfSat and VapoRock come from pinned editable clones; ThermoEngine and
MAGEMin require native builds. All engines live in the host-wide
`~/repos/engine-bin/<engine>/<pin>/` layout shared by checkouts. Run the
provisioning script after base setup:

```bash
python3 install-engines.py
python3 install-engines.py --check
```

`--check` is read-only: it reports lock drift, extra engine builds under
`~/repos`, the local TOML path/digest drift, and each host artifact SHA-256.
Native artifact SHA differences are informational because the committed source
pin is authoritative. To bump a source, edit and review `engines/engines.lock.toml`;
`--update` has been removed.

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

`install-dependencies.py` installs the Python stack from `requirements.txt`: Flask and
Flask-SocketIO (web UI), PyYAML, Plotly, NumPy (pinned `<2`, because VapoRock needs `np.bool8`),
SciPy, pandas, psutil, threadpoolctl, optuna (recipe optimizer), OpenIMCC (high-temperature melt
activities), and the pytest plugins the test configuration requires. It also installs the locked alphaMELTS app.

`install-engines.py` builds the pinned chemistry engines: MAGEMin, PySulfSat, ThermoEngine,
VapoRock, PetThermoTools and Thermobar. Run it after the base install. A later exact `uv sync`
removes them, so provision the engines again after any sync.

The one optional extra is `e2e` (browser tests): `pip install -e ".[e2e]"`.

## Melt Chemistry Backends

The simulator runs out of the box on its built-in Ellingham/Antoine model. External engines add
real melt thermodynamics where they are installed.

| engine | role |
|---|---|
| `internal-analytical` | Built-in Ellingham/Antoine model. The default active backend. |
| `alphamelts` | Active backend, through a subprocess or the PetThermoTools API. |
| `thermoengine` | Active backend. Uses VapoRock for vapour–melt work when it is installed. |
| [OpenIMCC](https://github.com/simonrowland/openimcc) | Sister project. Supplies melt activities above 1700 K for the eight major melt oxides (Si, Mg, Fe, Ca, Al, Ti, Na, K) through a 45-species associate model, plus Mn in its redox pack, whichever active backend is selected. Its gas database reaches well beyond the melt oxides, and melt activities for more elements are on the way. Installed by default. If it refuses a composition, the run uses the built-in activity table for that step and records a notice. |
| VapoRock, MAGEMin | Per-task providers and cross-checks. Not selectable as the active backend. |

Choose the active backend with `--backend` on the batch runner; batch runs always name one, so every
stored result says which engine produced it. The web UI offers Auto (alphaMELTS preferred),
alphaMELTS and Built-in analytical.

Installing an engine does not hand it the ledger. Liquidus and freeze-path providers are
diagnostic, and vapour-pressure authority stays with the built-in model. See
[`docs/melt-backends.md`](docs/melt-backends.md) for the per-task authority table.

`install-dependencies.py` downloads the locked alphaMELTS app, and `install-engines.py` builds the
pinned source engines. Our [engine patches](patches/README.md) are applied on top with
`patches/scripts/enginepatch.sh apply <engine>`.

## Model Status

The simulator includes a built-in Ellingham/Antoine thermodynamic model and can be extended with
external melt backends. **Results should be read as comparative process estimates, not validated
engineering predictions.**

[`docs/model-limitations.md`](docs/model-limitations.md) is the document to read before quoting a
number from the simulator: it carries the per-domain error budget and the known model
disagreements. The validation battery holds 186,644 observations. 101,709 are experimental
measurements from the published literature, and each engine is scored against them rail by rail;
[`docs/validation-battery.md`](docs/validation-battery.md) explains the scoring and how to read it.
Use its [leaderboard](docs/validation-battery.md#leaderboard) to page through best and worst
predictions by feedstock, engine, rail, and species.
The other 84,935 sit in 969 compilation shards of assessed reference tables, which the engines
read as data. An observation an engine cannot yet score carries a
typed reason, never a silent gap. The extract-reproduction rollup is generated into
[`docs/validation-battery-rollup.md`](docs/validation-battery-rollup.md); regenerate it rather than
quoting fixed numbers:

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

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Public Docs

**Start here**

- [Getting Started](docs/getting-started.md)
- [FAQ](docs/faq.md) · [Glossary](docs/glossary.md) · [Concepts](docs/concepts.md)

**What the model does, and what it does not**

- [Model Limitations](docs/model-limitations.md) — error budget, known disagreements, reproduction battery
- [Validation Battery](docs/validation-battery.md) — how each engine is scored against published measurements, rail by rail
- [Process Model](docs/process-model.md) · [Chemistry Methods](docs/chemistry-methods.md)
- [Feedstocks](docs/feedstocks.md)
- [Citation Policy](docs/citation-policy.md)
- IMCC melt-activity model: [openimcc](https://github.com/simonrowland/openimcc) · [Validation Battery Rollup](docs/validation-battery-rollup.md)

**Running it**

- [Running From The Shell](docs/running-from-shell.md) · [Runner Output Schema](docs/runner-output-schema.md)
- [Optimizer User Guide](docs/optimizer-user-guide.md) · [Recipe Playbook](docs/recipe-playbook.md)
- [Session Script Protocol](docs/session-script-protocol.md) · [Output Interpretation](docs/output-interpretation.md)

**Internals and engines**

- [Architecture](docs/architecture.md) — interactive Flask + Socket.IO plane
- [Eval-Runtime Architecture](docs/architecture-eval-runtime.md) — batch eval, warm workers, caches
- [Developer Map](docs/developer-map.md) · [Melt Backends](docs/melt-backends.md)
- [Engine Patches](patches/README.md) — our fixes to the third-party engines, pending upstream adoption
- [ThermoEngine Patches](docs/thermoengine-patches.md) · [Performance Ratchet](docs/performance-ratchet.md)

**Reference registry and campaign analyses**

- [`docs/references/`](docs/references/) — the `REF-NNN` reference registry and generated bibliography pages

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

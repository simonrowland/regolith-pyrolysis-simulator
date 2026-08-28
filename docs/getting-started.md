# Getting Started

This tutorial walks through a first run of the simulator using both the web UI and the CLI, starting from a fresh checkout.

## What you will do

Run the canonical full-yield pyrolysis recipe on a 1-tonne
`lunar_mare_low_ti` batch, observe the per-hour product ledger, and locate the
JSON output for downstream analysis.

## Prerequisites

- Python 3.12 (the simulator and its tests are developed against 3.12; 3.13+ may encounter compatibility issues with optional engine packages)
- `git`, `uv` or `pip` available on `PATH`
- For the full thermochemical engine stack on macOS arm64: Xcode command-line tools (`xcode-select --install`) and [Homebrew](https://brew.sh) — the installer uses `brew` to fetch `nlopt`, `open-mpi`, and `gsl` before compiling MAGEMin and ThermoEngine from source

In the current operational chain, builtin Antoine + Ellingham is the authoritative `VAPOR_PRESSURE` provider, VapoRock is a diagnostic-only shadow, PetThermoTools/ThermoEngine handles `SILICATE_EQUILIBRIUM`, and MAGEMin shadows or narrowly gates `SILICATE_LIQUIDUS` and `GATE_LIQUID_FRACTION`. Without the optional compiled engines installed, the simulator boots but is limited to comparative exploration, not validated melt chemistry. See [`docs/melt-backends.md`](melt-backends.md).

## Install

From a source checkout, run the dependency installer:

```bash
python3 install-dependencies.py
```

This creates `.venv` (if you are not already inside a virtual environment), installs `requirements.txt`, and prints the command to activate the environment. Follow the printed instruction, then confirm the app runs:

```bash
python3 regolith-pyrolysis-run.py
```

The launcher binds to `127.0.0.1:3000` by default. Override with `REGOLITH_HOST`, `REGOLITH_PORT`, or `REGOLITH_FLASK_DEBUG=1` for local development only.

### Engines (macOS arm64)

```bash
python3 install-engines.py
```

This clones and installs PetThermoTools, Thermobar, PySulfSat, and VapoRock as editable siblings of the repo, then compiles MAGEMin and ThermoEngine from source. The alphaMELTS binary is not bundled; see `engines/alphamelts/` for placement notes. The installer is idempotent: existing builds are reused.

Only PetThermoTools is declared in `[project.dependencies]`, so that one comes in with `pip install -e .`. **VapoRock is deliberately *not* a pip dependency**: `import vaporock` hard-requires the native ThermoEngine build (a Cython extension linking system GSL and Objective-C/C dylibs), which pip cannot compile. VapoRock and ThermoEngine are provisioned only by `install-engines.py`, as are MAGEMin and the remaining editable-sibling clones.

Skip the native compiles (editable sibling clones only — PetThermoTools, Thermobar, PySulfSat, VapoRock — with no MAGEMin / ThermoEngine build):

```bash
python3 install-engines.py --skip-compiles
```

Note that with `--skip-compiles` the VapoRock clone is installed but will still fail to import, because ThermoEngine was not built. Use `python3 install-engines.py --update` to pull the engine clones to their latest upstream commit and rebuild the natives.

With the operational chain installed, the active backend selection log line (see `docs/melt-backends.md`) confirms the selected `MeltBackend`; `run_metadata.engines_used.registry.vapor_pressure` shows `authoritative: "builtin-vapor-pressure"` with VapoRock, when importable, under `shadows`. Silent fallback to backend vapor pressures is forbidden unless `chemistry_kernel.allow_fallback_vapor: true` is set in setpoints.

## First run via the web UI

Start the server:

```bash
python3 regolith-pyrolysis-run.py
```

Open `http://127.0.0.1:3000/` in a browser.

1. Select feedstock `Lunar Mare Basalt (Low-Ti)` from the feedstock panel.
2. Leave the campaign at the default (`C0 — Vacuum Bakeoff`).
3. Click **Start**. The hourly panel begins streaming.

Things to look for during the run:

- **Campaign sequence**: the simulator advances through C0/C0b, staged Path A,
  the cool Na shuttle where the Mg driver engages, C4, and a final continuous
  C2A boiloff to the alumina ceiling. C5/MRE is absent.
- **SiO flux peak in the 1400–1600 °C window**: under the default Path A pN₂ sweep (C2A_continuous), SiO co-evolves with Fe. The `condensation_train_kg` column shows SiO glass accumulating in Stage 3. Under pO₂ control (C2B), SiO flux is suppressed >300× at 1 mbar pO₂.
- **Mass balance closure**: the `mass_balance_pct` field should remain below `5e-12 %` at every tick. A nonzero drift indicates a regression; see `tests/test_mass_balance.py`.
- **Stage purity** (in the runner JSON output's `stage_purity_report`): each stage carries a `verdict` (`PURE` / `MIXED` / `CONTAMINATED`) and a per-species kg breakdown of designated vs impurity material. Stage 3 should be `PURE` SiO under default Path A.
- **Shuttle refusals** (in `shuttle_refusal_history`): empty list means every C3 step the engine accepted; entries name the campaign, hour, melt T, and thermodynamic margin. Under V1c-JANAF a stray `--additive=K=…` will be quietly ignored by the gate (this is the surviving design, not a bug); see [`docs/recipe-playbook.md`](recipe-playbook.md) for the policy.


## First run via the CLI

The `simulator.runner` module provides a non-interactive batch path:

```bash
python3 -m simulator.runner \
    --feedstock=lunar_mare_low_ti \
    --campaign=C0 \
    --hours=400 \
    --recipe=data/recipes/canonical_lunar_full_yield.yaml \
    --output=runs/canonical_lunar_full_yield.json
```

The runner writes a JSON document to `--output` (creating the parent directory if needed) and exits `0` on success. A failed run still writes well-formed JSON with `"status": "failed"` so pipelines do not need to parse stderr.

Key CLI flags:

| Flag | Default | Notes |
|---|---|---|
| `--feedstock` | required | ID from `data/feedstocks.yaml` |
| `--output` | required | Path for JSON output |
| `--campaign` | `C0` | Starting campaign phase |
| `--hours` | `24` | Simulated hours to run |
| `--mass-kg` | `1000` | Batch mass in kg |
| `--backend` | `internal-analytical` | `internal-analytical` (legacy alias `stub`), `alphamelts`, or `thermoengine`; `auto` only on web/API autodetect |
| `--track` | `pyrolysis` | `pyrolysis` or `mre_baseline` |
| `--additive` | none | Repeatable: `--additive=Na=12`. Post-V1c the C3 shuttle gate uses Na only; `--additive=K=…` is accepted but ignored by the gate. |

For field definitions in the output JSON, see [`docs/runner-output-schema.md`](runner-output-schema.md).

### Session script interface

For interactive-style batch runs with explicit operator decisions, use the session harness. The repository ships **no** default script directory — `--script` takes whatever path you hand it, so write your own:

```bash
python3 -m simulator session \
    --script=my_lunar_session.txt
```

Use `--script=-` to read from stdin, which is the quickest way to try it:

```bash
printf 'start --feedstock lunar_mare_low_ti --campaign C2A\nadvance\nsnapshot\nquit\n' \
  | python3 -m simulator session --script -
```

See [`docs/session-script-protocol.md`](session-script-protocol.md) for the full script grammar and frame format.

## What to do next

- **Understand the process model**: [`docs/concepts.md`](concepts.md) explains the three control levers, the four product classes, and the physical basis for the extraction sequence.
- **Design a recipe**: [`docs/recipe-playbook.md`](recipe-playbook.md) covers the campaign catalog, operator decision points, and worked example workflows.
- **Read run outputs**: [`docs/output-interpretation.md`](output-interpretation.md) explains the per-hour summary fields, the final-state ledger, and what to plot.
- **Know the limits**: [`docs/model-limitations.md`](model-limitations.md) lists what the simulator does not model and what results cannot be claimed from it.

# Contributing to the Regolith Pyrolysis Simulator

The Regolith Pyrolysis Simulator is an open research workbench for
solar-thermal regolith refining. Contributions are welcome: bug reports,
literature data, physics corrections, tests, documentation and code. This
guide covers how to propose changes so the simulator stays scientifically
defensible.

## Before you start

Read these first. They describe the model and the rules the code enforces:

- [`docs/concepts.md`](docs/concepts.md) — the process, the control levers and
  the product classes.
- [`docs/architecture.md`](docs/architecture.md) and
  [`docs/developer-map.md`](docs/developer-map.md) — where the code lives and
  how chemistry is routed through the kernel.
- [`docs/chemistry-methods.md`](docs/chemistry-methods.md) — the physics and
  each engine's authority.
- [`docs/citation-policy.md`](docs/citation-policy.md) — how values are sourced
  and cited.

The core invariants are load-bearing, and the test suite enforces them:

- The ledger is mol-native. Atoms are conserved in every transition, and mass
  balance closes to numerical precision.
- Every chemistry intent has a bounded, declared engine authority (builtin,
  AlphaMELTS, ThermoEngine, MAGEMin, VapoRock). Do not silently widen an
  engine's authority.
- No provider mutates the ledger directly. Providers return proposals, and the
  kernel commit path (`ChemistryKernel.commit_batch`) is the sole writer.
- Stage 0 output stays separate, and the O₂ outlet keeps its own bins.

## Setting up

```bash
python3 install-dependencies.py     # base environment in .venv
python3 install-engines.py          # optional: pinned chemistry engines
python3 install-engines.py --check  # read-only lock drift report
```

Python 3.12 is required. The simulator runs on its built-in analytical model
without the optional engines; see [`docs/getting-started.md`](docs/getting-started.md)
and [`docs/melt-backends.md`](docs/melt-backends.md) for details.

## Running the tests

Tests use `pytest` with `pytest-xdist` and live under `tests/`. Before you open
a pull request, run at least the invariant guards and the tests for the
modules you touched:

```bash
# Fast invariant guards.
.venv/bin/python -m pytest tests/test_artifact_guards.py tests/test_mass_balance.py -q

# The module(s) you touched.
.venv/bin/python -m pytest tests/test_<module>.py -q
```

For changes that span several modules, run the full suite
(`.venv/bin/python -m pytest -q`). It runs in parallel by default.

`tests/test_artifact_guards.py` and `tests/test_mass_balance.py` enforce the
core invariants. Do not skip or weaken them without stating the reason in your
pull request.

**Ground truth, not self-parity.** A new physics test must assert against
external reality (published data, a standards body, a physical definition),
not against the simulator's own previous output. A test that only pins today's
number to yesterday's number locks in any error already present. Keep
literature anchors in a sidecar separate from the runtime value (for example
`data/vapor_pressures.yaml::pure_component_antoine`); see
`tests/test_physics_ground_truth.py` for the pattern. If a ground-truth test
fails, investigate the physics. Do not retune a coefficient to make it pass.

Engine-sensitive golden fixtures should be regenerated on the reference
engine build (see `scripts/regenerate_runner_goldens.py`). Small differences
in those fixtures between machines with different engine builds are expected.

## Code style

- **Python**: PEP 8, plus these project rules:
  - Imports go at the top of the module unless a lazy import is required (for
    example to keep an optional engine optional). Say why in a comment.
  - Type hints on every public function in `simulator/`. They are optional on
    private helpers and test fixtures.
  - Use dataclasses for state structures (see `simulator/state.py`), and frozen
    dataclasses for anything immutable.
  - Errors are first-class. Domain errors (`UnbalancedTransitionError`,
    `BackendUnavailableError` and so on) are defined next to the module that raises
    them, and re-exported from the package `__init__.py` when other modules
    need them.
  - Do not catch `Exception` broadly. Catch the specific exception that
    matters.
  - Missing or invalid input refuses with a typed reason. Out-of-domain
    physics predicts and flags (see `docs/chemistry-methods.md`); it never
    returns a silent zero.
  - When you change non-obvious arithmetic, put the derivation in a comment:
    premise, algebra, a unit check, and a sanity check.
- **Tests** mirror the source layout: `simulator/foo.py` → `tests/test_foo.py`.
  Integration tests can live at the top level of `tests/`.
- **Web layer** (`web/`, `app.py`): Flask + Socket.IO. Routes are thin, and
  business logic lives in `simulator/`. Socket events are documented in the
  `web/events.py` module docstring.

## Where things go

| Kind of change | Destination |
|---|---|
| Simulation engine logic | `simulator/<area>.py` |
| State / dataclasses | `simulator/state.py` |
| Mol-native ledger | `simulator/accounting/` |
| Chemistry-engine adapters | `simulator/melt_backend/` (AlphaMELTS, ThermoEngine, MAGEMin, VapoRock, openIMCC bridge) |
| Builtin chemistry providers | `engines/builtin/` |
| Decision routing | `simulator/decision_tree.py` |
| Web routes / events | `web/routes.py`, `web/events.py` |
| Dashboard front-end | `web/static/` |
| Data (feedstocks, setpoints, vapour pressures, literature extracts) | `data/` |
| Documentation | `docs/` |
| Tests | `tests/test_<module>.py` |
| Engine adapters and lock | `engines/` |

[`docs/developer-map.md`](docs/developer-map.md) has the detailed file map.

## How to propose a change

1. **Open an issue** describing the change and the physics or data it rests
   on. Cite the source for any new value.
2. **Create a branch** from the default branch.
3. **For a bug**: reproduce it with a failing test first, then find and fix the
   root cause rather than patching around it.
4. **Run the tests** described above.
5. **Open a pull request.** Explain why the change is needed, what invariant it
   preserves, and anything that surprised you. Every pull request gets at least
   one review focused on chemistry and accounting correctness.

## Commits

- Keep each commit to one logical change. Write the subject line in the
  imperative mood, under 70 characters.
- Use the body for the *why*. The diff already shows the *what*.
- Do not amend or force-push commits that others may already have pulled.
  Make a new commit instead.

## Questions

If an invariant or an engine's authority is unclear, ask in the issue before
writing code. A wrong invariant baked into the code costs more than a short
discussion.

## North star

This is a process-modelling workbench, not flight hardware. Optimise for
scientific defensibility over shipping speed: every claim should be traceable
to a citation, a model or a first-principles derivation. Mass balance closes in
every transition, and every provider's authority is bounded.

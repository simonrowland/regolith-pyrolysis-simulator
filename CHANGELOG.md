# Changelog

Notable changes to the regolith-pyrolysis-simulator. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); the project is research-stage (pre-1.0),
so minor versions may carry significant changes.

## [0.1.0] — 2026-05-20

First formal tagged release. Marks completion of the **WEB-THIN-DRIVER consolidation**:
the web UI and the batch runner now drive one shared command core, and the simulator is
fully testable headlessly. Cross-surface scientific parity verified **exact**.

### Added
- `simulator/session.py::SimSession` — synchronous command core (verbs:
  `start`/`advance`/`decide`/`adjust`/`pause`/`resume`/`snapshot`) with `StepResult` +
  `DecisionPolicy`; web and batch runner both drive it.
- Headless CLI `python -m simulator`: `run` (one-shot batch → JSON result document) and
  `session --script <file|->` (newline-delimited JSON, one frame per command) — the
  browser-free operator/test surface. Protocol pinned in `docs/session-script-protocol.md` (v1.0.0).
- `simulator/backends.py` — unified `resolve_backend` + `BackendSelectionPolicy`
  (`WEB_AUTODETECT` | `RUNNER_STRICT`, no default; runner-strict rejects `auto`).
- Deterministic web socket-trace golden harness (`tests/test_web_socket_trace.py`).

### Changed
- `web/events.py` is now a thin Socket.IO adapter over `SimSession` (socket trace
  byte-identical vs the pre-refactor stream).
- `simulator/runner.py` reimplemented on `SimSession`; removed the dead
  `iter_hours()` / `simulator=` reuse seam.
- Lunar-operator nav link hidden (the operator game is a stub; route + code intact).

### Fixed
- Web `backend='stub'` now deterministically selects `StubBackend` (previously routed
  through autodetect and returned AlphaMELTS when installed).

### Validated
- 809 tests pass (+96 skipped; the 1 failing `test_artifact_guards` case is an
  environmental `rg`-not-on-PATH artifact, not a code defect).
- **Cross-surface scientific parity EXACT**: batch = CLI = web ledgers agree to `0.0 mol`
  over a full `lunar_mare_low_ti` pyrolysis run; max mass-balance error ≤ `9.6e-13 %`.

### Baseline capabilities (already on `main` before 0.1.0)
- Mol-native atom ledger; `commit_batch` is the sole transition writer (with documented
  seeding/exempt exceptions); per-intent engine authority.
- VapoRock authoritative for vapor pressure — triply validated (Wolf-2022 adapter 0.008
  dex, literature 0.08 dex, MAGEMin shadow no-divergence); MAGEMin shadow engine wired;
  AlphaMELTS diagnostic-only.
- Per-species `wall_deposit_kg` ledger + fouling-rate verdict; band-aware
  Hertz-Knudsen-Langmuir condensation law.

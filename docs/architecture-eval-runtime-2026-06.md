# Eval runtime architecture (post G9.7)

**Status:** target design — documents the optimizer / batch eval plane **after** G9.6 profiler and G9.7 persistent workers land.  
**As of:** 2026-06-05 (planned; not yet implemented).  
**Companion:** live web + interactive sim remain in [`architecture.md`](architecture.md). This doc covers **CLI optimizer, fidelity DOE, Mac Studio precompute, and Book full job** economics.

---

## Problem

High-fidelity evals spend wall time on:

1. **Process churn** — fidelity harness forks a fresh child per eval; each cold-starts ThermoEngine.
2. **Backend init** — every `evaluate()` calls `SimSession.start()` → `resolve_backend()` → Berman DB + first `MELTSmodel`.
3. **Per-equilibrate model construction** — `ThermoEngineTransport.equilibrate()` builds a new `MELTSmodel()` each call.
4. **Repeated physics** — same `(T, composition, fO₂)` along reference trajectories recomputed unless PT-1 hits.

G9.7 addresses (1–3) with **warm worker processes**. PT-1 / `results_store` / staged prefix address (4) at output granularity.

---

## Runtime shape (target)

```text
Operator / batch script (cli, run_fidelity_doe, populate_reduced_real_cache)
  |
  v
Study driver (study.py)  OR  Fidelity harness (fidelity.py)  OR  Precompute script
  |
  +-- results_store lookup (full EvalSpec hit?) --> return ScoredResult
  |
  v
evaluate_batch (pool.py)  -- ProcessPoolExecutor, W workers
  |
  |  per worker (once, after fork/spawn):
  |    pin_worker_env()
  |    WorkerEvalContext.warm(backend_name)  --> AlphaMELTSBackend + ThermoEngine transport
  |
  |  per task:
  v
evaluate(patch, ..., worker_runtime=ctx)
  |
  v
RunExecutor.execute(config, backend=ctx.backend)   # fresh executor, reused melt backend
  |
  v
SimSession.start(config, backend=ctx.backend)      # skip resolve_backend when injected
  |
  v
PyrolysisSimulator (NEW each task) + AtomLedger (NEW each task)
  |
  +-- hourly step()
  |     +-- PT-1 lookup (cached-real / live-fill) --> equilibrium payload on HIT
  |     +-- on MISS: MeltBackend.equilibrate()  --> warm transport, reused MELTSmodel (G9.7b)
  |     +-- ChemistryKernel.commit_batch(...)   --> sole ledger writer (unchanged)
  |
  v
ScoredResult  --> parent process writes results_store
```

**Web UI (`web/events.py`)** is unchanged: one in-memory sim per Socket.IO client, no process pool. Book full job (W-B) enqueues the same `evaluate_batch` / CLI path asynchronously.

---

## Four cache layers (orthogonal)

| Layer | Store | Key | Survives process? | Use case |
|-------|-------|-----|-------------------|----------|
| **A — Warm model** | Worker RAM (`WorkerEvalContext`) | Worker PID + backend_name | No | Amortize ThermoEngine init across N evals in one batch |
| **B — PT-1 equilibrium** | SQLite (`reduced_real_equilibrium_payloads`) | T, comp digest, fO₂, pressure, engine digest, … | Yes | Trajectory replay, `cached-real`, precompute grind |
| **C — Full eval** | `results_store` SQLite | Full `EvalSpec` / recipe_id | Yes | Leaderboard, identical candidate re-run |
| **D — Staged prefix** | `results_store` + prefix spec (G9.4a) | Prefix patch + stage depth | Yes | Staged beam suffix-only eval |

Canonical `EvalSpec` v1.1 is defined by `simulator/optimize/evalspec.py::EvalSpec`; the field table and `backend_fingerprints` disposition live in `docs-private/design-recipe-optimizer-2026-05-29.md` §4/§12.3.

**Trajectory replay:** prefer **B** (and **D** when staged) when states are known; use **A** when states are novel (search, cache miss fill). **C** when the entire recipe patch repeats.

---

## New module: `simulator/optimize/worker_runtime.py`

```python
@dataclass
class WorkerEvalContext:
    backend_name: str
    backend: Any          # MeltBackend instance; warmed once
    init_wall_s: float
    worker_pid: int
    engine_version: str
    transport_mode: str   # "thermoengine" | "subprocess" | "petthermo" | ...

def get_worker_runtime() -> WorkerEvalContext | None: ...
def warm_worker_runtime(backend_name: str) -> WorkerEvalContext: ...
def clear_worker_runtime() -> None: ...  # tests + rollback
```

**Rules:**

- Initialized only inside `pool._initialize_worker` (after fork/spawn, never in parent).
- Disabled when `REGOLITH_OPTIMIZER_WARM_WORKERS=0` (cold path parity / rollback).
- **Never** holds `PyrolysisSimulator`, `SimSession`, or `AtomLedger`.

---

## API injection (target signatures)

```python
# session.py
def start(self, config: SimSessionConfig, *, backend: Any | None = None) -> SimSession:
    # backend is None -> resolve_backend(...) as today
    # backend set -> skip resolve; still validate policy once at worker warm

# evaluate.py
def evaluate(..., worker_runtime: WorkerEvalContext | None = None) -> ScoredResult:
    # worker_runtime from get_worker_runtime() when called inside pool task

# run_executor.py
def execute(self, config: SimSessionConfig, *, backend: Any | None = None) -> RunExecution:
```

Pool tasks call `evaluate(...)`; `evaluate` pulls `worker_runtime` from argument or `get_worker_runtime()`.

---

## Fidelity harness (G9.7c)

**Before:** `fidelity._run_eval` → new `multiprocessing.Process` per eval → full cold start.

**After:** reuse `evaluate_batch` with `max_workers=1` (or small W), `future.result(timeout=per_eval_timeout_s)`. Parent retains timeout + abort semantics via existing pool fail-fast teardown.

---

## ThermoEngine transport (G9.7b)

**Before:** `MELTSmodel()` constructed in `initialize()` and again on every `equilibrate()`.

**After:** one `MELTSmodel` instance on `ThermoEngineTransport`; per call: `set_bulk_composition` + `equilibrate_tp`. Reset or replace model when composition basis changes discontinuously (test gate).

Reuse applies only when `AlphaMELTSBackend._mode == "thermoengine"`.

---

## Parallelism and RAM (Studio 256 GB)

**Key insight (owner, 2026-06-05):** warm workers do **not** share run state across evals. Each eval has a
different recipe / trajectory. If two runs hit the same equilibrium step, **PT-1 output cache (layer B)**
is the right tool — not a second RAM-resident model.

| Workload | Warm workers W | Why |
|----------|----------------|-----|
| Fidelity DOE, Book full job, high `@ hours:1` | **1** | Serial; one warm backend amortizes init across N evals in sequence |
| Staged high eval | **1** | Study forces serial when staged (`study.py:608`) |
| Stub / cached-real study `parallel>1` | **min(parallel, cpu)** | Embarrassingly parallel **different** evals; each worker holds one backend for **its** queue |
| PT-1 precompute grind | **shard workers → merge** | Fill B-layer on miss; warm model only on cache miss path |

**Do not** plan to fill 256 GB with many ThermoEngine instances for serial high-tier jobs. RAM headroom
is for **one (or few) warm backends + OS cache**, not duplicate models for identical state.

W>1 is a **throughput** knob for parallel fast-tier search, not a substitute for PT-1.

**Pending grill-me Q7** before G9.7 architecture sign-off. **DECIDED 2026-06-05: Q7-A.**

---

## PT-1 store hardening (G9.7e)

Parallel precompute (`populate_reduced_real_cache`) with multiple workers on merged DB:

- `PRAGMA journal_mode=WAL`
- `busy_timeout` (e.g. 30 s)
- Prefer **shard → merge** (G9.3c) over multi-writer single file

Read-heavy `cached-real` evals remain safe; write contention is the risk during grind.

---

## Invariants (unchanged from AGENTS.md)

1. **Mol-native ledger** — fresh `AtomLedger` per eval task.
2. **Mass balance** ≤5e-12% — warm path must not skip commits or reuse ledger state.
3. **Chemistry kernel authority** — AlphaMELTS/ThermoEngine diagnostic for silicate equilibrium; `commit_batch` sole writer.
4. **Fail-loud backends** — explicit `alphamelts` failure → `BackendUnavailableAbort`, not silent stub.
5. **Determinism** — warm eval must match cold eval for same `EvalSpec` (pool determinism tests extended).

---

## Verification gates (G9.7 acceptance)

| Test | Gate |
|------|------|
| `test_optimizer_pool.py` | Warm repeat ≡ cold; picklable guards unchanged |
| `test_mass_balance.py` | Feasible warm high eval closes balance |
| `scripts/profile_eval_hotpath.py` | 2nd eval in worker: init time ≈ 0 |
| `run_fidelity_doe.py` N=4 | Wall ↓ vs pre-G9.7c baseline; 0 drops |
| `REGOLITH_OPTIMIZER_WARM_WORKERS=0` | Bit-identical or within float tolerance vs warm |

---

## Relation to other docs

| Doc | Role |
|-----|------|
| [`architecture.md`](architecture.md) | Interactive Flask + Socket.IO sim (unchanged) |
| [`melt-backends.md`](melt-backends.md) | Backend selection policy + ThermoEngine/subprocess |
| [`process-model.md`](process-model.md) | Physics per hour (unchanged) |
| `docs-private/optimizer-v1-ship-checklist.md` | G9.6, G9.7 gates + worker brief |

When G9.7 ships, add a one-paragraph pointer in `architecture.md` to this document for the batch eval plane.

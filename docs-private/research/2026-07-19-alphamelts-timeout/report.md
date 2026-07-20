# AlphaMELTS live-sim timeout

Date: 2026-07-19

## TL;DR

- The 20.0 s wall is `ALPHAMELTS_DEFAULT_TIMEOUT_S` in `simulator/melt_backend/alphamelts.py`, passed to every binary `subprocess.run`.
- The reported failure was not cold boot: a 1200 C isothermal call took 0.317 s cold-ish and 0.018 s warm, while the exact C0 request still hung with a 90 s wall.
- C0 hour 1 sent the binary an isothermal equilibrium at 75 C, far below the existing 800 C liquidus-search seed.
- The adapter now returns typed `out_of_domain` below 800 C without launching or disabling AlphaMELTS; the 20 s native hang wall is unchanged.
- Default AlphaMELTS session proof: hour 1 completed at 75 C; the same session's first in-domain native solve completed `ok` at hour 16 / 825 C with no backend error or fallback.
- Successful native outputs, cache identity, byte determinism, and goldens are unchanged.

## Symptom and timeout location

The default web selection resolves `auto` to `AlphaMELTSBackend`. The strict session path then dispatches silicate equilibrium through `engines/alphamelts/provider.py` and `engines/alphamelts/subprocess_runner.py` into `_equilibrate_subprocess`.

`simulator/melt_backend/alphamelts.py` defines `ALPHAMELTS_DEFAULT_TIMEOUT_S = 20.0`. `_equilibrate_subprocess` passes that value directly to:

```python
subprocess.run([str(binary), '1'], ..., timeout=timeout_s)
```

Before the fix, the exact session harness emitted:

```text
start: backend_active=AlphaMELTSBackend
advance 1: AlphaMELTS subprocess timed out ... after 20.0 seconds
wall time: 36.97 s (including startup/import work)
```

Evidence captured during diagnosis:

- `/tmp/gf-amtimeout-session-cli.jsonl`
- `/tmp/gf-amtimeout-session-cli.stderr`
- `/tmp/gf-amtimeout-session-90.out`
- `/tmp/gf-amtimeout-session-90.stderr`

## Measurement and corrected root cause

The canonical `.venv/bin/python` and configured AlphaMELTS 2.3.1 binary were used.

| Probe | Result |
|---|---:|
| Fresh backend initialization | 10.543611 s, including a first Matplotlib font-cache build |
| First direct 1200 C / 1 bar isothermal equilibrium | 0.316609 s, `status=ok` |
| Identical second equilibrium | 0.017695 s, `status=ok` |
| Exact C0 first advance with 20 s native wall | timed out |
| Exact C0 first advance with diagnostic 90 s native wall | timed out after 90.173643 s |

Instrumentation of the terminal request showed:

```text
temperature_C=75.0
pressure_bar=1.0
fO2_log=-9.0
run_mode=ISOTHERMAL
```

The first request is driven by the C0 ramp (`20..950 C`, `50 C/hr`). AlphaMELTS's subprocess adapter had composition and pressure gates, but no operating-temperature gate. It therefore launched the native silicate equilibrium engine at a cold solid-state point far below the repository's existing 800 C liquidus-search seed. Raising the timeout or prewarming a valid 1200 C point cannot make that 75 C request converge.

The shared `simulator/engine_pool.py` does not pool this default binary route. AlphaMELTS uses `WarmEngineWorker` only for the opt-in PetThermoTools Python transport. A sacrificial web pre-solve would duplicate physics work and would not fix the demonstrated low-temperature hang.

## Fix

`simulator/melt_backend/alphamelts.py` now defines a repository operating guard at 800 C, aligned with the existing liquidus-search seed. This is a guard against the measured 75 C hang, not a claim that 800 C is a universal scientific calibration boundary. `_equilibrate_subprocess` keeps the existing pressure-contract precedence, then short-circuits lower temperatures as:

```text
status=out_of_domain
backend_status_reason=subprocess_temperature_below_minimum
backend_failure_category=out_of_domain
```

The backend remains initialized in subprocess mode. No exception banner is emitted and no runtime switch to `InternalAnalyticalBackend` occurs. Per-step session/web payloads expose `backend_status=out_of_domain`, the reason code, and `backend_authoritative=false`, so the low-temperature guard is visible rather than silently presented as an authoritative AlphaMELTS solve. At the first in-domain ramp point, the same `AlphaMELTSBackend` instance executes the native solve under the unchanged 20 s hang wall.

Regression coverage proves that 75 C never launches the binary, the typed reason is present, 800 C is inclusive, the original sub-bar pressure refusal still takes precedence, and the native timeout remains 20 s.

## Live proof

Command path:

```shell
printf '%s\n' \
  'start --feedstock lunar_mare_low_ti --campaign C0 --mass-kg 1000 --backend alphamelts --track pyrolysis' \
  'advance 1' \
  'quit' \
| .venv/bin/python -m simulator.session_cli --script - --strict
```

Observed result:

```text
start: ok=true, backend=alphamelts, backend_active=AlphaMELTSBackend
advance 1: ok=true, frame_type=step, hour=1, T_C=75.0
```

A concise 20-step harness then advanced the same configuration until the first native success:

```text
backend=AlphaMELTSBackend
transport=subprocess
first_native={hour: 16, temperature_C: 825.0, backend_status: ok,
              backend_error: ''}
elapsed_s=29.6121915
```

No timeout, error banner, backend disable, or internal-analytical fallback occurred.

## Verification

- Focused timeout/floor/live-C0 tests: `5 passed`.
- Focused per-tick status/authority/UI tests: `8 passed`; independent re-review: `READY`.
- AlphaMELTS backend/provider/selection suite: `295 passed` in 27.04 s.
- Full `tests/test_web_events.py`: `140 passed` in 139.22 s.
- A broad repository run reached `1205 passed, 103 skipped` before it was intentionally interrupted and restarted after the final UI-status edits; it is not claimed as a complete-suite pass.
- Engine-pool suite: one pre-existing/out-of-scope failure reproduces alone in `test_canceling_close_fails_in_flight_request_without_orphan` (the expected cancel exception races into its own 2 s `EngineWorkerTimeout`); `simulator/engine_pool.py` and its tests are untouched.
- The first affected-suite run found one pressure-vs-temperature precedence regression; it was corrected, and the focused precedence tests then passed 3/3.
- `git diff --check`: clean.
- Ruff was not installed in the canonical `.venv`; no lint receipt is claimed.
- Golden move: none expected or performed.
- Determinism: successful native call inputs/outputs and timeout remain unchanged; only requests that previously hung below the declared subprocess floor now return a typed non-authoritative result.

"""Pilot: fast(stub) vs high(real by default) fidelity-correlation DOE, one feedstock.

Hard pilot constraints (DO NOT relax without operator sign-off — each real high
eval is ~6+ min of ThermoEngine MELTS):
  - max_samples / N        = 4   (exactly four candidates)
  - per_eval_timeout_s     = 900
  - top_k                  = (2,)
  - parallelism            : simulator/optimize/fidelity.py uses a warm fidelity
    pool. Stub tiers use a small pool; real backend tiers serialize at W=1 so one
    warmed AlphaMELTS backend is reused across all four high evals.

Run:
  .venv/bin/python scripts/run_fidelity_doe.py

Default high backend is alphamelts so the pilot cannot silently self-parity.
Set FIDELITY_DIAGNOSTIC_STUB_HIGH=1 for the explicit stub-vs-stub diagnostic;
that run may validate zero-drop mechanics but cannot claim a trust verdict.
Per-eval wall-clock is captured via a process-safe timing shim that appends one
JSONL row per evaluate() call to TIMING_LOG.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.optimize.doe import DoeSpec
from simulator.optimize.evaluate import evaluate as _evaluate
from simulator.optimize.fidelity import run_fidelity_correlation
from simulator.optimize.recipe import RecipeSchema

PROFILE = ROOT / "data/optimize_profiles/lunar_mare_low_ti.yaml"
N = 4                       # HARD: exactly four candidates
PER_EVAL_TIMEOUT_S = 900.0  # HARD
TOP_K = (2,)                # HARD
DIAGNOSTIC_STUB_HIGH = os.environ.get("FIDELITY_DIAGNOSTIC_STUB_HIGH") == "1"
HIGH_BACKEND = os.environ.get("FIDELITY_HIGH_BACKEND", "stub" if DIAGNOSTIC_STUB_HIGH else "alphamelts")
HIGH_HOURS = int(os.environ.get("FIDELITY_HIGH_HOURS", "1"))
# /tmp (non-Dropbox) avoids a CloudStorage sync race that can zero artifact files
# mid-run; the constraint explicitly permits temp/ or /tmp.
ARTIFACT_DIR = "/tmp/fidelity_pilot/out"
TIMING_LOG = "/tmp/fidelity_pilot/eval_timings.jsonl"


def _timed_evaluate(patch, feedstock_id, fidelity, **kwargs):
    """Fork-safe wrapper: time evaluate(), append a JSONL timing row, re-raise on error.

    Runs inside the harness child process. Records tier (==fidelity name passed by
    the harness), wall-clock seconds, feasibility, and whether a populated objective
    vector came back (the make-or-break signal for whether a Spearman/top-K verdict
    is even computable on the real arm).
    """
    t0 = time.perf_counter()
    err_cls = None
    primary_error = None
    try:
        result = _evaluate(patch, feedstock_id, fidelity, **kwargs)
    except BaseException as exc:  # noqa: BLE001 - record then re-raise for harness taxonomy
        primary_error = exc
        err_cls = type(exc).__name__
        result = None
        raise
    finally:
        try:
            dt = time.perf_counter() - t0
            feasible = getattr(result, "feasible", None) if result is not None else None
            objs = getattr(result, "objectives", None) if result is not None else None
            row = {
                "tier": fidelity,
                "candidate_id": kwargs.get("candidate_id"),
                "seconds": dt,
                "feasible": feasible,
                "objectives_populated": objs is not None,
                "failure_category": (
                    getattr(getattr(result, "failure_category", None), "value", None)
                    if result is not None
                    else None
                ),
                "error_class": err_cls,
                "wall_clock_epoch": time.time(),
                "pid": os.getpid(),
            }
            with open(TIMING_LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        except BaseException as reporting_error:  # noqa: BLE001 - preserve primary taxonomy
            if primary_error is None:
                raise
            try:
                primary_error.add_note(
                    f"timing-log reporting failed: {type(reporting_error).__name__}"
                )
            except BaseException:  # noqa: BLE001 - diagnostics must remain total
                pass
    return result


def main() -> int:
    Path(TIMING_LOG).parent.mkdir(parents=True, exist_ok=True)
    # Fresh timing log each run.
    Path(TIMING_LOG).write_text("", encoding="utf-8")

    profile = yaml.safe_load(Path(PROFILE).read_text())
    feedstock = profile["feedstock"]

    if HIGH_BACKEND == "stub" and not DIAGNOSTIC_STUB_HIGH:
        raise RuntimeError(
            "FIDELITY_HIGH_BACKEND=stub requires FIDELITY_DIAGNOSTIC_STUB_HIGH=1; "
            "stub-vs-stub is diagnostic only, not a pilot trust verdict"
        )

    # Per-tier backend override: fast -> cheap deterministic stub; high defaults
    # to real MELTS, with stub diagnostic allowed only by explicit env flag.
    # Stock profile pins ALL tiers to "stub"; evaluate() reads
    # profile["fidelities"][fidelity]["backend_name"] (simulator/optimize/evaluate.py
    # _run_options). Without this override both arms would be identical stubs.
    profile = dict(profile)
    profile["fidelities"] = dict(profile["fidelities"])
    profile["fidelities"]["fast"] = {"backend_name": "stub", "hours": 1}
    profile["fidelities"]["high"] = {"backend_name": HIGH_BACKEND, "hours": HIGH_HOURS}
    print(
        f"[runner] high_backend={HIGH_BACKEND!r} high_hours={HIGH_HOURS} "
        f"diagnostic_stub_high={DIAGNOSTIC_STUB_HIGH}"
    )

    schema = RecipeSchema()
    doe = DoeSpec(schema=schema, n_samples=N, seed=0)  # sampler defaults to scipy-sobol

    # Honor the profile's study_constraints selector (same resolver as study.py).
    # Stock profiles omit the selector and default to physics constraints.
    # Set FIDELITY_SKIP_PROFILE_CONSTRAINTS=1 only to exercise evaluate() defaults.
    eval_kwargs: dict = {}
    if os.environ.get("FIDELITY_SKIP_PROFILE_CONSTRAINTS") != "1":
        from simulator.optimize.study import _constraints_for_profile

        constraints = _constraints_for_profile(profile)
        eval_kwargs["constraints"] = constraints
        print(
            f"[runner] study_constraints={profile.get('study_constraints')!r} "
            f"-> {type(constraints).__name__}"
        )
    else:
        print("[runner] FIDELITY_SKIP_PROFILE_CONSTRAINTS=1; using evaluate() default gates")

    wall0 = time.perf_counter()
    try:
        result = run_fidelity_correlation(
            doe,
            _timed_evaluate,        # SAME callable for both arms; tier string selects backend
            _timed_evaluate,
            per_eval_timeout_s=PER_EVAL_TIMEOUT_S,
            feedstock_id=feedstock,
            profile=profile,
            fast_fidelity_name="fast",   # -> stub
            high_fidelity_name="high",   # -> alphamelts (real)
            top_k=TOP_K,
            max_samples=N,
            artifact_dir=ARTIFACT_DIR,
            evaluator_kwargs=eval_kwargs or None,
        )
    except BaseException as exc:  # noqa: BLE001 - capture + STOP, no retry loop
        total = time.perf_counter() - wall0
        print("=== HARNESS CRASH ===")
        print(f"error_class: {type(exc).__name__}")
        print(f"message: {exc}")
        print(f"total_wall_clock_s: {total:.1f}")
        traceback.print_exc()
        return 1

    total = time.perf_counter() - wall0
    print("=== RESULT ===")
    print("total_wall_clock_s:", round(total, 1))
    print("fast_screen_trustworthy:", result.fast_screen_trustworthy)
    print("confidence:", result.confidence)
    print("spearman_by_objective:", dict(result.spearman_by_objective))
    print("feasible_infeasible_agreement:", result.feasible_infeasible_agreement)
    print("top_k_recall:", dict(result.top_k_recall))
    print(
        "n_compared/total/dropped:",
        result.n_samples_compared,
        result.n_samples_total,
        result.n_samples_dropped,
    )
    print("dropped_evaluations:", json.dumps([dict(d) for d in result.dropped_evaluations], indent=2))
    print("artifact_paths:", dict(result.artifact_paths))
    print("notes:", list(result.notes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

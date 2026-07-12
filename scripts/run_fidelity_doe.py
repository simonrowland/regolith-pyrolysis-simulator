"""Pilot: fast(internal-analytical) vs high(real) fidelity DOE, one feedstock.

Hard pilot constraints (DO NOT relax without operator sign-off — each real high
eval is ~6+ min of ThermoEngine MELTS):
  - max_samples / N        = 4   (exactly four candidates)
  - per_eval_timeout_s     = 900
  - top_k                  = (2,)
  - parallelism            : simulator/optimize/fidelity.py uses a warm fidelity
    pool. Internal-analytical tiers use a small pool; real backend tiers
    serialize at W=1 so one
    warmed AlphaMELTS backend is reused across all four high evals.

Run:
  .venv/bin/python scripts/run_fidelity_doe.py

Default high backend is alphamelts so the pilot cannot silently self-parity.
Set FIDELITY_DIAGNOSTIC_INTERNAL_ANALYTICAL_HIGH=1 for the explicit
internal-analytical self-comparison diagnostic;
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
from collections.abc import Mapping
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.backend_names import (
    ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
    LEGACY_ANALYTICAL_FIDELITY_DIAGNOSTIC_ENV,
    canonical_backend_name,
)
from simulator.optimize.doe import DoeSpec
from simulator.optimize.evaluate import evaluate as _evaluate
from simulator.optimize.fidelity import run_fidelity_correlation
from simulator.optimize.recipe import RecipeSchema

PROFILE = ROOT / "data/optimize_profiles/lunar_mare_low_ti.yaml"
N = 4                       # HARD: exactly four candidates
PER_EVAL_TIMEOUT_S = 900.0  # HARD
TOP_K = (2,)                # HARD


def _diagnostic_internal_analytical_high_from_env(
    environ: Mapping[str, str],
) -> bool:
    return any(
        environ.get(name) == "1"
        for name in (
            "FIDELITY_DIAGNOSTIC_INTERNAL_ANALYTICAL_HIGH",
            LEGACY_ANALYTICAL_FIDELITY_DIAGNOSTIC_ENV,
        )
    )


def _high_backend_from_env(
    environ: Mapping[str, str],
    *,
    diagnostic_internal_analytical_high: bool,
) -> str:
    default = (
        ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
        if diagnostic_internal_analytical_high
        else "alphamelts"
    )
    return canonical_backend_name(environ.get("FIDELITY_HIGH_BACKEND", default))


def _validate_high_backend_selection(
    high_backend: str,
    *,
    diagnostic_internal_analytical_high: bool,
) -> None:
    if (
        canonical_backend_name(high_backend)
        == ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
        and not diagnostic_internal_analytical_high
    ):
        raise RuntimeError(
            "FIDELITY_HIGH_BACKEND=internal-analytical requires "
            "FIDELITY_DIAGNOSTIC_INTERNAL_ANALYTICAL_HIGH=1; internal-analytical "
            "self-comparison is diagnostic only, not a pilot trust verdict"
        )


DIAGNOSTIC_INTERNAL_ANALYTICAL_HIGH = (
    _diagnostic_internal_analytical_high_from_env(os.environ)
)
HIGH_BACKEND = _high_backend_from_env(
    os.environ,
    diagnostic_internal_analytical_high=DIAGNOSTIC_INTERNAL_ANALYTICAL_HIGH,
)
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

    _validate_high_backend_selection(
        HIGH_BACKEND,
        diagnostic_internal_analytical_high=DIAGNOSTIC_INTERNAL_ANALYTICAL_HIGH,
    )

    # Per-tier backend override: fast -> deterministic internal-analytical;
    # high defaults to real MELTS, with analytical self-comparison allowed only
    # by explicit env flag. Stock profiles serialize all backend names as
    # "internal-analytical"; evaluate() reads
    # profile["fidelities"][fidelity]["backend_name"] (simulator/optimize/evaluate.py
    # _run_options). Without this override both arms would be identical
    # internal-analytical runs.
    profile = dict(profile)
    profile["fidelities"] = dict(profile["fidelities"])
    profile["fidelities"]["fast"] = {
        "backend_name": ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
        "hours": 1,
    }
    profile["fidelities"]["high"] = {"backend_name": HIGH_BACKEND, "hours": HIGH_HOURS}
    print(
        f"[runner] high_backend={HIGH_BACKEND!r} high_hours={HIGH_HOURS} "
        "diagnostic_internal_analytical_high="
        f"{DIAGNOSTIC_INTERNAL_ANALYTICAL_HIGH}"
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
            fast_fidelity_name="fast",   # -> internal-analytical
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

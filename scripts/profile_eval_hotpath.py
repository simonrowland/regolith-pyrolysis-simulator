"""Profile evaluate() hot path for G9.6.

Runs the micro-benchmark scenarios documented in
docs-private/optimizer-v1-ship-checklist.md section G9.6. The profiler is
read-only: it does not create worker runtimes, mutate ledgers, or change pool
behavior.

Example:
  .venv/bin/python scripts/profile_eval_hotpath.py
  .venv/bin/python scripts/profile_eval_hotpath.py --cprofile evaluate_alphamelts_1h
  .venv/bin/python scripts/profile_eval_hotpath.py --out docs-private/research/2026-06-07-g9.6
"""

from __future__ import annotations

import argparse
import cProfile
import json
import multiprocessing as mp
import os
import platform
import pstats
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Callable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_CACHE_ROOT = Path(os.environ.get("TMPDIR", "/tmp")) / "regolith-eval-hotpath"
os.environ.setdefault("MPLCONFIGDIR", str(_CACHE_ROOT / "matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", str(_CACHE_ROOT / "xdg-cache"))
Path(os.environ["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)
Path(os.environ["XDG_CACHE_HOME"]).mkdir(parents=True, exist_ok=True)

import yaml

from simulator.backends import BackendSelectionPolicy, resolve_backend
from simulator.melt_backend.alphamelts import AlphaMELTSBackend
from simulator.optimize.determinism import pin_worker_env
from simulator.optimize.evaluate import evaluate
from simulator.optimize.recipe import RecipePatch

DEFAULT_PROFILE = Path("data/optimize_profiles/lunar_mare_low_ti.yaml")
DEFAULT_OUT = Path("docs-private/research/2026-06-07-g9.6")
DEFAULT_REPEAT = 3
DEFAULT_HIGH_HOURS = 1
SCENARIO_ORDER = (
    "backend_init",
    "equilibrate_once",
    "evaluate_stub_1h",
    "evaluate_stub_repeat",
    "evaluate_alphamelts_1h",
    "evaluate_repeat",
    "fidelity_fork_stub",
)
SCENARIO_ALIASES = {
    "thermoengine_init_only": "backend_init",
    "fork_spawn_tax": "fidelity_fork_stub",
}
MARE_OXIDES_WT = {
    "SiO2": 44.5,
    "TiO2": 1.5,
    "Al2O3": 13.5,
    "FeO": 16.5,
    "MgO": 9.0,
    "CaO": 11.0,
    "Na2O": 0.4,
    "K2O": 0.10,
    "Cr2O3": 0.35,
    "MnO": 0.20,
    "P2O5": 0.10,
}


def _git_sha() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=REPO_ROOT,
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _resolve_repo_path(path: Path) -> Path:
    return path if path.is_absolute() else REPO_ROOT / path


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _median(samples: list[float]) -> float | None:
    if not samples:
        return None
    return statistics.median(samples)


def _elapsed_seconds(fn: Callable[[], Any]) -> float:
    t0 = time.perf_counter()
    fn()
    return time.perf_counter() - t0


def _time_samples(
    fn: Callable[[], Any],
    *,
    repeat: int,
    scale: float = 1.0,
) -> list[float]:
    samples: list[float] = []
    for _ in range(repeat):
        samples.append(_elapsed_seconds(fn) * scale)
    return samples


def _timed_result(
    name: str,
    description: str,
    fn: Callable[[], Any],
    *,
    repeat: int,
    scale: float = 1.0,
) -> dict[str, Any]:
    samples = _time_samples(fn, repeat=repeat, scale=scale)
    return {
        "name": name,
        "description": description,
        "status": "ok",
        "runs": repeat,
        "samples_s": samples,
        "median_s": _median(samples),
    }


def _skip_result(name: str, description: str, reason: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "status": "skipped",
        "runs": 0,
        "samples_s": [],
        "median_s": None,
        "skip_reason": reason,
    }


def _load_profile(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text())
    if not isinstance(loaded, dict):
        raise ValueError(f"profile must load to a mapping: {path}")
    return loaded


def _empty_patch() -> RecipePatch:
    return RecipePatch({})


def _high_profile(
    base: Mapping[str, Any],
    *,
    hours: int = DEFAULT_HIGH_HOURS,
) -> dict[str, Any]:
    if hours < 1:
        raise ValueError("high evaluate scenarios require --high-hours >= 1")
    profile = dict(base)
    fidelities = dict(profile.get("fidelities") or {})
    fidelities["high"] = {"backend_name": "alphamelts", "hours": int(hours)}
    profile["fidelities"] = fidelities
    return profile


def _probe_alphamelts(*, mode: str | None = None) -> dict[str, Any]:
    backend = AlphaMELTSBackend()
    config = {"mode": mode} if mode else {}
    try:
        initialized = bool(backend.initialize(config))
        available = bool(initialized and backend.is_available())
        reason = "" if available else "initialize/is_available returned false"
        return {
            "available": available,
            "mode": getattr(backend, "_mode", None),
            "engine_version": getattr(backend, "_engine_version", None),
            "requested_mode": mode or "auto",
            "skip_reason": reason,
        }
    except Exception as exc:  # noqa: BLE001 - optional backend boundary
        return {
            "available": False,
            "mode": None,
            "engine_version": None,
            "requested_mode": mode or "auto",
            "skip_reason": f"{type(exc).__name__}: {exc}",
        }


def _require_thermoengine() -> str | None:
    probe = _probe_alphamelts(mode="thermoengine")
    if probe["available"]:
        return None
    return str(probe["skip_reason"])


def _require_alphamelts(probe: Mapping[str, Any]) -> str | None:
    if probe.get("available"):
        return None
    return str(probe.get("skip_reason") or "AlphaMELTS unavailable")


def _init_thermoengine_backend() -> AlphaMELTSBackend:
    backend = AlphaMELTSBackend()
    if not (backend.initialize({"mode": "thermoengine"}) and backend.is_available()):
        raise RuntimeError("ThermoEngine transport unavailable")
    return backend


def _evaluate_stub_once(profile: Mapping[str, Any], *, candidate_id: str) -> None:
    feedstock = str(profile["feedstock"])
    evaluate(
        _empty_patch(),
        feedstock,
        "stub",
        profile=profile,
        candidate_id=candidate_id,
    )


def _evaluate_alphamelts_once(
    profile: Mapping[str, Any],
    *,
    candidate_id: str,
    hours: int = DEFAULT_HIGH_HOURS,
) -> None:
    feedstock = str(profile["feedstock"])
    evaluate(
        _empty_patch(),
        feedstock,
        "high",
        profile=_high_profile(profile, hours=hours),
        candidate_id=candidate_id,
    )


def scenario_backend_init(repeat: int) -> dict[str, Any]:
    skip_reason = _require_thermoengine()
    if skip_reason:
        return _skip_result(
            "backend_init",
            "ThermoEngine init-only: AlphaMELTSBackend.initialize(mode=thermoengine)",
            skip_reason,
        )
    return _timed_result(
        "backend_init",
        "ThermoEngine init-only: AlphaMELTSBackend.initialize(mode=thermoengine)",
        lambda: _init_thermoengine_backend(),
        repeat=repeat,
    )


def scenario_equilibrate_once(repeat: int) -> dict[str, Any]:
    skip_reason = _require_thermoengine()
    if skip_reason:
        return _skip_result(
            "equilibrate_once",
            "Single ThermoEngine equilibrate() call after one backend init",
            skip_reason,
        )
    backend = _init_thermoengine_backend()

    def _once() -> None:
        backend.equilibrate(
            temperature_C=1400.0,
            pressure_bar=1.0,
            composition_kg=MARE_OXIDES_WT,
        )

    return _timed_result(
        "equilibrate_once",
        "Single ThermoEngine equilibrate() call after one backend init",
        _once,
        repeat=repeat,
    )


def scenario_evaluate_stub_1h(
    profile: Mapping[str, Any],
    repeat: int,
) -> dict[str, Any]:
    counter = 0

    def _once() -> None:
        nonlocal counter
        counter += 1
        _evaluate_stub_once(profile, candidate_id=f"profile-stub-{counter}")

    return _timed_result(
        "evaluate_stub_1h",
        "Full evaluate() on stub fidelity, 1 hour, empty patch",
        _once,
        repeat=repeat,
    )


def scenario_evaluate_stub_repeat(
    profile: Mapping[str, Any],
    repeat: int,
) -> dict[str, Any]:
    pair_counter = 0

    def _pair() -> None:
        nonlocal pair_counter
        pair_counter += 1
        _evaluate_stub_once(
            profile,
            candidate_id=f"profile-stub-repeat-{pair_counter}-1",
        )
        _evaluate_stub_once(
            profile,
            candidate_id=f"profile-stub-repeat-{pair_counter}-2",
        )

    return _timed_result(
        "evaluate_stub_repeat",
        "Two sequential stub evaluate() calls in one PID; samples are seconds per eval",
        _pair,
        repeat=repeat,
        scale=0.5,
    )


def scenario_evaluate_alphamelts_1h(
    profile: Mapping[str, Any],
    repeat: int,
    alphamelts_probe: Mapping[str, Any],
    high_hours: int,
) -> dict[str, Any]:
    description = (
        "Full evaluate() on high fidelity with backend_name=alphamelts, "
        f"{high_hours} hour(s)"
    )
    if high_hours < 1:
        return _skip_result(
            "evaluate_alphamelts_1h",
            description,
            "--high-hours=0 closeout mode skips full high evaluate() to avoid "
            "multi-hour profiler sweeps; use --high-hours 1 for a full run.",
        )
    skip_reason = _require_alphamelts(alphamelts_probe)
    if skip_reason:
        return _skip_result(
            "evaluate_alphamelts_1h",
            description,
            skip_reason,
        )
    counter = 0

    def _once() -> None:
        nonlocal counter
        counter += 1
        _evaluate_alphamelts_once(
            profile,
            candidate_id=f"profile-high-{counter}",
            hours=high_hours,
        )

    return _timed_result(
        "evaluate_alphamelts_1h",
        description,
        _once,
        repeat=repeat,
    )


def scenario_evaluate_repeat(
    profile: Mapping[str, Any],
    repeat: int,
    alphamelts_probe: Mapping[str, Any],
    high_hours: int,
) -> dict[str, Any]:
    description = (
        "Two sequential high evaluate() calls in one PID; samples are seconds "
        f"per eval at {high_hours} hour(s)"
    )
    if high_hours < 1:
        return _skip_result(
            "evaluate_repeat",
            description,
            "--high-hours=0 closeout mode skips high same-PID repeat; use "
            "--high-hours 1 for a full run.",
        )
    skip_reason = _require_alphamelts(alphamelts_probe)
    if skip_reason:
        return _skip_result(
            "evaluate_repeat",
            description,
            skip_reason,
        )
    pair_counter = 0

    def _pair() -> None:
        nonlocal pair_counter
        pair_counter += 1
        _evaluate_alphamelts_once(
            profile,
            candidate_id=f"profile-repeat-{pair_counter}-1",
            hours=high_hours,
        )
        _evaluate_alphamelts_once(
            profile,
            candidate_id=f"profile-repeat-{pair_counter}-2",
            hours=high_hours,
        )

    return _timed_result(
        "evaluate_repeat",
        description,
        _pair,
        repeat=repeat,
        scale=0.5,
    )


def _fork_worker(queue: Any, payload: str) -> None:
    pin_worker_env()
    try:
        if payload == "noop":
            queue.put(("ok", None))
            return
        profile = yaml.safe_load(Path(payload).read_text())
        if not isinstance(profile, dict):
            raise ValueError(f"profile must load to a mapping: {payload}")
        _evaluate_stub_once(profile, candidate_id="profile-fork")
        queue.put(("ok", None))
    except Exception as exc:  # noqa: BLE001 - child reports compact reason
        queue.put(("error", f"{type(exc).__name__}: {exc}"))


def _fork_join(payload: str, *, start_method: str) -> float:
    ctx = mp.get_context(start_method)
    queue: mp.Queue[Any] = ctx.Queue(maxsize=1)
    process = ctx.Process(target=_fork_worker, args=(queue, payload))
    t0 = time.perf_counter()
    process.start()
    process.join()
    elapsed = time.perf_counter() - t0
    if process.exitcode != 0:
        raise RuntimeError(f"{start_method} worker failed exitcode={process.exitcode}")
    status, detail = queue.get_nowait()
    if status != "ok":
        raise RuntimeError(f"{start_method} worker failed: {detail}")
    return elapsed


def _process_start_method() -> str:
    return "fork" if "fork" in mp.get_all_start_methods() else "spawn"


def scenario_fidelity_fork_stub(profile_path: Path, repeat: int) -> dict[str, Any]:
    start_method = _process_start_method()
    noop_samples = [
        _fork_join("noop", start_method=start_method)
        for _ in range(repeat)
    ]
    stub_samples = [
        _fork_join(str(profile_path), start_method=start_method)
        for _ in range(repeat)
    ]
    noop_median = _median(noop_samples)
    stub_median = _median(stub_samples)
    tax_median = None
    if noop_median is not None and stub_median is not None:
        tax_median = max(0.0, stub_median - noop_median)
    return {
        "name": "fidelity_fork_stub",
        "description": (
            "Fidelity harness fork/spawn tax: empty child vs child running one "
            "stub evaluate()"
        ),
        "status": "ok",
        "runs": repeat,
        "start_method": start_method,
        "noop": {
            "samples_s": noop_samples,
            "median_s": noop_median,
        },
        "stub_evaluate": {
            "samples_s": stub_samples,
            "median_s": stub_median,
        },
        "tax_median_s": tax_median,
    }


def _scenario_median(scenario: Mapping[str, Any]) -> float | None:
    if scenario.get("name") == "fidelity_fork_stub":
        value = scenario.get("tax_median_s")
    else:
        value = scenario.get("median_s")
    return float(value) if isinstance(value, (int, float)) else None


def _fmt_seconds(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value >= 100:
        return f"{value:.1f}"
    if value >= 10:
        return f"{value:.2f}"
    if value >= 1:
        return f"{value:.3f}"
    return f"{value:.4f}"


def _rank_hypotheses(results: Mapping[str, Any]) -> list[dict[str, Any]]:
    scenarios = results["scenarios"]
    h1 = _scenario_median(scenarios["fidelity_fork_stub"])
    h2 = _scenario_median(scenarios["backend_init"])
    h3 = _scenario_median(scenarios["equilibrate_once"])
    hypotheses = [
        {
            "id": "H1",
            "name": "Fidelity fork/spawn tax",
            "signal": "T_fork",
            "median_s": h1,
            "scenario": "fidelity_fork_stub",
            "candidate_fix": "simulator/optimize/fidelity.py:201",
            "chunk": "G9.7c",
            "expected_impact": "remove per-eval child startup from fidelity harness",
        },
        {
            "id": "H2",
            "name": "Backend init per evaluate()",
            "signal": "T_init",
            "median_s": h2,
            "scenario": "backend_init",
            "candidate_fix": "simulator/optimize/pool.py:247",
            "chunk": "G9.7a",
            "expected_impact": "warm one backend per worker instead of per evaluate()",
        },
        {
            "id": "H3",
            "name": "MELTSmodel per equilibrate()",
            "signal": "T_eq",
            "median_s": h3,
            "scenario": "equilibrate_once",
            "candidate_fix": "engines/alphamelts/thermoengine.py:104",
            "chunk": "G9.7b",
            "expected_impact": "reuse MELTSmodel and reset composition per equilibrate()",
        },
    ]
    ranked = sorted(
        hypotheses,
        key=lambda row: (
            row["median_s"] is not None,
            row["median_s"] if row["median_s"] is not None else -1.0,
        ),
        reverse=True,
    )
    for index, row in enumerate(ranked, start=1):
        row["rank"] = index if row["median_s"] is not None else None
    return ranked


def _derived(results: Mapping[str, Any]) -> dict[str, Any]:
    scenarios = results["scenarios"]
    return {
        "T_init_s": _scenario_median(scenarios["backend_init"]),
        "T_eq_s": _scenario_median(scenarios["equilibrate_once"]),
        "T_fork_s": _scenario_median(scenarios["fidelity_fork_stub"]),
        "T_stub_s": _scenario_median(scenarios["evaluate_stub_1h"]),
        "T_stub_repeat_per_eval_s": _scenario_median(scenarios["evaluate_stub_repeat"]),
        "T_high_1h_s": _scenario_median(scenarios["evaluate_alphamelts_1h"]),
        "T_high_repeat_per_eval_s": _scenario_median(scenarios["evaluate_repeat"]),
    }


def _study_pool_note(results: Mapping[str, Any]) -> str:
    derived = results["derived"]
    high = derived["T_high_1h_s"]
    repeat = derived["T_high_repeat_per_eval_s"]
    stub = derived["T_stub_s"]
    stub_repeat = derived["T_stub_repeat_per_eval_s"]
    fork = derived["T_fork_s"]
    stub_note = ""
    if stub is not None and stub_repeat is not None:
        stub_delta = stub_repeat - stub
        stub_note = (
            " Stub same-PID repeat was "
            f"{_fmt_seconds(stub_repeat)} s/eval vs cold stub "
            f"{_fmt_seconds(stub)} s (delta {_fmt_seconds(stub_delta)} s/eval)."
        )
    if high is None or repeat is None:
        return (
            "Study-pool reuse could not be measured for high fidelity because "
            "AlphaMELTS high-evaluate scenarios were skipped."
            f"{stub_note}"
        )
    delta = repeat - high
    return (
        "Same-PID high repeat median was "
        f"{_fmt_seconds(repeat)} s/eval vs cold high median {_fmt_seconds(high)} s "
        f"(delta {_fmt_seconds(delta)} s/eval). Fidelity T_fork applies to "
        f"fidelity._run_eval only ({_fmt_seconds(fork)} s measured on stub child start)."
        f"{stub_note}"
    )


def _skip_reason(results: Mapping[str, Any], scenario_name: str) -> str:
    scenario = results["scenarios"][scenario_name]
    return str(scenario.get("skip_reason") or "scenario not measured")


def _positive_verdict(value: float | None) -> str:
    if value is None:
        return "inconclusive"
    return "validated" if value > 0 else "ruled out"


def _hypothesis_verdicts(results: Mapping[str, Any]) -> list[dict[str, str]]:
    derived = results["derived"]
    t_fork = derived["T_fork_s"]
    t_init = derived["T_init_s"]
    t_eq = derived["T_eq_s"]
    h1_evidence = (
        f"T_fork={_fmt_seconds(t_fork)} from `fidelity_fork_stub`; "
        "`fidelity._run_eval` forks at `simulator/optimize/fidelity.py:201`."
        if t_fork is not None
        else f"`fidelity_fork_stub` skipped: {_skip_reason(results, 'fidelity_fork_stub')}."
    )
    h2_evidence = (
        f"T_init={_fmt_seconds(t_init)} from `backend_init`; "
        "worker initializer hook is `simulator/optimize/pool.py:247`."
        if t_init is not None
        else f"`backend_init` skipped: {_skip_reason(results, 'backend_init')}."
    )
    h3_evidence = (
        f"T_eq={_fmt_seconds(t_eq)} from warm `equilibrate_once`; "
        "`MELTSmodel()` is recreated at `engines/alphamelts/thermoengine.py:104`."
        if t_eq is not None
        else f"`equilibrate_once` skipped: {_skip_reason(results, 'equilibrate_once')}."
    )
    return [
        {
            "id": "H1",
            "hypothesis": "Fidelity fork tax dominates low-hours high evals",
            "verdict": _positive_verdict(t_fork),
            "evidence": h1_evidence,
        },
        {
            "id": "H2",
            "hypothesis": "Backend init per evaluate() reloads ThermoEngine state",
            "verdict": _positive_verdict(t_init),
            "evidence": h2_evidence,
        },
        {
            "id": "H3",
            "hypothesis": "MELTSmodel() per equilibrate() is material",
            "verdict": _positive_verdict(t_eq),
            "evidence": h3_evidence,
        },
        {
            "id": "H4",
            "hypothesis": "VapoRock second pass rivals MELTS per sim hour",
            "verdict": "inconclusive",
            "evidence": (
                "Closeout run uses microbenches plus stub cProfile; no high full-run "
                "VapoRock/MELTS cProfile was collected."
            ),
        },
        {
            "id": "H5",
            "hypothesis": "Subprocess mode beats ThermoEngine for hours=1 screening",
            "verdict": "inconclusive",
            "evidence": (
                "No subprocess-mode scenario in G9.6 closeout; auto probe mode was "
                f"{results['alphamelts_auto'].get('mode')!r}."
            ),
        },
        {
            "id": "H6",
            "hypothesis": "PT-1 prefix replay amortizes repeated reference trajectories",
            "verdict": "inconclusive",
            "evidence": (
                "No reduced-real prefix replay scenario in this profiler; needs "
                "G9.4a/G9.7 cache-replay data."
            ),
        },
    ]


def _hypothesis_verdict_rows(results: Mapping[str, Any]) -> list[str]:
    rows = [
        "| # | Hypothesis | Verdict | Evidence |",
        "|---|---|---|---|",
    ]
    for row in results["hypothesis_verdicts"]:
        rows.append(
            f"| {row['id']} | {row['hypothesis']} | {row['verdict']} | "
            f"{row['evidence']} |"
        )
    return rows


def _required_median_rows(results: Mapping[str, Any]) -> list[str]:
    derived = results["derived"]
    return [
        f"- `T_init`: {_fmt_seconds(derived['T_init_s'])} s",
        f"- `T_eq`: {_fmt_seconds(derived['T_eq_s'])} s",
        f"- `T_fork`: {_fmt_seconds(derived['T_fork_s'])} s",
        f"- `T_stub`: {_fmt_seconds(derived['T_stub_s'])} s",
        (
            "- `T_stub_repeat_same_pid`: "
            f"{_fmt_seconds(derived['T_stub_repeat_per_eval_s'])} s/eval"
        ),
        f"- `T_high`: {_fmt_seconds(derived['T_high_1h_s'])} s",
        (
            "- `T_high_repeat_same_pid`: "
            f"{_fmt_seconds(derived['T_high_repeat_per_eval_s'])} s/eval"
        ),
    ]


def _scenario_rows(results: Mapping[str, Any]) -> list[str]:
    rows = [
        "| Scenario | Runs | Median s | Status | Notes |",
        "|---|---:|---:|---|---|",
    ]
    for name in SCENARIO_ORDER:
        scenario = results["scenarios"][name]
        status = str(scenario["status"])
        median = _scenario_median(scenario)
        runs = int(scenario.get("runs") or 0)
        if status == "skipped":
            notes = str(scenario.get("skip_reason") or "")
        elif name == "fidelity_fork_stub":
            notes = (
                f"start={scenario['start_method']}; "
                f"noop={_fmt_seconds(scenario['noop']['median_s'])}; "
                f"stub_child={_fmt_seconds(scenario['stub_evaluate']['median_s'])}"
            )
        else:
            notes = str(scenario["description"])
        rows.append(
            f"| `{name}` | {runs} | {_fmt_seconds(median)} | {status} | {notes} |"
        )
    return rows


def _backlog_rows(results: Mapping[str, Any]) -> list[str]:
    rows = [
        "| Priority | Fix | Expected impact | Evidence | G9.7 chunk |",
        "|---:|---|---|---|---|",
    ]
    for row in results["hypotheses_ranked"]:
        rank = row["rank"] if row["rank"] is not None else "n/a"
        rows.append(
            f"| {rank} | `{row['candidate_fix']}` | {row['expected_impact']} | "
            f"{row['id']} {row['signal']}={_fmt_seconds(row['median_s'])} "
            f"from `{row['scenario']}` | {row['chunk']} |"
        )
    return rows


def _recommended_dispatch_order(results: Mapping[str, Any]) -> list[str]:
    chunks: list[str] = []
    for row in results["hypotheses_ranked"]:
        if row["median_s"] is None:
            continue
        chunk = str(row["chunk"])
        if chunk not in chunks:
            chunks.append(chunk)
    if not chunks:
        chunks = ["G9.7a", "G9.7c", "G9.7b"]
    return chunks


def _recommended_dispatch_rows(results: Mapping[str, Any]) -> list[str]:
    chunks = results["g97_dispatch_order"]
    reasons = {
        "G9.7a": "persistent workers + warm backend; attacks H2 and enables pool reuse",
        "G9.7b": "cache MELTSmodel on ThermoEngineTransport; attacks H3",
        "G9.7c": "route fidelity through evaluate_batch; attacks H1 fork tax",
    }
    rows = [
        "| Order | Chunk | Reason |",
        "|---:|---|---|",
    ]
    for index, chunk in enumerate(chunks, start=1):
        rows.append(f"| {index} | {chunk} | {reasons.get(chunk, 'ranked by profiler data')} |")
    return rows


def _write_findings(results: Mapping[str, Any], out_dir: Path) -> Path:
    timings_path = out_dir / "timings.json"
    findings_path = out_dir / "findings.md"
    lines = [
        "# G9.6 Eval Hotpath Findings",
        "",
        f"- Generated: {results['generated_at_utc']}",
        f"- Git SHA: `{results['git_sha']}`",
        f"- Timings JSON: `{_display_path(timings_path)}`",
        f"- Profile: `{results['profile']}`",
        f"- Repeats: {results['repeat']}",
        f"- High hours: {results['high_hours']}",
        f"- AlphaMELTS auto probe: {json.dumps(results['alphamelts_auto'])}",
        f"- ThermoEngine probe: {json.dumps(results['thermoengine'])}",
        "",
        "## Required Medians",
        "",
        *_required_median_rows(results),
        "",
        "## Scenario Medians",
        "",
        *_scenario_rows(results),
        "",
        "## Hypothesis Verdicts H1-H6",
        "",
        *_hypothesis_verdict_rows(results),
        "",
        "## Ranked Optimization Backlog",
        "",
        *_backlog_rows(results),
        "",
        "## G9.7 Recommended Dispatch Order",
        "",
        *_recommended_dispatch_rows(results),
        "",
        "## Study-Pool Reuse",
        "",
        _study_pool_note(results),
        "",
        "## C1 Contract",
        "",
        (
            "Profiler only. No `worker_runtime.py`, no pool behavior changes, "
            "and no ledger mutation paths were edited."
        ),
        "",
    ]
    findings_path.write_text("\n".join(lines), encoding="utf-8")
    return findings_path


def _normalize_selected(raw: list[str] | None) -> set[str]:
    if not raw:
        return set(SCENARIO_ORDER)
    selected: set[str] = set()
    unknown: list[str] = []
    for name in raw:
        normalized = SCENARIO_ALIASES.get(name, name)
        if normalized not in SCENARIO_ORDER:
            unknown.append(name)
        else:
            selected.add(normalized)
    if unknown:
        known = ", ".join([*SCENARIO_ORDER, *SCENARIO_ALIASES])
        raise SystemExit(f"unknown --scenario {unknown!r}; known scenarios: {known}")
    return selected


def _run_cprofile(
    scenario: str,
    profile: Mapping[str, Any],
    profile_path: Path,
    out_dir: Path,
    high_hours: int,
) -> None:
    normalized = SCENARIO_ALIASES.get(scenario, scenario)
    out_dir.mkdir(parents=True, exist_ok=True)
    cprofile_path = out_dir / "cprofile.txt"

    def _write_cprofile_entry(body: str) -> None:
        entry = (
            f"# cProfile `{normalized}`\n\n"
            f"- Generated: {datetime.now(timezone.utc).isoformat()}\n"
            f"- High hours: {high_hours}\n\n"
            f"{body}"
        )
        if cprofile_path.exists():
            existing = cprofile_path.read_text(encoding="utf-8")
            cprofile_path.write_text(
                existing.rstrip() + "\n\n" + entry,
                encoding="utf-8",
            )
        else:
            cprofile_path.write_text(entry, encoding="utf-8")

    if normalized in {"evaluate_alphamelts_1h", "evaluate_repeat"} and high_hours < 1:
        _write_cprofile_entry(
            "SKIPPED: --high-hours=0 closeout mode skips high evaluate() "
            "cProfile to avoid multi-hour profiler sweeps.\n"
        )
        return

    alphamelts_probe = _probe_alphamelts()
    runners: dict[str, Callable[[], Any]] = {
        "backend_init": lambda: _init_thermoengine_backend(),
        "equilibrate_once": lambda: scenario_equilibrate_once(1),
        "evaluate_stub_1h": lambda: _evaluate_stub_once(
            profile,
            candidate_id="profile-stub-cprofile",
        ),
        "evaluate_stub_repeat": lambda: scenario_evaluate_stub_repeat(profile, 1),
        "evaluate_alphamelts_1h": lambda: _evaluate_alphamelts_once(
            profile,
            candidate_id="profile-high-cprofile",
            hours=high_hours,
        ),
        "evaluate_repeat": lambda: scenario_evaluate_repeat(
            profile,
            1,
            alphamelts_probe,
            high_hours,
        ),
        "fidelity_fork_stub": lambda: scenario_fidelity_fork_stub(profile_path, 1),
    }
    if normalized not in runners:
        raise SystemExit(f"unknown --cprofile scenario {scenario!r}")
    if normalized in {"backend_init", "equilibrate_once"} and _require_thermoengine():
        raise SystemExit("ThermoEngine unavailable; skipping cProfile")
    if normalized in {"evaluate_alphamelts_1h", "evaluate_repeat"}:
        skip_reason = _require_alphamelts(alphamelts_probe)
        if skip_reason:
            raise SystemExit(f"AlphaMELTS unavailable; skipping cProfile: {skip_reason}")

    profiler = cProfile.Profile()
    profiler.enable()
    runners[normalized]()
    profiler.disable()
    stream = StringIO()
    stats = pstats.Stats(profiler, stream=stream)
    stats.sort_stats("cumulative")
    stats.print_stats(40)
    _write_cprofile_entry(stream.getvalue())


def _run_selected(
    selected: set[str],
    profile: Mapping[str, Any],
    profile_path: Path,
    repeat: int,
    alphamelts_probe: Mapping[str, Any],
    high_hours: int,
) -> dict[str, Any]:
    scenarios: dict[str, Any] = {}
    for name in SCENARIO_ORDER:
        if name not in selected:
            scenarios[name] = _skip_result(name, "Filtered by --scenario", "not selected")
            continue
        if name == "backend_init":
            scenarios[name] = scenario_backend_init(repeat)
        elif name == "equilibrate_once":
            scenarios[name] = scenario_equilibrate_once(repeat)
        elif name == "evaluate_stub_1h":
            scenarios[name] = scenario_evaluate_stub_1h(profile, repeat)
        elif name == "evaluate_stub_repeat":
            scenarios[name] = scenario_evaluate_stub_repeat(profile, repeat)
        elif name == "evaluate_alphamelts_1h":
            scenarios[name] = scenario_evaluate_alphamelts_1h(
                profile,
                repeat,
                alphamelts_probe,
                high_hours,
            )
        elif name == "evaluate_repeat":
            scenarios[name] = scenario_evaluate_repeat(
                profile,
                repeat,
                alphamelts_probe,
                high_hours,
            )
        elif name == "fidelity_fork_stub":
            scenarios[name] = scenario_fidelity_fork_stub(profile_path, repeat)
    return scenarios


def main() -> int:
    scenario_help = ", ".join(SCENARIO_ORDER)
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=f"Scenarios: {scenario_help}",
    )
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--repeat",
        type=int,
        default=DEFAULT_REPEAT,
        help="Number of timed runs per applicable scenario (default: 3).",
    )
    parser.add_argument(
        "--high-hours",
        type=int,
        default=DEFAULT_HIGH_HOURS,
        help=(
            "Hours for high-fidelity evaluate scenarios. Use 0 for G9.6 "
            "closeout microbench-only runs; default: 1."
        ),
    )
    parser.add_argument(
        "--scenario",
        action="append",
        help="Run only these scenarios (repeatable).",
    )
    parser.add_argument(
        "--cprofile",
        metavar="SCENARIO",
        help="Run cProfile on one scenario.",
    )
    args = parser.parse_args()
    if args.repeat < 1:
        raise SystemExit("--repeat must be >= 1")
    if args.high_hours < 0:
        raise SystemExit("--high-hours must be >= 0")

    pin_worker_env()
    profile_path = _resolve_repo_path(args.profile)
    out_dir = _resolve_repo_path(args.out)
    profile = _load_profile(profile_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.cprofile:
        _run_cprofile(args.cprofile, profile, profile_path, out_dir, args.high_hours)
        print(f"wrote {_display_path(out_dir / 'cprofile.txt')}")
        return 0

    selected = _normalize_selected(args.scenario)
    alphamelts_probe = _probe_alphamelts()
    results: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "profile": _display_path(profile_path),
        "repeat": args.repeat,
        "high_hours": args.high_hours,
        "alphamelts_auto": alphamelts_probe,
        "thermoengine": _probe_alphamelts(mode="thermoengine"),
        "scenarios": _run_selected(
            selected,
            profile,
            profile_path,
            args.repeat,
            alphamelts_probe,
            args.high_hours,
        ),
    }
    results["derived"] = _derived(results)
    results["hypotheses_ranked"] = _rank_hypotheses(results)
    results["hypothesis_verdicts"] = _hypothesis_verdicts(results)
    results["g97_dispatch_order"] = _recommended_dispatch_order(results)

    timings_path = out_dir / "timings.json"
    timings_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    findings_path = _write_findings(results, out_dir)

    print(json.dumps(results, indent=2))
    print(f"\nwrote {_display_path(timings_path)}")
    print(f"wrote {_display_path(findings_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""W0-W-MUTATOR-v1 custodian worker — the only engine-touching process.

This module is executed as a fresh subprocess per engine touch by
``benchmarks.w0_sens.w_mutator`` (one JSON spec on stdin, one
sentinel-prefixed JSON envelope on stdout). It is never imported by the
parent; importing it is side-effect free (all engine work happens under
``main()``), and its pure helpers are unit-tested against synthetic
vectors.

Build-separation contract (correctness, not security isolation): the
parent spawns this worker with
``DYLD_FALLBACK_LIBRARY_PATH`` pointing at exactly one build prefix's
``lib/`` directory. Before importing thermoengine, every subcommand
asserts that ``ctypes.util.find_library('phaseobjc')`` resolves to that
prefix's ``libphaseobjc.dylib`` — so the Objective-C class registered in
this process can only come from the intended build. The worker process
exits immediately after emitting its envelope, so no perturbed build
outlives the process. This separates builds from each other; it does NOT
isolate anything from a same-user caller (this module is importable like
any other — see the ``w_mutator`` module docstring's honest-claim
section).

Value contract: the full pristine parameter vector (including any held W)
is written only to the quarantine envelope file (mode 0600) or compared
in-memory; stdout carries verdicts, slot indices, joint hashes, and the
readback of the *substituted* frozen value only. A readback MISMATCH is
reported as a fact — parameter slot plus a SHA-256 fingerprint of the
observed value — never the value itself: if the pristine build resolved,
that readback IS the held interaction-parameter value, and the abort
record is exactly where the quarantine must not leak it.

EVALUATION SEAM (added for t-654). ``evaluate-corpus`` and
``synthetic-chem-potential`` are the commands through which the pinned
custodian adapter evaluates benchmark activities "directly against that
build" (PREREGISTRATION-wave0.md:37). Both re-assert, IN THE SAME PROCESS
that computes, that (a) ``ctypes.util.find_library('phaseobjc')`` still
resolves to this build prefix and (b) the live parameter slot reads back
the substituted value exactly — so a numerically silent perturbation is
detected per evaluation, not only per build.

That second assertion is load-bearing because the repository's own
``simulator.engine_local_config.setup_thermoengine_dylib_path`` PREPENDS
the pristine staged dylib directory to ``DYLD_FALLBACK_LIBRARY_PATH``
(``simulator/engine_local_config.py:258-262``) and is called by
``ThermoEngineTransport._initialize_in_process``
(``engines/alphamelts/thermoengine.py:283``). Left alone it would let the
PRISTINE ``libphaseobjc`` win resolution and the screen would compute the
unpatched model while believing it perturbed. ``evaluate-corpus`` therefore
replaces that helper, inside this ephemeral worker process only, with a
guard that verifies the environment the parent already pinned and mutates
nothing.
"""

from __future__ import annotations

import ctypes.util
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.w0_sens.w_mutator import (  # noqa: E402
    MELTS_MODEL,
    PARAM_NAME_RE,
    WORKER_SENTINEL,
    observed_value_fingerprint,
)

_ABORT_TYPE = "ABORT-W-MUTATOR"


class _WorkerAbort(RuntimeError):
    pass


def _vector_sha256(values: Sequence[float]) -> str:
    """Joint commitment hash over the full vector (not per-value)."""
    payload = json.dumps([float(value) for value in values])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def vector_diff_slots(
    baseline_names: Sequence[str],
    baseline_units: Sequence[str],
    baseline_values: Sequence[float],
    current_names: Sequence[str],
    current_units: Sequence[str],
    current_values: Sequence[float],
) -> tuple[int, ...]:
    """Slots whose values differ; name/unit drift is an abort, not a diff."""
    if tuple(baseline_names) != tuple(current_names) or tuple(
        baseline_units
    ) != tuple(current_units):
        raise _WorkerAbort(
            "parameter name/unit vector changed; the build is not an "
            "isolated single-constant substitution"
        )
    if len(baseline_values) != len(current_values):
        raise _WorkerAbort("parameter value vector length changed")
    return tuple(
        index
        for index, (before, after) in enumerate(
            zip(baseline_values, current_values)
        )
        if float(before) != float(after)
    )


def assert_build_prefix_resolves(prefix_lib: str) -> Path:
    """Refuse unless ``libphaseobjc`` resolves to exactly this build prefix.

    Called before AND after engine import by every evaluating command: the
    only thing separating one source-patched build from another is dynamic
    resolution, so a drifted resolution is an abort, not a warning.
    """
    prefix = Path(prefix_lib).resolve()
    expected = prefix / "libphaseobjc.dylib"
    if not expected.is_file():
        raise _WorkerAbort(f"build prefix has no libphaseobjc.dylib: {prefix_lib}")
    found = ctypes.util.find_library("phaseobjc")
    if found is None or Path(found).resolve() != expected:
        raise _WorkerAbort(
            "DYLD resolution of libphaseobjc.dylib does not point at the "
            f"requested build prefix: found={found!r} expected={str(expected)!r}"
        )
    return prefix


def _load_phase(prefix_lib: str) -> Any:
    assert_build_prefix_resolves(prefix_lib)
    from thermoengine import model

    database = model.Database(database="Berman", liq_mod="v1.0", calib=True)
    return database.get_phase("Liq")


def _assert_substitution_live(
    names: Sequence[str],
    values: Sequence[float],
    param_name: str,
    expected_value_J: float,
) -> tuple[int, float]:
    """Prove the loaded build carries the substituted value, exactly."""
    slot = _slot_of(names, param_name)
    readback = float(values[slot])
    expected = float(expected_value_J)
    if readback != expected:
        # MISMATCH FACT, never the value: if the pristine build resolved,
        # the readback IS the held W (WQ-1). Slot + fingerprint only.
        raise _WorkerAbort(
            f"the loaded build does not carry the substituted value for "
            f"{param_name!r} (slot {slot}): readback MISMATCH against the "
            f"frozen {expected!r} J substitution (observed_value_sha256="
            f"{observed_value_fingerprint(readback)}); the perturbation "
            "would be numerically silent"
        )
    return slot, readback


def _capture_vector(phase: Any) -> tuple[list[str], list[str], list[float]]:
    names = [str(name) for name in phase.param_names]
    units = [str(unit) for unit in phase.param_units(param_names=names)]
    values = [float(v) for v in phase.get_param_values(param_names=names)]
    if not (len(names) == len(units) == len(values)):
        raise _WorkerAbort(
            "live W name/unit/value vectors are different lengths: "
            f"{len(names)}/{len(units)}/{len(values)}"
        )
    return names, units, values


def _slot_of(names: Sequence[str], param_name: str) -> int:
    if PARAM_NAME_RE.fullmatch(param_name) is None:
        raise _WorkerAbort(f"unrecognized MELTS W parameter name: {param_name!r}")
    matches = [index for index, name in enumerate(names) if name == param_name]
    if len(matches) != 1:
        raise _WorkerAbort(
            f"exact parameter name {param_name!r} matches {len(matches)} live "
            "slots; the engine identity cannot be confirmed"
        )
    return matches[0]


def _write_envelope(envelope_path: str, payload: Mapping[str, Any]) -> None:
    path = Path(envelope_path)
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(dict(payload), handle)
    os.chmod(path, 0o600)


def _read_envelope(envelope_path: str) -> dict[str, Any]:
    try:
        return json.loads(Path(envelope_path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise _WorkerAbort(f"quarantine envelope unreadable: {exc!r}") from exc


def _cmd_capture_pristine(spec: Mapping[str, Any]) -> dict[str, Any]:
    phase = _load_phase(str(spec["prefix_lib"]))
    names, units, values = _capture_vector(phase)
    slot = _slot_of(names, str(spec["param_name"]))
    _write_envelope(
        str(spec["envelope_path"]),
        {
            "melts_model": MELTS_MODEL,
            "param_name": spec["param_name"],
            "slot_index": slot,
            "prefix_lib": spec["prefix_lib"],
            "names": names,
            "units": units,
            "values": values,
        },
    )
    return {
        "ok": True,
        "n_slots": len(names),
        "slot_index": slot,
        "vector_sha256": _vector_sha256(values),
    }


def _cmd_readback_build(spec: Mapping[str, Any]) -> dict[str, Any]:
    phase = _load_phase(str(spec["prefix_lib"]))
    names, units, values = _capture_vector(phase)
    param_name = str(spec["param_name"])
    slot = _slot_of(names, param_name)
    expected = float(spec["expected_value_J"])
    readback = values[slot]
    if readback != expected:
        # MISMATCH FACT, never the value — see _assert_substitution_live.
        raise _WorkerAbort(
            f"write of the frozen {expected!r} J substitution to "
            f"{param_name!r} (slot {slot}) did not take effect exactly in "
            f"the patched build; readback MISMATCH (observed_value_sha256="
            f"{observed_value_fingerprint(readback)})"
        )
    envelope = _read_envelope(str(spec["envelope_path"]))
    changed = vector_diff_slots(
        envelope["names"],
        envelope["units"],
        envelope["values"],
        names,
        units,
        values,
    )
    if changed != (slot,):
        raise _WorkerAbort(
            "structural diff shows a model datum other than "
            f"{param_name!r} changed at slots {changed!r}"
        )
    return {
        "ok": True,
        "readback_J": readback,
        "changed_slots": list(changed),
        "n_slots": len(names),
        "slot_index": slot,
    }


def _cmd_verify_restoration(spec: Mapping[str, Any]) -> dict[str, Any]:
    phase = _load_phase(str(spec["prefix_lib"]))
    names, units, values = _capture_vector(phase)
    envelope = _read_envelope(str(spec["envelope_path"]))
    matches = (
        tuple(envelope["names"]) == tuple(names)
        and tuple(envelope["units"]) == tuple(units)
        and tuple(float(v) for v in envelope["values"]) == tuple(values)
    )
    return {
        "ok": True,
        "vector_matches_pristine": matches,
        "vector_sha256": _vector_sha256(values),
    }


def _cmd_synthetic_chem_potential(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Endmember chemical potential at a synthetic, target-free state point.

    Step-2 gate primitive. The state point is a fabricated liquid endmember
    mole vector — not a benchmark row, not a corpus composition — so no
    chemical target is touched. The parent forms the signed response as the
    difference against its own ``0 J`` control build, which cancels the
    reference and ideal terms exactly (both builds share them), leaving the
    single-constant excess contribution the analytic reference predicts.

    Every endmember must be supplied strictly positive: ``SolutionPhase``
    nudges exact zeros up to ``sqrt(eps)`` (thermoengine ``phases.py``
    ``_nudge_solution_comp``), which would silently move the mole fractions
    the analytic basis is computed from.
    """
    import numpy as np

    phase = _load_phase(str(spec["prefix_lib"]))
    names, _units, values = _capture_vector(phase)
    slot, readback = _assert_substitution_live(
        names, values, str(spec["param_name"]), float(spec["expected_value_J"])
    )
    endmembers = [str(name) for name in phase.endmember_names]
    base_mol = float(spec["base_mol"])
    component_mol = {
        str(key): float(value) for key, value in dict(spec["component_mol"]).items()
    }
    unknown = sorted(set(component_mol) - set(endmembers))
    if unknown:
        raise _WorkerAbort(
            f"synthetic state names endmembers absent from the live liquid "
            f"phase: {unknown}"
        )
    target = str(spec["target_endmember"])
    if target not in endmembers:
        raise _WorkerAbort(
            f"synthetic target endmember {target!r} is absent from the live "
            "liquid phase"
        )
    mol = [base_mol + component_mol.get(name, 0.0) for name in endmembers]
    if any(not (math.isfinite(value) and value > 0.0) for value in mol):
        raise _WorkerAbort(
            "synthetic mole vector must be strictly positive in every "
            "endmember; exact zeros are nudged by the phase and would move "
            "the analytic mole fractions"
        )
    total = sum(mol)
    temperature_K = float(spec["temperature_K"])
    pressure_bar = float(spec["pressure_bar"])
    raw = phase.chem_potential(
        np.array([temperature_K], dtype=float),
        np.array([pressure_bar], dtype=float),
        mol=np.array([mol], dtype=float),
    )
    array = np.asarray(raw, dtype=float).reshape(-1)
    if array.size != len(endmembers):
        raise _WorkerAbort(
            f"chem_potential returned {array.size} values for "
            f"{len(endmembers)} endmembers"
        )
    mu = float(array[endmembers.index(target)])
    if not math.isfinite(mu):
        raise _WorkerAbort(
            f"chem_potential for {target!r} at the synthetic state point is "
            f"not finite: {mu!r}"
        )
    return {
        "ok": True,
        "mu_J_per_mol": mu,
        "readback_J": readback,
        "slot_index": slot,
        "n_endmembers": len(endmembers),
        "endmember_names": endmembers,
        "mole_fractions": {
            name: value / total for name, value in zip(endmembers, mol)
        },
    }


def _patched_build_transport(prefix_lib: Path) -> Any:
    """The pinned ThermoEngine adapter, bound to exactly this build prefix.

    Reuses ``ThermoEngineTransport._initialize_in_process`` (the pinned
    adapter path) but substitutes the repository's dylib-path helper with a
    verify-only guard: the production helper PREPENDS the pristine staged
    dylib directory (``simulator/engine_local_config.py:258-262``), which
    would let the unpatched ``libphaseobjc`` win resolution and make the
    perturbation numerically silent. The parent has already scoped this
    process's ``DYLD_FALLBACK_LIBRARY_PATH`` to one prefix; the guard proves
    that and mutates nothing.
    """
    import engines.alphamelts.thermoengine as thermoengine_module
    from simulator.melt_backend.alphamelts import activity_from_chem_potential

    def _pinned_dylib_dir() -> Path:
        current = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
        if current != str(prefix_lib):
            raise _WorkerAbort(
                "custodian worker dylib path is not pinned to the build "
                f"prefix: {current!r} != {str(prefix_lib)!r}"
            )
        return prefix_lib

    thermoengine_module.setup_thermoengine_dylib_path = _pinned_dylib_dir
    transport = thermoengine_module.ThermoEngineTransport(
        model_name=MELTS_MODEL,
        activity_converter=activity_from_chem_potential,
    )
    transport._initialize_in_process()
    return transport


def _cmd_evaluate_corpus(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate the eligible benchmark corpus against one patched build.

    One process per build: the Objective-C class namespace is process
    global, so the whole corpus is evaluated inside the single process that
    loaded this prefix. Per-row engine refusals are DATA (typed missing
    cells, step 4), never instrument aborts; only build-identity failures
    abort.
    """
    from benchmarks.melt_activity_benchmark import (
        EngineResult,
        _oxide_mole_fractions,
        _prediction_for_point,
        _reason_line,
        classify_engine_exception,
    )
    from engines.alphamelts.domain import canonical_melt_oxide_activity_name

    prefix_lib = assert_build_prefix_resolves(str(spec["prefix_lib"]))
    param_name = str(spec["param_name"])
    expected_value_J = float(spec["expected_value_J"])
    transport = _patched_build_transport(prefix_lib)
    assert_build_prefix_resolves(str(spec["prefix_lib"]))
    names, _units, values = _capture_vector(transport._liq_phase)
    slot, readback = _assert_substitution_live(
        names, values, param_name, expected_value_J
    )

    cache: dict[tuple[str, float, float | None], Any] = {}
    cells: list[dict[str, Any]] = []
    for point in list(spec["points"]):
        point_id = str(point["id"])
        composition = {
            str(oxide): float(value)
            for oxide, value in dict(point["composition_wt_pct"]).items()
        }
        temperature_K = float(point["temperature_K"])
        fO2_bar = point.get("fO2_bar")
        fO2_log = None if fO2_bar is None else math.log10(float(fO2_bar))
        key = (str(point["composition_id"]), temperature_K, fO2_log)
        if key not in cache:
            try:
                payload = transport._equilibrate_in_process(
                    temperature_C=temperature_K - 273.15,
                    pressure_bar=float(point.get("pressure_bar", 1.0)),
                    comp_wt=composition,
                    fO2_log=fO2_log,
                )
                cache[key] = ("ok", payload)
            except Exception as exc:  # engine refusals are data, not aborts
                status, reason = classify_engine_exception(exc)
                cache[key] = ("error", (status, reason))
        kind, held = cache[key]
        if kind == "error":
            status, reason = held
            cells.append(
                {
                    "point_id": point_id,
                    "value": None,
                    "status": status,
                    "reason": reason,
                }
            )
            continue
        payload = held
        activities: dict[str, float] = {}
        unmapped: list[str] = []
        for label, raw_value in dict(payload.activity_coefficients).items():
            value = float(raw_value)
            if not (math.isfinite(value) and value > 0.0):
                continue
            oxide = canonical_melt_oxide_activity_name(label)
            if oxide is not None:
                activities.setdefault(str(oxide), value)
            else:
                unmapped.append(str(label))
        mole_fractions = _oxide_mole_fractions(composition)
        gammas = {
            oxide: activity / mole_fractions[oxide]
            for oxide, activity in activities.items()
            if mole_fractions.get(oxide, 0.0) > 0.0
        }
        result = EngineResult(
            status="ok",
            activities=activities,
            gammas=gammas,
            details={
                "unmapped_activity_labels": sorted(unmapped),
                "solver_converged": bool(payload.solver_converged),
                "liquid_present": bool(payload.liquid_composition_wt_pct),
                "fO2_protocol": "pinned" if fO2_log is not None else "intrinsic",
                "solved_fO2_reason": payload.solved_fO2_reason,
            },
        )
        enriched = {**point, "composition_wt_pct": composition}
        prediction, prediction_reason = _prediction_for_point(enriched, result)
        cells.append(
            {
                "point_id": point_id,
                "value": None if prediction is None else float(prediction),
                "status": "ok" if prediction is not None else "observable_unavailable",
                "reason": _reason_line(prediction_reason),
            }
        )
    return {
        "ok": True,
        "readback_J": readback,
        "slot_index": slot,
        "n_slots": len(names),
        "cells": cells,
    }


_COMMANDS = {
    "capture-pristine": _cmd_capture_pristine,
    "readback-build": _cmd_readback_build,
    "verify-restoration": _cmd_verify_restoration,
    "evaluate-corpus": _cmd_evaluate_corpus,
    "synthetic-chem-potential": _cmd_synthetic_chem_potential,
}


def main() -> int:
    try:
        spec = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        print(f"{WORKER_SENTINEL}{json.dumps({'ok': False, 'abort': _ABORT_TYPE, 'detail': f'bad spec: {exc!r}'})}")
        return 2
    command = _COMMANDS.get(str(spec.get("command")))
    if command is None:
        payload = {
            "ok": False,
            "abort": _ABORT_TYPE,
            "detail": f"unknown worker command: {spec.get('command')!r}",
        }
        print(f"{WORKER_SENTINEL}{json.dumps(payload)}")
        return 2
    try:
        payload = command(spec)
    except _WorkerAbort as exc:
        payload = {"ok": False, "abort": _ABORT_TYPE, "detail": str(exc)}
        print(f"{WORKER_SENTINEL}{json.dumps(payload)}")
        return 2
    except Exception as exc:  # engine import/equilibration failures are aborts
        payload = {
            "ok": False,
            "abort": _ABORT_TYPE,
            "detail": f"{type(exc).__name__}: {exc}",
        }
        print(f"{WORKER_SENTINEL}{json.dumps(payload)}")
        return 2
    print(f"{WORKER_SENTINEL}{json.dumps(payload)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
readback of the *substituted* frozen value only.
"""

from __future__ import annotations

import ctypes.util
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.w0_sens.w_mutator import (  # noqa: E402
    MELTS_MODEL,
    PARAM_NAME_RE,
    WORKER_SENTINEL,
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


def _load_phase(prefix_lib: str) -> Any:
    expected = (Path(prefix_lib).resolve() / "libphaseobjc.dylib")
    if not expected.is_file():
        raise _WorkerAbort(f"build prefix has no libphaseobjc.dylib: {prefix_lib}")
    found = ctypes.util.find_library("phaseobjc")
    if found is None or Path(found).resolve() != expected:
        raise _WorkerAbort(
            "DYLD resolution of libphaseobjc.dylib does not point at the "
            f"requested build prefix: found={found!r} expected={str(expected)!r}"
        )
    from thermoengine import model

    database = model.Database(database="Berman", liq_mod="v1.0", calib=True)
    return database.get_phase("Liq")


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
        raise _WorkerAbort(
            f"write of {expected!r} J to {param_name!r} did not take effect "
            f"exactly in the patched build; readback={readback!r}"
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


_COMMANDS = {
    "capture-pristine": _cmd_capture_pristine,
    "readback-build": _cmd_readback_build,
    "verify-restoration": _cmd_verify_restoration,
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

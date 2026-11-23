"""Live numeric probes require engine version bumps when outputs change."""

from __future__ import annotations

import builtins
import hashlib
import inspect
import json
import math
import os
import subprocess
import sys
import threading
from collections.abc import Mapping, Sequence
from dataclasses import replace
from functools import wraps
from pathlib import Path

import pytest

from simulator import engine_cache


# The controller's full-suite loadgroup run exited a worker with this module
# grouped, and completed without the group. Each probe is already isolated in
# its own subprocess, so the probes need no shared xdist worker.


# Each value pins the current cache chemistry version with one live solver
# result. Update the pair together after a reviewed numeric engine change.
ENGINE_GOLDEN_PROBES = {
    "openimcc": (1, "0656d169b9fb045ebfc2455b7c725cf716817bfac00247d8406f2d24692ecf5d"),
    "internal-analytical": (4, "9cb3af327af7d1f04cabc2dc21b704802324e4da7e93f61310d2b6734996bef0"),
    "vaporock": (1, "673a8c5ac16725e21c985b585a5320f08bc2bf31f785d9f68e7c1cd3f9753f3b"),
    "magemin": (4, "59eb4ffd0c28195b79b5fdfb1f212fe0f13ed1c1112afa1ae724ce60797cbac8"),
    "alphamelts": (4, "3d6e809e84c119afc0341d5bcec193fd0c0886e027c16d0fcf247035f3547af4"),
    "thermoengine": (4, "3db7ad76c46e3130880e2782b235e739a7de72b825180c84fa94e46ec68bfb53"),
}

_EXPECTED_NUMERIC_TRACE_PATHS = {
    "openimcc": set(),
    "internal-analytical": {
        "data/vapor_pressures.yaml",
        "data/setpoints.yaml",
        "data/species_catalog.yaml",
        "data/literature/extracts/nasa-cea-thermo.yaml",
        "data/literature/extracts/ivtan-mno-coo-thermo.yaml",
    },
    "vaporock": set(),
    "magemin": set(),
    "alphamelts": {
        "data/vapor_pressures.yaml",
        "data/literature/extracts/nasa-cea-thermo.yaml",
        "data/literature/extracts/ivtan-mno-coo-thermo.yaml",
    },
    "thermoengine": {
        "data/vapor_pressures.yaml",
        "data/literature/extracts/nasa-cea-thermo.yaml",
        "data/literature/extracts/ivtan-mno-coo-thermo.yaml",
    },
}

def _numeric_leaves(value, path=()):
    if isinstance(value, bool) or value is None:
        return {}
    if isinstance(value, int):
        return {".".join(path): value}
    if isinstance(value, float):
        if math.isfinite(value):
            return {".".join(path): value.hex()}
        return {".".join(path): repr(value)}
    if isinstance(value, Mapping):
        leaves = {}
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            name = str(key)
            leaves.update(_numeric_leaves(item, (*path, name)))
        return leaves
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        leaves = {}
        for index, item in enumerate(value):
            leaves.update(_numeric_leaves(item, (*path, str(index))))
        return leaves
    return {}


def _numeric_output_digest(cache_value) -> str:
    assert isinstance(cache_value, engine_cache.EngineCacheValue)
    numeric = _numeric_leaves(
        {
            "hot": engine_cache.live_hot(cache_value.hot),
            "cold": cache_value.cold,
        }
    )
    assert numeric, "probe returned no numeric cached output fields"
    encoded = json.dumps(numeric, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _backend_cache_value(engine_name: str, result):
    from simulator.melt_backend.base import EquilibriumResult, equilibrium_to_hot

    if engine_name == "openimcc":
        return result.to_hot()
    if isinstance(result, EquilibriumResult):
        return result.to_hot(engine_id=engine_name)
    if isinstance(result, Mapping):
        return equilibrium_to_hot(result, engine_id=engine_name)
    return equilibrium_to_hot(
        vars(result), engine_id=engine_name, live_value=result
    )


def _trace_workspace_data_reads(
    monkeypatch: pytest.MonkeyPatch,
    callsites: dict[str, set[str]],
) -> set[str]:
    root = engine_cache._ENGINE_CACHE_ROOT.resolve()
    reads: set[str] = set()

    def record(path) -> None:
        try:
            resolved = Path(path).resolve()
            relative = resolved.relative_to(root)
        except (OSError, TypeError, ValueError):
            return
        if relative.parts and relative.parts[0] == "data":
            relative_path = relative.as_posix()
            reads.add(relative_path)
            for frame in inspect.stack()[1:20]:
                frame_path = Path(frame.filename)
                if (
                    frame_path == Path(__file__)
                    or "pathlib" in frame.filename
                    or frame.function in {"record", "read_bytes", "read_text", "path_open", "builtin_open"}
                ):
                    continue
                callsites.setdefault(relative_path, set()).add(
                    f"{frame_path.name}:{frame.function}"
                )
                break

    original_read_bytes = Path.read_bytes
    original_read_text = Path.read_text
    original_path_open = Path.open
    original_builtin_open = builtins.open

    def read_bytes(path, *args, **kwargs):
        record(path)
        return original_read_bytes(path, *args, **kwargs)

    def read_text(path, *args, **kwargs):
        record(path)
        return original_read_text(path, *args, **kwargs)

    def path_open(path, *args, **kwargs):
        record(path)
        return original_path_open(path, *args, **kwargs)

    def builtin_open(file, *args, **kwargs):
        record(file)
        return original_builtin_open(file, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "open", path_open)
    monkeypatch.setattr(builtins, "open", builtin_open)
    return reads


def _assert_trace_is_fingerprinted(
    engine_name: str,
    observed: set[str],
    callsites: Mapping[str, set[str]],
) -> None:
    numeric_reads = _numeric_trace_paths(observed, callsites)
    declared = {
        input_ref.path
        for input_ref in engine_cache.ENGINE_CACHE_INPUTS[engine_name]
        if input_ref.kind == "data"
    }
    unrecorded = numeric_reads - declared
    assert not unrecorded, (
        f"{engine_name} numeric data reads are absent from COLD-row trace metadata: "
        f"{sorted(unrecorded)}; numeric trace={sorted(numeric_reads)}; "
        f"full trace={sorted(observed)}"
    )


def _numeric_trace_paths(
    observed: set[str], callsites: Mapping[str, set[str]]
) -> set[str]:
    identity_only = {"engine_binding_admission.py:file_record"}
    return {
        path
        for path in observed
        if callsites.get(path) != identity_only
    }


def _assert_expected_numeric_trace(
    engine_name: str,
    observed: set[str],
    callsites: Mapping[str, set[str]],
) -> None:
    expected = set(_EXPECTED_NUMERIC_TRACE_PATHS[engine_name])
    # Engine binding admission hashes these files only for runtime provenance;
    # that diagnostic snapshot is not part of the cached numeric result.
    numeric_reads = _numeric_trace_paths(observed, callsites)
    missing = expected - numeric_reads
    assert not missing, (
        f"{engine_name} numeric input trace did not reach expected files: "
        f"{sorted(missing)}; full trace={sorted(observed)}"
    )


def _assert_probe_matches_golden(engine: str, version: int, actual: str) -> None:
    expected_version, expected_digest = ENGINE_GOLDEN_PROBES[engine]
    assert version == expected_version, (
        f"{engine}: ENGINE_CHEMISTRY_VERSION changed from {expected_version} "
        f"to {version}; review the numeric probe and update its pinned pair"
    )
    assert actual == expected_digest, (
        f"{engine}: live numeric probe changed without its pinned version/digest "
        f"pair; bump ENGINE_CHEMISTRY_VERSION after confirming the numeric change. "
        f"Expected {expected_digest}, got {actual}"
    )


def _clear_numeric_input_read_caches() -> None:
    """Make the real solve reopen its data inputs inside the trace window."""
    from simulator.vapour_rail import catalog

    catalog.clear_vapor_pressure_view_caches()
    catalog._THERMO_EXTRACT_PARSE_CACHE.clear()
    catalog._THERMO_OBSERVATION_CACHE.clear()


def _live_engine_probe(engine_name, nodeid, monkeypatch, *, mutate_cold=False):
    from simulator.battery.enums import Engine
    from simulator.diagnostic_helpers.binary_pot_battery import (
        Po2Request,
        close_battery_engine_handle,
        equilibrate_cell,
        load_binary_pots,
    )
    from simulator.engine_cache import _engine_cache_path_scope, _without_engine_cache
    from tests.battery.cache_test_support import open_available_engine

    loaded_pots, _metadata = load_binary_pots()
    pots = {pot.pot_id: pot for pot in loaded_pots}
    cached_values = []
    cached_requests = []
    callsites: dict[str, set[str]] = {}
    traced = _trace_workspace_data_reads(monkeypatch, callsites)
    _clear_numeric_input_read_caches()
    mutations = []
    if mutate_cold:
        from simulator.melt_backend.base import EquilibriumResult

        original_to_hot = EquilibriumResult.to_hot

        def changed_enthalpy(result, *args, **kwargs):
            old = result.system_enthalpy
            result.system_enthalpy = (0.0 if old is None else float(old)) + 12345.0
            mutations.append((old, result.system_enthalpy))
            return original_to_hot(result, *args, **kwargs)

        monkeypatch.setattr(EquilibriumResult, "to_hot", changed_enthalpy)
    original_cached_call = engine_cache.cached_engine_call

    def capture_cache_value(engine_id, cache_request, solve):
        def capture_solve():
            value = solve()
            if engine_id == engine_name:
                cached_values.append(value)
                cached_requests.append(cache_request)
            return value

        return original_cached_call(engine_id, cache_request, capture_solve)

    monkeypatch.setattr(engine_cache, "cached_engine_call", capture_cache_value)
    handle = open_available_engine(Engine(engine_name), nodeid=nodeid)
    try:
        original_backend_equilibrate = handle.backend.equilibrate

        @wraps(original_backend_equilibrate)
        def capture_backend_cache_value(*args, **kwargs):
            result = original_backend_equilibrate(*args, **kwargs)
            if not cached_values:
                cached_values.append(_backend_cache_value(engine_name, result))
            return result

        monkeypatch.setattr(
            handle.backend,
            "equilibrate",
            capture_backend_cache_value,
        )
        with _engine_cache_path_scope("probe.sqlite"), _without_engine_cache():
            probe_pot = pots["feo_sio2_40_60"]
            if engine_name == "internal-analytical":
                probe_pot = replace(
                    probe_pot,
                    pot_id="fe_na_sio2_cache_probe",
                    composition_wt_pct={
                        "FeO": 20.0,
                        "Na2O": 20.0,
                        "SiO2": 60.0,
                    },
                )
            cell = equilibrate_cell(
                handle,
                probe_pot,
                # d-339 changes Fe activity beyond the 1600 C Holzheid edge.
                temperature_K=(
                    1605.0 + 273.15
                    if engine_name == "internal-analytical"
                    else 1700.0
                ),
                po2=Po2Request(mode="commanded", po2_bar=1.0e-8),
                timeout_s=90.0,
                isolated=False,
            )
        assert cached_values, (
            f"{engine_name}: real solve did not expose its cache value; "
            f"cell status={cell.status}, refusal={cell.refusal_reason}, "
            f"engine status={cell.engine_status}, reason={cell.engine_reason}"
        )
        return {
            "pid": os.getpid(),
            "version": engine_cache.ENGINE_CHEMISTRY_VERSION[engine_name],
            "digest": _numeric_output_digest(cached_values[-1]),
            "request": cached_requests[-1] if cached_requests else None,
            "observed": sorted(traced),
            "callsites": {path: sorted(sites) for path, sites in callsites.items()},
            "mutations": len(mutations),
            "vapor_pressure_species": _cached_vapor_pressure_species(
                engine_name, cached_values[-1]
            ),
        }
    finally:
        close_battery_engine_handle(handle)


def _cached_vapor_pressure_species(engine_name, cache_value):
    if engine_name == "openimcc":
        return []
    from simulator.melt_backend.base import equilibrium_cache_from_value

    hot = engine_cache.live_hot(cache_value)
    if not isinstance(hot, list):
        return []
    result = equilibrium_cache_from_value(hot, engine_id=engine_name)
    pressures = result.get("vapor_pressures_Pa")
    if not isinstance(pressures, Mapping):
        return []
    return sorted(str(name).split("(", 1)[0].strip() for name in pressures)


def _run_probe(
    engine_name, request, tmp_path, *, mutate_cold=False, timeout_s=120.0, cwd=None
):
    result_path = tmp_path / "probe.json"
    # Keep native work and process-wide I/O patches out of the pytest worker.
    # subprocess.run kills and reaps the probe if its outer deadline expires.
    # Preserve pytest's activated import paths as well as the parent's engine
    # environment and cwd; starting a fresh interpreter otherwise loses them.
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(str(Path(path).resolve()) for path in sys.path),
    }
    completed = subprocess.run(
        [
            sys.executable, "-m", "tests.test_engine_cache_golden_probes", engine_name,
            request.node.nodeid, str(result_path), str(int(mutate_cold)),
        ],
        env=environment,
        stdin=subprocess.DEVNULL,  # The worker's stdin belongs to xdist's channel.
        capture_output=True,
        text=True,
        timeout=timeout_s,
        cwd=cwd,
    )
    assert completed.returncode == 0, (
        f"{engine_name} probe exited {completed.returncode}\n"
        f"{completed.stdout}\n{completed.stderr}"
    )
    result = json.loads(result_path.read_text())
    if "skip" in result:
        pytest.skip(result["skip"])
    if "fail" in result:
        pytest.fail(result["fail"], pytrace=False)
    assert result["pid"] != os.getpid(), "live probe ran in the pytest worker"
    observed = set(result["observed"])
    callsites = {path: set(sites) for path, sites in result["callsites"].items()}
    _assert_trace_is_fingerprinted(engine_name, observed, callsites)
    _assert_expected_numeric_trace(engine_name, observed, callsites)
    return result


def _assert_key_portable_across_working_directories(
    engine_name: str,
    version: int,
    probe_request: Mapping[str, object],
    tmp_path: Path,
) -> None:
    repository = engine_cache._ENGINE_CACHE_ROOT.resolve()
    environment = {
        **os.environ,
        "PYTHONPATH": str(repository),
    }
    script = (
        "import json, sys\n"
        "from simulator.engine_cache import engine_cache_key\n"
        "print(engine_cache_key(sys.argv[1], int(sys.argv[2]), "
        "json.loads(sys.argv[3])).hex())\n"
    )
    request_json = json.dumps(probe_request, sort_keys=True, separators=(",", ":"))
    keys = []
    for name in ("cwd-one", "cwd-two"):
        cwd = tmp_path / name
        cwd.mkdir(parents=True)
        completed = subprocess.run(
            [sys.executable, "-c", script, engine_name, str(version), request_json],
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )
        keys.append(completed.stdout.strip())
    assert keys[0] == keys[1], (
        f"{engine_name} canonical probe key changed across working directories"
    )


@pytest.fixture(autouse=True)
def _no_probe_worker_leaks():
    originals = (builtins.open, Path.open, Path.read_bytes, Path.read_text)
    threads = set(threading.enumerate())
    yield
    assert (builtins.open, Path.open, Path.read_bytes, Path.read_text) == originals
    leaked = set(threading.enumerate()) - threads
    assert not leaked, f"probe left live threads in the pytest worker: {leaked}"


def test_probe_timeout_kills_and_reaps_work(request, tmp_path, monkeypatch):
    pid_path = tmp_path / "probe.pid"
    executable = tmp_path / "hanging-probe"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import builtins, os, threading\n"
        "from pathlib import Path\n"
        f"Path({str(pid_path)!r}).write_text(str(os.getpid()))\n"
        "builtins.open = lambda *args, **kwargs: None\n"
        "threading.Thread(target=threading.Event().wait, daemon=True).start()\n"
        "threading.Event().wait()\n"
    )
    executable.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(executable))
    with pytest.raises(subprocess.TimeoutExpired):
        _run_probe("thermoengine", request, tmp_path, timeout_s=10.0)
    pid = int(pid_path.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)


@pytest.mark.parametrize(
    ("reason", "outcome"),
    (
        ("No module named 'missing_probe_engine'", pytest.skip.Exception),
        ("native engine initialization failed", pytest.fail.Exception),
    ),
)
def test_probe_transports_engine_availability_policy(
    reason, outcome, request, tmp_path, monkeypatch
):
    executable = tmp_path / "unavailable-probe"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import runpy, sys\n"
        "from types import SimpleNamespace\n"
        "from simulator.diagnostic_helpers import binary_pot_battery\n"
        "binary_pot_battery.open_battery_engine = lambda name: SimpleNamespace(\n"
        f"    available=False, backend=None, unavailable_reason={reason!r})\n"
        "sys.argv = sys.argv[2:]\n"
        "runpy.run_module(sys.argv[0], run_name='__main__')\n"
    )
    executable.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(executable))
    with pytest.raises(outcome, match=reason):
        _run_probe("magemin", request, tmp_path)
    result = json.loads((tmp_path / "probe.json").read_text())
    assert reason in result["skip" if outcome is pytest.skip.Exception else "fail"]


def test_probe_inherits_engine_resolution_context(request, tmp_path, monkeypatch):
    import_root = tmp_path / "imports"
    import_root.mkdir()
    (import_root / "probe_activation.py").write_text("activated = True\n")
    monkeypatch.syspath_prepend(str(import_root))
    monkeypatch.chdir(tmp_path)
    probe_cwd = Path.cwd().resolve()
    monkeypatch.setenv("DYLD_FALLBACK_LIBRARY_PATH", str(tmp_path / "lib"))
    executable = tmp_path / "context-probe"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "import probe_activation\n"
        "assert probe_activation.activated\n"
        f"assert Path.cwd().resolve() == Path({str(probe_cwd)!r}).resolve()\n"
        f"assert os.environ['DYLD_FALLBACK_LIBRARY_PATH'] == {str(tmp_path / 'lib')!r}\n"
        "assert sys.stdin.read() == ''\n"
        "Path(sys.argv[5]).write_text(json.dumps({\n"
        "    'pid': os.getpid(), 'observed': [], 'callsites': {},\n"
        "    'context_inherited': True,\n"
        "}))\n"
    )
    executable.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(executable))
    result = _run_probe("openimcc", request, tmp_path, cwd=probe_cwd)
    assert result["context_inherited"]


@pytest.mark.parametrize("engine_name", tuple(engine_cache.ENGINE_CHEMISTRY_VERSION))
def test_live_engine_numeric_probe_matches_version(
    engine_name, request, tmp_path, monkeypatch
) -> None:
    from tests.test_engine_cache_input_identity import (
        _assert_probe_key_ignores_source_changes,
    )

    result = _run_probe(engine_name, request, tmp_path)
    _assert_probe_matches_golden(engine_name, result["version"], result["digest"])
    probe_request = result["request"]
    assert isinstance(probe_request, Mapping), (
        f"{engine_name}: live probe did not capture the production cache request"
    )
    key = engine_cache.engine_cache_key(
        engine_name, result["version"], probe_request
    )
    changed_request = dict(probe_request)
    if "temperature_K" in changed_request:
        changed_request["temperature_K"] = float(changed_request["temperature_K"]) + 1.0
    elif isinstance(changed_request.get("kwargs"), Mapping):
        changed_kwargs = dict(changed_request["kwargs"])
        changed_kwargs["temperature_C"] = float(
            changed_kwargs.get("temperature_C", 1700.0)
        ) + 1.0
        changed_request["kwargs"] = changed_kwargs
    else:
        changed_request["probe_key_change"] = True
    assert engine_cache.engine_cache_key(
        engine_name, result["version"], changed_request
    ) != key
    assert engine_cache.engine_cache_key(
        engine_name, int(result["version"]) + 1, probe_request
    ) != key
    _assert_key_portable_across_working_directories(
        engine_name,
        result["version"],
        probe_request,
        tmp_path / "key-portability",
    )
    _assert_probe_key_ignores_source_changes(
        engine_name,
        result["version"],
        dict(probe_request),
        tmp_path / "source-invariance",
        monkeypatch,
    )
    if engine_name in {"internal-analytical", "alphamelts", "thermoengine"}:
        species = set(result["vapor_pressure_species"])
        assert species, (
            f"{engine_name}: cached probe output has no vapor-pressure leaves"
        )
        if engine_name == "internal-analytical":
            assert {"Fe", "SiO", "Na"} <= species
def test_numeric_probe_change_without_version_bump_fails_guard() -> None:
    version, digest = ENGINE_GOLDEN_PROBES["internal-analytical"]
    changed = "0" * len(digest)
    assert changed != digest
    with pytest.raises(AssertionError, match="bump ENGINE_CHEMISTRY_VERSION"):
        _assert_probe_matches_golden("internal-analytical", version, changed)


def test_real_cold_field_mutation_without_version_bump_fails_guard(
    request, tmp_path
) -> None:
    result = _run_probe("thermoengine", request, tmp_path, mutate_cold=True)
    assert result["mutations"], "cold-field mutation did not reach the real result"
    with pytest.raises(AssertionError, match="bump ENGINE_CHEMISTRY_VERSION"):
        _assert_probe_matches_golden(
            "thermoengine", result["version"], result["digest"]
        )


if __name__ == "__main__":
    engine_name, nodeid, output, mutate = sys.argv[1:]
    try:
        with pytest.MonkeyPatch.context() as patches:
            result = _live_engine_probe(
                engine_name, nodeid, patches, mutate_cold=bool(int(mutate))
            )
    except pytest.skip.Exception as exc:
        result = {"skip": str(exc)}
    except pytest.fail.Exception as exc:
        result = {"fail": str(exc)}
    Path(output).write_text(json.dumps(result))

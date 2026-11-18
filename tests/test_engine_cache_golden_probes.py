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
    "internal-analytical": (2, "f109bcd497f0514fe6151e9bc68630232f5c5739aa22ccbacbbf4c69d584c2ff"),
    "vaporock": (1, "673a8c5ac16725e21c985b585a5320f08bc2bf31f785d9f68e7c1cd3f9753f3b"),
    "magemin": (3, "0e83b5a7f28e74ac77f2f763636e5ed042e450411faf200f9f62e02e357e4497"),
    "alphamelts": (3, "ff6a0568162891d88ef8924d6317eb5bd53c932717c5bde9acdc7fc4feb49c11"),
    "thermoengine": (3, "3db7ad76c46e3130880e2782b235e739a7de72b825180c84fa94e46ec68bfb53"),
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


def _assert_trace_is_keyed(
    engine_name: str,
    observed: set[str],
    callsites: Mapping[str, set[str]],
) -> None:
    from tests.test_engine_cache_input_identity import _EXPECTED_ENGINE_INPUTS

    numeric_reads = _numeric_trace_paths(observed, callsites)
    declared = {
        path
        for path, kind, _selector in _EXPECTED_ENGINE_INPUTS[engine_name]
        if kind == "data"
    }
    unkeyed = numeric_reads - declared
    callsite_detail = {
        path: sorted(callsites.get(path, set())) for path in unkeyed
    }
    assert not unkeyed, (
        f"{engine_name} real-solve file access was not declared in the independent "
        f"input contract: {sorted(unkeyed)}; numeric trace={sorted(numeric_reads)}; "
        f"full trace={sorted(observed)}; "
        f"callsites={callsite_detail}"
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
    from tests.test_engine_cache_input_identity import (
        _EXPECTED_NUMERIC_TRACE_PATHS,
    )

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
    from simulator.engine_cache import _without_engine_cache
    from tests.battery.cache_test_support import open_available_engine

    loaded_pots, _metadata = load_binary_pots()
    pots = {pot.pot_id: pot for pot in loaded_pots}
    cached_values = []
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
        with _without_engine_cache():
            cell = equilibrate_cell(
                handle,
                pots["feo_sio2_40_60"],
                temperature_K=1700.0,
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
            "observed": sorted(traced),
            "callsites": {path: sorted(sites) for path, sites in callsites.items()},
            "mutations": len(mutations),
        }
    finally:
        close_battery_engine_handle(handle)


def _run_probe(
    engine_name, request, tmp_path, *, mutate_cold=False, timeout_s=120.0
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
    _assert_trace_is_keyed(engine_name, observed, callsites)
    _assert_expected_numeric_trace(engine_name, observed, callsites)
    return result


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
    monkeypatch.setenv("DYLD_FALLBACK_LIBRARY_PATH", str(tmp_path / "lib"))
    executable = tmp_path / "context-probe"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "import probe_activation\n"
        "assert probe_activation.activated\n"
        f"assert Path.cwd() == Path({str(tmp_path)!r})\n"
        f"assert os.environ['DYLD_FALLBACK_LIBRARY_PATH'] == {str(tmp_path / 'lib')!r}\n"
        "assert sys.stdin.read() == ''\n"
        "Path(sys.argv[4]).write_text(json.dumps({\n"
        "    'pid': os.getpid(), 'observed': [], 'callsites': {},\n"
        "    'context_inherited': True,\n"
        "}))\n"
    )
    executable.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(executable))
    result = _run_probe("openimcc", request, tmp_path)
    assert result["context_inherited"]


@pytest.mark.parametrize("engine_name", tuple(engine_cache.ENGINE_CHEMISTRY_VERSION))
def test_live_engine_numeric_probe_matches_version(
    engine_name, request, tmp_path
) -> None:
    result = _run_probe(engine_name, request, tmp_path)
    _assert_probe_matches_golden(engine_name, result["version"], result["digest"])


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

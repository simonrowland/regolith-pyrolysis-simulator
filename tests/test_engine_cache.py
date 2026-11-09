from __future__ import annotations

import importlib.util
import inspect
import json
import os
import re
import sqlite3
import subprocess
import sys
import ast
from pathlib import Path
from collections.abc import Mapping

import pytest

from simulator import engine_cache
from simulator.engine_cache import (
    ENGINE_CHEMISTRY_VERSION,
    EngineCache,
    TransientEngineFailure,
    cached_solve,
    key,
)


class _Outcome:
    def __init__(self, value: dict[str, object]) -> None:
        self.value = value
        self.status = str(value.get("status", "ok"))
        self.refusal_reason = (
            None
            if value.get("refusal_reason") is None
            else str(value["refusal_reason"])
        )
        self.engine_status = (
            None
            if value.get("engine_status") is None
            else str(value["engine_status"])
        )
        self.engine_reason = (
            None
            if value.get("engine_reason") is None
            else str(value["engine_reason"])
        )

    def to_cache_payload(self) -> dict[str, object]:
        return dict(self.value)

    @classmethod
    def from_cache_payload(cls, payload: dict[str, object]) -> "_Outcome":
        return cls(dict(payload))


def test_key_is_stable_across_mapping_order_and_process_restart() -> None:
    request = {
        "composition": {"FeO": 12.0, "SiO2": 48.0},
        "temperature_K": 1600.0,
        "oxygen": {"mode": "pO2", "value": 1.0e-8},
    }
    reordered = {
        "oxygen": {"value": 1.0e-8, "mode": "pO2"},
        "temperature_K": 1600.0,
        "composition": {"SiO2": 48.0, "FeO": 12.0},
    }

    expected = key("openimcc", request)
    assert key("openimcc", reordered) == expected

    code = (
        "import json, sys; "
        "from simulator.engine_cache import key; "
        "print(key('openimcc', json.loads(sys.argv[1])))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code, json.dumps(request)],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert completed.stdout.strip() == expected


def test_key_preserves_exact_float_values() -> None:
    left = key("internal-analytical", {"temperature_K": 1.0})
    right = key("internal-analytical", {"temperature_K": 1.0000000000000002})

    assert left != right


def test_key_uses_chemistry_version_and_only_accepts_request_vector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = {"temperature_K": 1700.0}
    before = key("vaporock", request)
    monkeypatch.setitem(
        ENGINE_CHEMISTRY_VERSION,
        "vaporock",
        ENGINE_CHEMISTRY_VERSION["vaporock"] + 1,
    )
    after = key("vaporock", request)

    assert before != after
    assert list(inspect.signature(key).parameters) == [
        "engine_id",
        "request_vector",
    ]
    with pytest.raises(TypeError):
        key("vaporock", request, observation_id="not-an-engine-input")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("engine", "dependency", "call_kind"),
    [
        ("internal-analytical", "openimcc", "equilibrate_cell"),
        ("internal-analytical", "openimcc", "residue_vapor_pressure"),
        ("thermoengine", "vaporock", "evaluate_gas"),
        ("alphamelts", "vaporock", "evaluate_gas"),
    ],
)
def test_declared_dependency_epoch_changes_every_adapter_call_key(
    monkeypatch: pytest.MonkeyPatch,
    engine: str,
    dependency: str,
    call_kind: str,
) -> None:
    request = {"call": call_kind, "temperature_K": 1700.0}
    before = key(engine, request)
    monkeypatch.setitem(
        ENGINE_CHEMISTRY_VERSION,
        dependency,
        ENGINE_CHEMISTRY_VERSION[dependency] + 1,
    )

    assert key(engine, request) != before


def test_undeclared_engine_epoch_does_not_change_an_adapter_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = {"call": "evaluate_gas", "temperature_K": 1700.0}
    before = key("alphamelts", request)
    monkeypatch.setitem(
        ENGINE_CHEMISTRY_VERSION,
        "magemin",
        ENGINE_CHEMISTRY_VERSION["magemin"] + 1,
    )

    assert key("alphamelts", request) == before


def test_adapter_cross_engine_calls_are_declared_dependencies() -> None:
    root = Path(engine_cache.__file__).resolve().parents[1]
    assert set(ENGINE_DEPENDENCY_GUARD_SURFACES) == set(ENGINE_CHEMISTRY_VERSION)
    for engine in ENGINE_DEPENDENCY_GUARD_SURFACES:
        undeclared = _undeclared_adapter_dependencies(engine, root)
        assert not undeclared, f"{engine} uses undeclared engine dependencies: {undeclared}"


ENGINE_DEPENDENCY_GUARD_SURFACES = {
    "openimcc": {
        "modules": (
            ("simulator/melt_backend/openimcc_bridge.py", None),
            ("simulator/melt_backend/openimcc_cache.py", None),
            ("simulator/diagnostic_helpers/binary_pot_battery.py", "_OpenImccBatteryBackend"),
        ),
        "symbols": {
            "OpenImccMeltBackend", "OpenImccBridgeResult",
            "OpenImccCleanedMeltResult", "OpenImccUnavailableError",
            "OpenImccCompositionPolicyRefusal",
            "OpenImccOxygenBalanceUnavailableError",
            "openimcc_bridge", "_OpenImccBatteryBackend",
            "evaluate_cleaned_melt", "evaluate_gas_oxygen_balance",
            "cached_openimcc_evaluate", "cached_openimcc_evaluate_gas",
            "cached_openimcc_redox", "OpenImccEvaluationOutcome",
            "OpenImccGasOutcome", "OpenImccRedoxOutcome",
        },
    },
    "internal-analytical": {
        "modules": (
            ("simulator/diagnostic_helpers/binary_pot_battery.py", "_InternalAnalyticalBatteryBackend"),
            ("engines/builtin/vapor_pressure.py", "_build_high_t_melt_activity_authority"),
        ),
        "symbols": {
            "InternalAnalyticalBackend", "_InternalAnalyticalBatteryBackend",
            "_internal_analytical_vapor_pressure_adapter",
            "_build_high_t_melt_activity_authority",
        },
    },
    "vaporock": {
        "modules": (("simulator/melt_backend/vaporock.py", None),),
        "symbols": {
            "VapoRockBackend", "open_warm_vaporock_backend",
            "eval_gas_abundances", "_call_system_eval_gas_abundances",
        },
    },
    "magemin": {
        "modules": (
            ("simulator/melt_backend/magemin.py", None),
            ("engines/magemin/provider.py", "MAGEMinShadowProvider"),
        ),
        "symbols": {"MAGEMinBackend", "MAGEMinShadowProvider"},
    },
    "alphamelts": {
        "modules": (("simulator/melt_backend/alphamelts.py", None),),
        "symbols": {"AlphaMELTSBackend"},
    },
    "thermoengine": {
        "modules": (("simulator/melt_backend/thermoengine.py", None),),
        "symbols": {"ThermoEngineBackend", "ThermoEngineTransport"},
    },
}


def _undeclared_adapter_dependencies(
    engine: str,
    root: Path,
    *,
    source_overrides: Mapping[str, str] | None = None,
) -> set[str]:
    names: set[str] = set()
    for path, scope_name in ENGINE_DEPENDENCY_GUARD_SURFACES[engine]["modules"]:
        source = (
            source_overrides[path]
            if source_overrides is not None and path in source_overrides
            else (root / path).read_text(encoding="utf-8")
        )
        tree = ast.parse(source, filename=path)
        if scope_name is not None:
            tree = next(
                node
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.name == scope_name
            )
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.ImportFrom):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
    dependencies = engine_cache.ENGINE_CHEMISTRY_DEPENDENCIES[engine]
    symbols = {
        symbol: owner
        for owner, surface in ENGINE_DEPENDENCY_GUARD_SURFACES.items()
        for symbol in surface["symbols"]
    }
    return {
        owner
        for symbol, owner in symbols.items()
        if symbol in names and owner != engine and owner not in dependencies
    }


def test_dependency_guard_detects_injected_undeclared_engine_call() -> None:
    root = Path(engine_cache.__file__).resolve().parents[1]
    adapter_path = "simulator/diagnostic_helpers/binary_pot_battery.py"
    original = (root / adapter_path).read_text(encoding="utf-8")
    injected = original.replace(
        "class _InternalAnalyticalBatteryBackend:",
        "class _InternalAnalyticalBatteryBackend:\n    MAGEMinBackend()  # injected undeclared cross-engine call",
        1,
    )

    assert "magemin" in _undeclared_adapter_dependencies(
        "internal-analytical",
        root,
        source_overrides={adapter_path: injected},
    )


def test_concurrent_process_writes_preserve_same_and_different_keys(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "engine-cache.sqlite3"
    writes = [
        ("same", {"value": 1}),
        ("same", {"value": 1}),
        ("left", {"value": 2}),
        ("right", {"value": 3}),
    ]
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import json, sys; "
                "from simulator.engine_cache import EngineCache; "
                "EngineCache(sys.argv[1]).put(sys.argv[2], json.loads(sys.argv[3]), {'runtime': 'test'})",
                str(db_path),
                key("magemin", {"request": name}),
                json.dumps(payload),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=os.environ.copy(),
        )
        for name, payload in writes
    ]

    for process in processes:
        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, f"stdout={stdout}\nstderr={stderr}"

    connection = sqlite3.connect(db_path)
    try:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        assert journal_mode.lower() == "wal"
    finally:
        connection.close()

    cache = EngineCache(db_path)
    assert cache.get(key("magemin", {"request": "same"})) == {"value": 1}
    assert cache.get(key("magemin", {"request": "left"})) == {"value": 2}
    assert cache.get(key("magemin", {"request": "right"})) == {"value": 3}


def test_cached_solve_skips_transient_failure_and_caches_typed_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = EngineCache(tmp_path / "engine-cache.sqlite3")
    monkeypatch.setattr(
        engine_cache,
        "_from_cache_payload",
        _Outcome.from_cache_payload,
    )
    transient_request = {"temperature_K": 1500.0}

    def fail_transiently() -> _Outcome:
        raise TransientEngineFailure("worker startup failed")

    with pytest.raises(TransientEngineFailure):
        cached_solve(cache, "alphamelts", transient_request, fail_transiently)
    assert cache.get(key("alphamelts", transient_request)) is None

    worker_request = {"temperature_K": 1525.0}
    worker_failure = _Outcome(
        {
            "status": "refusal",
            "refusal_reason": "engine_crash",
            "engine_status": "EngineWorkerDied",
        }
    )
    assert (
        cached_solve(cache, "alphamelts", worker_request, lambda: worker_failure)
        is worker_failure
    )
    assert cache.get(key("alphamelts", worker_request)) is None

    refusal_request = {"temperature_K": 1550.0}
    refusal = _Outcome(
        {"status": "refusal", "refusal_reason": "unsupported_composition"}
    )
    assert cached_solve(cache, "alphamelts", refusal_request, lambda: refusal) is refusal
    hit = cached_solve(
        cache,
        "alphamelts",
        refusal_request,
        lambda: pytest.fail("cached refusal unexpectedly called solve_fn"),
    )

    assert isinstance(hit, _Outcome)
    assert hit.value == refusal.value


def test_isolated_worker_exit_cells_are_transient(
    tmp_path: Path,
) -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        BinaryPot,
        EngineHandle,
        Po2Request,
        _crash_cell_from_returncode,
    )

    cell = _crash_cell_from_returncode(
        handle=EngineHandle(
            name="alphamelts",
            backend=None,
            available=True,
            unavailable_reason=None,
            takes_fo2=True,
            supports_intrinsic_fo2=True,
        ),
        pot=BinaryPot("crashed", None, "test", {"SiO2": 100.0}),
        temperature_K=1700.0,
        po2=Po2Request("engine_default", None),
        wall0=0.0,
        cpu0=0.0,
        hostname="test-host",
        returncode=-9,
        timed_out=False,
        stderr="killed",
        arm="headline",
        notices=(),
        authority=None,
        certified_band=None,
    )
    cache = EngineCache(tmp_path / "crash-cache.sqlite")
    calls = 0

    def crash_again():
        nonlocal calls
        calls += 1
        return cell

    request = {"temperature_K": 1700.0}
    cached_solve(cache, "alphamelts", request, crash_again)
    cached_solve(cache, "alphamelts", request, crash_again)

    assert cell.engine_status == "engine_crash"
    assert calls == 2
    assert cache.counters["alphamelts"]["stores"] == 0
    assert cache.counters["alphamelts"]["transient_skips"] == 2


def test_unavailable_refusal_cells_are_not_cached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = EngineCache(tmp_path / "unavailable-cache.sqlite")
    monkeypatch.setattr(
        engine_cache,
        "_from_cache_payload",
        _Outcome.from_cache_payload,
    )
    outcome = _Outcome(
        {
            "status": "refusal",
            "refusal_reason": "unavailable",
            "engine_status": "ValueError",
            "engine_reason": "fo2_outside_attainable_bracket",
        }
    )
    calls = 0

    def solve_again() -> _Outcome:
        nonlocal calls
        calls += 1
        return outcome

    request = {"call": "equilibrate_cell", "case": "unavailable-refusal"}
    cached_solve(cache, "thermoengine", request, solve_again)
    cached_solve(cache, "thermoengine", request, solve_again)

    assert calls == 2
    assert cache.counters["thermoengine"]["stores"] == 0
    assert cache.counters["thermoengine"]["hits"] == 0
    assert cache.counters["thermoengine"]["transient_skips"] == 2


@pytest.mark.parametrize("disposition", ["environment", "resource", "worker_died"])
def test_lifecycle_failure_dispositions_are_transient(disposition: str) -> None:
    class LifecycleOutcome(_Outcome):
        backend_failure_disposition = disposition

    outcome = LifecycleOutcome({"status": "not_converged"})

    assert engine_cache.is_transient_failure(outcome)


def test_runtime_identity_tripwire_logs_once_and_keeps_the_hit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    cache = EngineCache(tmp_path / "engine-cache.sqlite3")
    monkeypatch.setattr(
        engine_cache,
        "_from_cache_payload",
        _Outcome.from_cache_payload,
    )
    runtime = {"version": "old"}
    monkeypatch.setattr(engine_cache, "runtime_identity", lambda _engine: dict(runtime))
    request = {"pressure_bar": 1.0}
    original = _Outcome({"status": "ok", "value": 7})
    assert cached_solve(cache, "thermoengine", request, lambda: original) is original

    runtime["version"] = "new"
    solve_calls = 0

    def must_not_solve() -> _Outcome:
        nonlocal solve_calls
        solve_calls += 1
        return _Outcome({"status": "wrong"})

    with caplog.at_level("INFO", logger=engine_cache.__name__):
        first_hit = cached_solve(cache, "thermoengine", request, must_not_solve)
        second_hit = cached_solve(cache, "thermoengine", request, must_not_solve)

    notices = [
        record
        for record in caplog.records
        if getattr(record, "notice", {}).get("kind")
        == "engine_cache_runtime_changed"
    ]
    assert first_hit.value == original.value
    assert second_hit.value == original.value
    assert solve_calls == 0
    assert len(notices) == 1
    assert "old" in notices[0].getMessage()
    assert "new" in notices[0].getMessage()
    assert (
        "thermoengine runtime changed (stored {\"version\":\"old\"}, now "
        "{\"version\":\"new\"}). Cached outputs are still used. Bump "
        "ENGINE_CHEMISTRY_VERSION['thermoengine'] ONLY if this change affects "
        "chemistry; leave it for non-chemistry patches."
    ) == notices[0].getMessage()


@pytest.mark.parametrize("change", ["mtime", "path"])
def test_thermoengine_native_library_change_notices_without_changing_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    change: str,
    request: pytest.FixtureRequest,
) -> None:
    request.addfinalizer(engine_cache.runtime_identity.cache_clear)
    first_library = tmp_path / "first" / "libphaseobjc.dylib"
    first_library.parent.mkdir()
    first_library.write_bytes(b"phaseobjc")
    selected_library = [first_library]
    monkeypatch.setattr(
        engine_cache,
        "_thermoengine_native_library_path",
        lambda: selected_library[0],
    )
    engine_cache.runtime_identity.cache_clear()
    monkeypatch.setattr(
        engine_cache,
        "_from_cache_payload",
        _Outcome.from_cache_payload,
    )
    cache = EngineCache(tmp_path / f"thermoengine-{change}.sqlite3")
    request = {"temperature_K": 1700.0}
    unchanged_key = key("thermoengine", request)
    original = _Outcome({"status": "ok", "value": 7})

    assert cached_solve(cache, "thermoengine", request, lambda: original) is original
    stored_identity = engine_cache.runtime_identity("thermoengine")

    if change == "mtime":
        stat = first_library.stat()
        first_library.write_bytes(b"phaseobjc changed")
        os.utime(
            first_library,
            ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000),
        )
    else:
        second_library = tmp_path / "second" / "libphaseobjc.dylib"
        second_library.parent.mkdir()
        second_library.write_bytes(b"phaseobjc")
        selected_library[0] = second_library
    engine_cache.runtime_identity.cache_clear()
    current_identity = engine_cache.runtime_identity("thermoengine")

    with caplog.at_level("INFO", logger=engine_cache.__name__):
        warm = cached_solve(
            cache,
            "thermoengine",
            request,
            lambda: pytest.fail("runtime metadata change must keep the hit"),
        )

    notices = [
        record
        for record in caplog.records
        if getattr(record, "notice", {}).get("kind")
        == "engine_cache_runtime_changed"
    ]
    assert key("thermoengine", request) == unchanged_key
    assert stored_identity["native_library"] != current_identity["native_library"]
    assert warm.value == original.value
    assert len(notices) == 1
    assert (
        notices[0].notice["stored"]["native_library"]
        != notices[0].notice["current"]["native_library"]
    )
    engine_cache.runtime_identity.cache_clear()


def test_cached_solve_tracks_per_engine_work_unit_counters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = EngineCache(tmp_path / "engine-cache.sqlite3")
    monkeypatch.setattr(
        engine_cache,
        "_from_cache_payload",
        _Outcome.from_cache_payload,
    )
    request = {"oxygen": {"mode": "intrinsic"}}

    cached_solve(cache, "internal-analytical", request, lambda: _Outcome({"n": 1}))
    cached_solve(cache, "internal-analytical", request, lambda: pytest.fail("expected hit"))

    with pytest.raises(TransientEngineFailure):
        cached_solve(
            cache,
            "internal-analytical",
            {"oxygen": {"mode": "fO2"}},
            lambda: (_ for _ in ()).throw(TransientEngineFailure("resource error")),
        )
    refusal = _Outcome({"status": "refusal", "refusal_reason": "no_liquid"})
    cached_solve(
        cache,
        "internal-analytical",
        {"oxygen": {"mode": "pO2"}},
        lambda: refusal,
    )

    assert cache.counters["internal-analytical"] == {
        "hits": 1,
        "misses": 3,
        "stores": 2,
        "transient_skips": 1,
        "calls_by_kind": {
            "equilibrate_cell": 3,
            "evaluate": 0,
            "evaluate_redox": 0,
            "evaluate_gas": 0,
            "residue_vapor_pressure": 0,
        },
    }
    assert cache.counters["openimcc"] == {
        "hits": 0,
        "misses": 0,
        "stores": 0,
        "transient_skips": 0,
        "calls_by_kind": {
            "equilibrate_cell": 0,
            "evaluate": 0,
            "evaluate_redox": 0,
            "evaluate_gas": 0,
            "residue_vapor_pressure": 0,
        },
    }


def test_runtime_identity_is_memoized_per_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine_cache.runtime_identity.cache_clear()
    original_find_spec = engine_cache.importlib.util.find_spec
    calls = 0

    def counted_find_spec(module_name: str):
        nonlocal calls
        calls += 1
        return original_find_spec(module_name)

    monkeypatch.setattr(engine_cache.importlib.util, "find_spec", counted_find_spec)
    try:
        first = engine_cache.runtime_identity("internal-analytical")
        second = engine_cache.runtime_identity("internal-analytical")
    finally:
        engine_cache.runtime_identity.cache_clear()

    assert first == second
    assert calls == 1


def test_every_chemistry_version_has_a_changelog_entry() -> None:
    source = Path(engine_cache.__file__).read_text(encoding="utf-8")
    entries = {
        (engine, int(version))
        for engine, version in re.findall(
            r"^\s*#\s*(\S+) v(\d+):", source, re.MULTILINE
        )
    }
    assert all(
        (engine, version) in entries
        for engine, version in ENGINE_CHEMISTRY_VERSION.items()
    )


@pytest.mark.parametrize(
    ("engine_id", "dependency"),
    [
        (engine_id, dependency)
        for engine_id, dependencies in (
            engine_cache.ENGINE_CHEMISTRY_DATA_DEPENDENCIES.items()
        )
        for dependency in dependencies
    ],
)
def test_chemistry_data_dependency_names_a_real_path(
    engine_id: str, dependency: str
) -> None:
    kind, reference = dependency.split(":", 1)
    if kind == "repo":
        path = Path(engine_cache.__file__).resolve().parents[1] / reference
    elif kind == "package":
        package, relative = reference.split("/", 1)
        spec = importlib.util.find_spec(package)
        if spec is None:
            pytest.skip(f"{engine_id} data package {package!r} is not installed")
        roots = tuple(spec.submodule_search_locations or ())
        if not roots and spec.origin:
            roots = (str(Path(spec.origin).parent),)
        path = next(
            (Path(root) / relative for root in roots if (Path(root) / relative).exists()),
            Path(roots[0]) / relative if roots else Path("__missing_package_path__"),
        )
    elif kind == "engine":
        from simulator.engine_local_config import (
            find_alphamelts_binary,
            resolve_magemin_binary_path,
            setup_thermoengine_dylib_path,
        )

        if reference == "magemin/ref_database":
            binary = resolve_magemin_binary_path()
            if binary is None:
                pytest.skip("MAGEMin database runtime binary is not installed")
            path = binary.resolve().parent / "ref_database"
        elif reference == "alphamelts/model":
            binary = find_alphamelts_binary()
            if binary is None:
                pytest.skip("AlphaMELTS model runtime binary is not installed")
            path = binary
        elif reference == "thermoengine/native-libraries":
            try:
                path = setup_thermoengine_dylib_path()
            except ImportError as exc:
                pytest.skip(f"ThermoEngine native data is not installed: {exc}")
        else:
            pytest.fail(f"unknown engine data dependency {reference!r}")
    else:
        pytest.fail(f"unknown engine data dependency kind {kind!r}")

    assert path.exists(), (
        f"{engine_id} data dependency {dependency!r} does not resolve: {path}"
    )

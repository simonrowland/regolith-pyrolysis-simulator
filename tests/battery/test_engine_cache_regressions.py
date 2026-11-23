"""Cache seams must preserve live predictions and retry transient failures."""

import sqlite3
from dataclasses import replace
from threading import Lock
from types import SimpleNamespace

import pytest

from simulator import engine_cache
from simulator.diagnostic_helpers import binary_pot_battery as battery


@pytest.mark.parametrize("engine", tuple(engine_cache.ENGINE_CHEMISTRY_VERSION))
def test_every_normalized_prediction_setting_changes_the_key(engine):
    normalized = battery.normalize_engine_settings(engine, None)
    settings = engine_cache.engine_cache_settings(engine, normalized)
    original = engine_cache.engine_cache_key(engine, engine_cache.ENGINE_CHEMISTRY_VERSION[engine], {"engine_settings": settings})
    for name, value in settings.items():
        changed = {**settings, name: {"pack": "changed"} if isinstance(value, dict) else [value, "changed"]}
        assert engine_cache.engine_cache_key(engine, engine_cache.ENGINE_CHEMISTRY_VERSION[engine], {"engine_settings": changed}) != original, name


@pytest.mark.parametrize("engine", tuple(engine_cache.ENGINE_CHEMISTRY_VERSION))
def test_isolated_request_keys_cover_all_consumed_physics_inputs(engine, monkeypatch):
    keys = []

    def capture(name, request, solve):
        keys.append(engine_cache.engine_cache_key(name, engine_cache.ENGINE_CHEMISTRY_VERSION[name], request))
        return {"status": "refusal", "refusal_reason": "unavailable"}

    monkeypatch.setattr(engine_cache, "cached_engine_call", capture)
    worker = object.__new__(battery._IsolatedCellWorker)
    worker.engine = engine
    worker.engine_settings = battery.normalize_engine_settings(engine, None)
    request = {
        "temperature_K": 1700.0, "composition_wt_pct": {"SiO2": 60.0, "MgO": 40.0},
        "physical_pressure_bar": 1e-6,
        "po2": {"mode": "commanded", "po2_bar": 1e-8, "cell_material": "W"},
    }
    worker.request(request, timeout_s=5)
    for name, changed in (
        ("temperature_K", 1701.0),
        ("composition_wt_pct", {"SiO2": 61.0, "MgO": 39.0}),
        ("physical_pressure_bar", 1e-5),
        ("po2", {**request["po2"], "po2_bar": 1e-7}),
        ("po2", {**request["po2"], "mode": "oxygen_balance_effusion"}),
        ("po2", {**request["po2"], "cell_material": "Mo"}),
    ):
        worker.request({**request, name: changed}, timeout_s=5)
        assert keys[-1] != keys[0], (engine, name)


def test_openimcc_isolated_cell_rejects_legacy_incomplete_gas_map(tmp_path, monkeypatch):
    from simulator.battery.score import _sum_gas_partial_pressures_pa

    worker = object.__new__(battery._IsolatedCellWorker)
    worker.engine = "openimcc"
    worker.engine_settings = battery.normalize_engine_settings(worker.engine, None)
    worker._lock = Lock()
    oxygen = battery.Po2Request(battery.PO2_COMMANDED, 1e-8)
    request = {
        "composition_wt_pct": {"SiO2": 60.0, "MgO": 40.0},
        "temperature_K": 1700.0,
        "physical_pressure_bar": 1e-6,
        "po2": oxygen.as_payload(),
    }
    legacy = battery.EquilibrateCell(
        pot_id="legacy", engine=worker.engine, temperature_K=1700.0, po2=oxygen,
        status="ok", refusal_reason=None, engine_status="ok", engine_reason=None,
        melt_activities={}, gas_partial_pressures_Pa={"Na": 2.0}, liquid_fraction=1.0,
        wall_s=0.0, cpu_s=0.0, hostname="test",
    )
    calls = []

    def solve(payload, *, timeout_s):
        calls.append((payload, timeout_s))
        return replace(legacy, invalid_gas_partial_pressure_components=("SiO",)).as_payload()

    worker._worker = SimpleNamespace(call=solve)
    cache_path = str(tmp_path / "openimcc.sqlite")
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", cache_path)
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_MODE", "readonly")
    version_one = engine_cache.engine_cache_key(worker.engine, 1, {
        **request,
        "engine_settings": engine_cache.engine_cache_settings(worker.engine, worker.engine_settings),
    })
    with engine_cache._without_engine_cache():
        cold = worker.request(request, timeout_s=5)
    engine_cache._prepare_memory_cache()
    monkeypatch.setitem(engine_cache._MEMORY_CACHE, (cache_path, version_one, True),
                        engine_cache.live_hot(legacy.to_hot()))
    warm = worker.request(request, timeout_s=5)

    def admissible_sum(payload):
        if payload["invalid_gas_partial_pressure_components"]:
            return None
        return _sum_gas_partial_pressures_pa(payload["gas_partial_pressures_Pa"])

    assert _sum_gas_partial_pressures_pa(legacy.gas_partial_pressures_Pa) == 2.0
    assert warm["invalid_gas_partial_pressure_components"] == cold["invalid_gas_partial_pressure_components"] == ["SiO"]
    assert admissible_sum(warm) is admissible_sum(cold) is None
    assert len(calls) == 2


@pytest.mark.parametrize("seam", ["inline", "isolated"])
def test_analytical_oxygen_pack_changes_each_key(seam, tmp_path, monkeypatch):
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    seen = []

    def capture(engine, request, solve):
        seen.append(engine_cache.engine_cache_key(engine, 1, request))
        return {"status": "refusal", "refusal_reason": "unavailable"}

    monkeypatch.setattr(engine_cache, "cached_engine_call", capture)
    for pack in ("default", "non-default-oxygen-pack"):
        settings = battery.normalize_engine_settings(
            "internal-analytical", {"oxygen_balance_backend": {"pack": pack}},
        )
        if seam == "inline":
            backend = battery._open_resolved_backend("internal-analytical", settings)
            handle = SimpleNamespace(name="internal-analytical", engine_settings=settings, backend=backend)
            battery._call_cached_backend_equilibrate(
                handle, {"po2_request": battery.Po2Request(battery.PO2_OXYGEN_BALANCE_EFFUSION, None)},
                lambda: pytest.fail("key capture must not solve"),
            )
        else:
            worker = object.__new__(battery._IsolatedCellWorker)
            worker.engine = "internal-analytical"
            worker.engine_settings = settings
            worker.request({"temperature_K": 1700.0, "po2": {"mode": battery.PO2_OXYGEN_BALANCE_EFFUSION}}, timeout_s=5)
    assert seen[0] != seen[1]


def test_analytical_settings_use_the_consumed_pack_default():
    from simulator.melt_backend.openimcc_bridge import DEFAULT_BACKEND_PACK_NAME

    projections = []
    for raw in (None, {}, {"oxygen_balance_backend": {}}, {"oxygen_balance_backend": {"pack": None}}, {"oxygen_balance_backend": {"pack": ""}}):
        settings = battery.normalize_engine_settings("internal-analytical", raw)
        backend = battery._open_resolved_backend("internal-analytical", settings)
        assert backend._oxygen_balance_pack == DEFAULT_BACKEND_PACK_NAME
        projections.append(engine_cache.engine_cache_settings("internal-analytical", settings))
    assert all(value == projections[0] for value in projections)


def test_empty_residue_surface_survives_off_cold_and_disk_hit(tmp_path, monkeypatch):
    request = dict(
        temperature_C=100.0, pressure_bar=0.0, composition_kg=None,
        composition_mol={"SiO2": 1.0}, fO2_log=0.0,
        po2_request=battery.Po2Request(battery.PO2_COMMANDED, 1.0),
        include_diagnostic_shadows=False,
    )
    core = battery._new_internal_analytical_core()
    expected = battery._internal_analytical_vapor_pressure_adapter(core=core, **request)
    live = battery.internal_analytical_residue_vapor_pressure(core=core, **request)
    assert vars(live) == vars(expected)
    path = tmp_path / "cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    cold = battery.internal_analytical_residue_vapor_pressure(core=core, **request)
    engine_cache._MEMORY_CACHE.clear()
    monkeypatch.setattr(battery, "_internal_analytical_vapor_pressure_adapter", lambda **kwargs: pytest.fail("disk hit must not solve"))
    warm = battery.internal_analytical_residue_vapor_pressure(core=core, **request)
    for result in (live, cold, warm):
        assert result.status == "ok"
        assert result.vapor_pressures_Pa == {}
        assert result.vapor_pressures_source == {}
        assert result.liquid_fraction is None
        assert result.phase_assemblage_available is False
        assert result.vapor_pressures_Pa.get("SiO") is None


@pytest.mark.parametrize("seam", ["inline", "residue", "isolated"])
@pytest.mark.parametrize("clear_ram", [False, True], ids=["ram", "disk"])
def test_nonconvergence_then_recovery_is_never_masked(seam, clear_ram, tmp_path, monkeypatch):
    from engines.builtin import vapor_pressure
    from simulator.melt_backend import openimcc_bridge
    from tests.chemistry.test_internal_analytical_stateless import _COMPOSITION_MOL

    core_backend = battery._InternalAnalyticalBatteryBackend()
    request = dict(
        temperature_C=2400.65 - 273.15, pressure_bar=1e-6,
        composition_kg=None, composition_mol=_COMPOSITION_MOL, fO2_log=-9.0,
        po2_request=battery.Po2Request(battery.PO2_COMMANDED, 1e-9),
    )
    worker = object.__new__(battery._IsolatedCellWorker)
    worker.engine = "internal-analytical"
    worker.engine_settings = battery.normalize_engine_settings(worker.engine, None)
    worker._lock = Lock()
    handle = SimpleNamespace(name=worker.engine, backend=core_backend, engine_settings=worker.engine_settings)
    def solve():
        return core_backend.equilibrate(**request)

    real_handle = battery.EngineHandle(
        name=worker.engine, backend=core_backend, available=True, unavailable_reason=None,
        takes_fo2=True, supports_intrinsic_fo2=True, engine_settings=worker.engine_settings,
    )

    def cell_payload(payload, *, timeout_s):
        return battery.run_isolated_cell_worker(payload, handle=real_handle)

    worker._worker = SimpleNamespace(call=cell_payload)

    def evaluate():
        if seam == "residue":
            return battery.internal_analytical_residue_vapor_pressure(
                core=core_backend._core, **request, include_diagnostic_shadows=False,
            ).vapor_pressures_Pa
        if seam == "inline":
            return battery._call_cached_backend_equilibrate(handle, request, solve).vapor_pressures_Pa
        payload = {
            "engine": worker.engine, "pot_id": "recovery",
            "temperature_K": 2400.65, "composition_wt_pct": {"MgO": 40.0, "SiO2": 60.0},
            "physical_pressure_bar": 1e-6, "po2": request["po2_request"].as_payload(),
        }
        return worker.request(payload, timeout_s=30)["gas_partial_pressures_Pa"]

    live = evaluate()
    path = tmp_path / "cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    engine_cache.engine_cache_counters(reset=True)
    with monkeypatch.context() as scoped:
        def refuse(**kwargs):
            from openimcc import ImccNonconvergenceError

            raise ImccNonconvergenceError("temporary cache regression failure", {})
        scoped.setattr(openimcc_bridge, "evaluate", refuse)
        scoped.setattr(vapor_pressure, "_same_composition_temperature_bridge", lambda *args, **kwargs: None)
        fallback = evaluate()
        assert fallback != live
        assert core_backend._core._last_high_t_melt_activity["fallback_reason"]["code"] == "imcc_nonconvergence"
        counts = engine_cache.engine_cache_counters()["internal-analytical"]
        assert counts["stores"] == counts["hits"] == 0
        assert not any(key[0] == str(path) for key in engine_cache._MEMORY_CACHE)
        if path.exists():
            with sqlite3.connect(path) as connection:
                assert connection.execute("SELECT count(*) FROM engine_outputs").fetchone()[0] == 0
    if clear_ram:
        engine_cache._MEMORY_CACHE.clear()
    assert evaluate() == live
    assert engine_cache.engine_cache_counters()["internal-analytical"]["stores"] == 1
    engine_cache._MEMORY_CACHE.clear()
    monkeypatch.setattr(core_backend, "equilibrate", lambda **kwargs: pytest.fail("recovery must be stored"))
    monkeypatch.setattr(battery, "_internal_analytical_vapor_pressure_adapter", lambda **kwargs: pytest.fail("recovery must be stored"))
    assert evaluate() == live


def test_inherited_cache_is_neither_read_nor_written_by_fake_worker(tmp_path, monkeypatch):
    import conftest

    path = tmp_path / "inherited.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    engine_cache.cached_engine_call("internal-analytical", {}, lambda: {"pressure": 1.0})
    with sqlite3.connect(path) as connection:
        before = connection.execute("SELECT * FROM engine_outputs").fetchall()
    conftest._box_for_test_owned_engine_caches.__wrapped__(monkeypatch)
    calls = []

    def fake():
        calls.append(1)
        return {"pressure": 12345.0}

    assert engine_cache.cached_engine_call("internal-analytical", {}, fake) == {"pressure": 12345.0}
    assert engine_cache.cached_engine_call("internal-analytical", {"other": True}, fake) == {"pressure": 12345.0}
    assert len(calls) == 2
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT * FROM engine_outputs").fetchall() == before


@pytest.mark.parametrize("seam", ["inline", "isolated"])
def test_mutation_dropped_oxygen_pack_is_detected(seam, tmp_path, monkeypatch):
    original = engine_cache.engine_cache_settings

    def omit_pack(engine, settings):
        result = original(engine, settings)
        if engine == "internal-analytical":
            result.pop("oxygen_balance_backend")
        return result

    monkeypatch.setattr(engine_cache, "engine_cache_settings", omit_pack)
    with pytest.raises(AssertionError):
        test_analytical_oxygen_pack_changes_each_key(seam, tmp_path, monkeypatch)


@pytest.mark.parametrize("seam", ["inline", "residue", "isolated"])
def test_mutation_dropped_transient_disposition_is_detected(seam, tmp_path, monkeypatch):
    original = battery._internal_analytical_vapor_pressure_adapter

    def forget_failure(**kwargs):
        result = original(**kwargs)
        result.diagnostics.pop("backend_failure_disposition", None)
        return result

    monkeypatch.setattr(battery, "_internal_analytical_vapor_pressure_adapter", forget_failure)
    with pytest.raises(AssertionError):
        test_nonconvergence_then_recovery_is_never_masked(seam, False, tmp_path, monkeypatch)


def test_mutation_dropped_empty_pressure_field_is_detected(tmp_path, monkeypatch):
    from simulator.melt_backend import base

    original = base.equilibrium_cache_from_value

    def lose_empty(payload, **kwargs):
        result = original(payload, **kwargs)
        if not result.get("vapor_pressures_Pa"):
            result.pop("vapor_pressures_Pa", None)
        return result

    monkeypatch.setattr(base, "equilibrium_cache_from_value", lose_empty)
    with pytest.raises(AttributeError):
        test_empty_residue_surface_survives_off_cold_and_disk_hit(tmp_path, monkeypatch)


@pytest.mark.parametrize("engine", ["internal-analytical", "alphamelts", "magemin", "thermoengine", "vaporock"])
def test_inline_request_keys_cover_every_forwarded_solve_input(engine, tmp_path, monkeypatch):
    import inspect

    keys = []

    def capture(name, request, solve):
        keys.append(engine_cache.engine_cache_key(name, engine_cache.ENGINE_CHEMISTRY_VERSION[name], request))
        return None

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    monkeypatch.setattr(engine_cache, "cached_engine_call", capture)
    handle = battery.open_battery_engine(engine)
    assert handle.available, handle.unavailable_reason
    kwargs = {
        "temperature_C": 1426.85, "composition_mol": {"SiO2": 1.0},
        "composition_kg": None, "pressure_bar": 1e-6, "fO2_log": -9.0,
    }
    try:
        battery._call_cached_backend_equilibrate(handle, kwargs, lambda: pytest.fail("key capture must not solve"))
        for name, changed in (
            ("temperature_C", 1427.85), ("composition_mol", {"SiO2": 2.0}),
            ("composition_kg", {"SiO2": 1.0}), ("pressure_bar", 1e-5),
            ("fO2_log", -8.0),
            ("po2_request", battery.Po2Request("commanded", 1e-8)),
            ("redox_mode", "not_an_input"),
            ("vapor_transport_pO2_bar", 1e-8),
            ("vapor_transport_floor_bar", 1e-12),
            ("vapor_transport_pO2_admission", "observation"),
            ("native_activity_formula", "MgO"),
        ):
            if name not in inspect.signature(handle.backend.equilibrate).parameters:
                continue
            battery._call_cached_backend_equilibrate(handle, {**kwargs, name: changed}, lambda: None)
            if engine == "internal-analytical" and name == "fO2_log":
                assert keys[-1] == keys[0]
            else:
                assert keys[-1] != keys[0], (engine, name)
        battery._call_cached_backend_equilibrate(handle, {**kwargs, "call_timeout_s": 5}, lambda: None)
        assert keys[-1] == keys[0]
    finally:
        battery.close_battery_engine_handle(handle)


def test_direct_openimcc_key_covers_every_bridge_input():
    from simulator.melt_backend.openimcc_bridge import _evaluate_request_vector

    kwargs = {"composition_mol": {"SiO2": 1.0}, "temperature_K": 1700.0}
    original = engine_cache.engine_cache_key("openimcc", 1, _evaluate_request_vector(**kwargs))
    for name, changed in (
        ("composition_mol", {"SiO2": 2.0}),
        ("composition_kg", {"SiO2": 1.0}), ("temperature_K", 1701.0),
        ("pack", "published-core"), ("allow_extrapolation", True),
        ("allow_out_of_envelope", True),
    ):
        request = _evaluate_request_vector(**{**kwargs, name: changed})
        assert engine_cache.engine_cache_key("openimcc", 1, request) != original, name


def test_analytical_fo2_argument_is_not_consumed():
    import ast
    import inspect
    import textwrap
    from tests.chemistry.test_internal_analytical_stateless import _COMPOSITION_MOL

    function = ast.parse(textwrap.dedent(inspect.getsource(battery._internal_analytical_vapor_pressure_adapter)))
    assert not any(isinstance(node, ast.Name) and node.id == "fO2_log" and isinstance(node.ctx, ast.Load) for node in ast.walk(function))
    backend = battery._InternalAnalyticalBatteryBackend()
    kwargs = dict(
        temperature_C=2400.65 - 273.15, pressure_bar=1e-6,
        composition_mol=_COMPOSITION_MOL,
        po2_request=battery.Po2Request("commanded", 1e-9),
    )
    assert vars(backend.equilibrate(**kwargs, fO2_log=-9.0)) == vars(backend.equilibrate(**kwargs, fO2_log=-8.0))

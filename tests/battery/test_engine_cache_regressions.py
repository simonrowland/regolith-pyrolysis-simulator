"""Cache seams must preserve live predictions and retry transient failures."""

import sqlite3
from threading import Lock
from types import SimpleNamespace

import pytest

from simulator import engine_cache
from simulator.diagnostic_helpers import binary_pot_battery as battery


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
    calls = []

    def solve():
        calls.append(1)
        return core_backend.equilibrate(**request)

    def cell_payload(_payload, *, timeout_s):
        result = solve()
        return battery.EquilibrateCell(
            pot_id="recovery", engine=worker.engine, temperature_K=2400.65,
            po2=request["po2_request"], status=result.status, refusal_reason=None,
            engine_status=result.status, engine_reason=None,
            melt_activities=result.activity_coefficients,
            gas_partial_pressures_Pa=result.vapor_pressures_Pa,
            liquid_fraction=result.liquid_fraction, wall_s=0.0, cpu_s=0.0, hostname="test",
            backend_failure_disposition=result.diagnostics.get("backend_failure_disposition"),
        ).as_payload()

    worker._worker = SimpleNamespace(call=cell_payload)

    def evaluate():
        if seam == "residue":
            return battery.internal_analytical_residue_vapor_pressure(
                core=core_backend._core, **request, include_diagnostic_shadows=False,
            ).vapor_pressures_Pa
        if seam == "inline":
            return battery._call_cached_backend_equilibrate(handle, request, solve).vapor_pressures_Pa
        payload = {
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


from __future__ import annotations

import os
import hashlib
import json
import copy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from simulator.diagnostic_helpers.binary_pot_battery import (
    EquilibrateCell,
    BinaryPot,
    EngineHandle,
    Po2Request,
    _engine_cache_request_vector,
    close_battery_engine_handle,
    equilibrate_cell,
    open_battery_engine,
)
import simulator.diagnostic_helpers.binary_pot_battery as battery
from simulator.engine_cache import ENGINE_CHEMISTRY_VERSION, EngineCache


class _Backend:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0
        self._mode = "python_api"
        self._model = "model-a"
        self._raw_model_name = "model-a"
        self._redox_buffer = "QFM"
        self._fo2_offset = 0.0
        self._fe3fet_ratio = None
        self._python_api_model = ("model-a", 1)
        self._bridge = "subprocess"
        self._database = "ig"
        self._config = {"fO2_buffer": None}
        self._binary_path = None
        self._pack_name = "default"
        self._oxygen_balance_pack = "default"
        self._allow_extrapolation = True
        self._allow_out_of_envelope = True
        self._temperature_units = "C"
        self._pressure_units = "bar"
        self._vapor_pressure_units = "bar"
        self._vapor_transport_floor_bar = 1.0e-12
        self._configured_vapor_transport_floor_bar = 1.0e-12
        self._configured_vapor_transport_pO2_bar = 1.0e-9
        self._vapor_transport_pO2_admission = "environment"

    def equilibrate(self, **_kwargs: Any) -> Any:
        self.calls += 1
        return SimpleNamespace(
            status="ok",
            diagnostics={},
            warnings=[],
            activity_coefficients={"SiO2": 0.5},
            vapor_pressures_Pa={},
            liquid_fraction=0.9,
            phase_assemblage_available=True,
        )

    @staticmethod
    def _pressure_bar_to_GPa(value: float) -> float:
        return value / 10_000.0

    @staticmethod
    def _GPa_to_kbar(value: float) -> float:
        return value * 10.0


def _handle(name: str, backend: _Backend | None = None) -> EngineHandle:
    backend = backend or _Backend(name)
    if name == "openimcc":
        from simulator.melt_backend import openimcc_bridge

        backend._bridge = openimcc_bridge
    return EngineHandle(
        name=name,
        backend=backend,
        available=True,
        unavailable_reason=None,
        takes_fo2=True,
        supports_intrinsic_fo2=True,
    )


def _pot(pot_id: str = "cache-key-pot") -> BinaryPot:
    return BinaryPot(
        pot_id=pot_id,
        kato_1993_table4_system=None,
        why="cache request vector fixture",
        composition_wt_pct={"SiO2": 50.0, "Al2O3": 50.0},
    )


def _vector(
    handle: EngineHandle,
    *,
    pressure_bar: float = 1.0,
    po2: Po2Request | None = None,
) -> dict[str, Any]:
    value = _engine_cache_request_vector(
        handle,
        _pot(),
        temperature_K=1703.0,
        po2=po2 or Po2Request("engine_default", None),
        physical_pressure_bar=pressure_bar,
        native_activity_formula=None,
    )
    assert value is not None
    return value


def _path_value(value: Any, path: tuple[str, ...]) -> Any:
    current = value
    for part in path:
        current = current.get(part) if isinstance(current, dict) else getattr(current, part)
    return current


def _set_path_value(value: Any, path: tuple[str, ...], replacement: Any) -> None:
    parent = _path_value(value, path[:-1])
    key = path[-1]
    if isinstance(parent, dict):
        parent[key] = replacement
    else:
        setattr(parent, key, replacement)


_INITIALIZED_SETTING_MUTATIONS: dict[str, tuple[tuple[Any, ...], ...]] = {
    "openimcc": (("_pack_name", ("pack",), "review-cache-pack", "review-cache-pack-2"),),
    "internal-analytical": (("_oxygen_balance_pack", ("oxygen_balance_backend", "pack"), "review-parent-pack", "review-parent-pack-2"),),
    "alphamelts": (
        ("_model", ("model",), "review-cache-model", "review-cache-model-2"),
        ("_mode", ("transport",), "python_api", "subprocess"),
        ("_python_api_model", ("python_api_model",), ("review-model", 2), ("review-model-2", 3)),
        ("_redox_buffer", ("redox_buffer",), "NNO", "QFM"),
        ("_fo2_offset", ("fO2_offset",), 0.25, -0.25),
        ("_fe3fet_ratio", ("Fe3Fet_ratio",), 0.25, 0.75),
        ("_configured_vapor_transport_pO2_bar", ("configured_vapor_transport_pO2_bar",), 1.0e-8, 1.0e-7),
        ("_configured_vapor_transport_floor_bar", ("configured_vapor_transport_floor_bar",), 1.0e-10, 1.0e-11),
        ("_raw_model_name", ("raw_model_name",), "review-raw-model", "review-raw-model-2"),
    ),
    "thermoengine": (
        ("_model", ("model",), "review-cache-model", "review-cache-model-2"),
        ("_mode", ("transport",), "review-cache-transport", "review-cache-transport-2"),
        ("_configured_vapor_transport_pO2_bar", ("configured_vapor_transport_pO2_bar",), 1.0e-8, 1.0e-7),
        ("_configured_vapor_transport_floor_bar", ("configured_vapor_transport_floor_bar",), 1.0e-10, 1.0e-11),
    ),
    "vaporock": (
        ("_temperature_units", ("temperature_units",), "K", "C"),
        ("_pressure_units", ("pressure_units",), "Pa", "bar"),
        ("_vapor_pressure_units", ("vapor_pressure_units",), "Pa", "bar"),
    ),
    "magemin": (
        ("_database", ("database",), "mp", "ig"),
        (("_config", "fO2_buffer"), ("fO2_buffer",), "nno", "qfm"),
        ("_bridge", ("bridge",), "ctypes", "subprocess"),
    ),
}


def _initialized_effective_setting(
    engine: str,
    backend: Any,
    key_path: tuple[str, ...],
    worker_settings: dict[str, Any],
) -> Any:
    key = key_path[-1]
    if engine == "internal-analytical" and key_path == (
        "oxygen_balance_backend",
        "pack",
    ):
        return backend._oxygen_balance_backend._pack_name
    if engine == "thermoengine" and key == "model":
        return backend._thermoengine_transport._model_name
    if engine == "alphamelts" and key == "python_api_model":
        if backend._mode != "python_api":
            return None
        worker = getattr(backend, "_pet_worker", None)
        if worker is not None:
            return (backend._model, int(worker._bootstrap_args[0]))
        return backend._python_api_model
    if engine == "alphamelts" and key == "raw_model_name":
        return backend._model
    if engine == "vaporock" and key == "vapor_pressure_units":
        pool = getattr(backend, "_warm_pool", None)
        if pool is not None:
            return pool.workers[0]._bootstrap_args[1]
    if engine == "magemin" and key in {"database", "fO2_buffer"}:
        return worker_settings.get(key)
    attributes = {
        "openimcc": {"pack": "_pack_name"},
        "internal-analytical": {},
        "alphamelts": {
            "model": "_model",
            "transport": "_mode",
            "python_api_model": "_python_api_model",
            "redox_buffer": "_redox_buffer",
            "fO2_offset": "_fo2_offset",
            "Fe3Fet_ratio": "_fe3fet_ratio",
            "configured_vapor_transport_pO2_bar": "_configured_vapor_transport_pO2_bar",
            "configured_vapor_transport_floor_bar": "_configured_vapor_transport_floor_bar",
        },
        "thermoengine": {
            "transport": "_mode",
            "configured_vapor_transport_pO2_bar": "_configured_vapor_transport_pO2_bar",
            "configured_vapor_transport_floor_bar": "_configured_vapor_transport_floor_bar",
        },
        "vaporock": {
            "temperature_units": "_temperature_units",
            "pressure_units": "_pressure_units",
            "vapor_pressure_units": "_vapor_pressure_units",
        },
        "magemin": {"bridge": "_bridge"},
    }
    return getattr(backend, attributes[engine][key])


def _setting_marker(value: Any) -> float:
    payload = json.dumps(value, sort_keys=True, default=str).encode()
    return 1.0 + int(hashlib.sha256(payload).hexdigest()[:8], 16) / 0xFFFFFFFF


def _cell_for_marker(engine: str, po2: Po2Request, marker: float) -> EquilibrateCell:
    return EquilibrateCell(
        pot_id="cache-key-pot",
        engine=engine,
        temperature_K=1703.0,
        po2=po2,
        status="ok",
        refusal_reason=None,
        engine_status="ok",
        engine_reason=None,
        melt_activities={},
        gas_partial_pressures_Pa={"SiO2": marker},
        liquid_fraction=0.5,
        wall_s=0.0,
        cpu_s=0.0,
        hostname="fixture",
    )


@pytest.mark.parametrize(
    "engine",
    (
        "openimcc",
        "internal-analytical",
        "alphamelts",
        "thermoengine",
        "vaporock",
        "magemin",
    ),
)
def test_initialized_adapter_parent_setting_mutations_match_direct_and_isolated(
    engine: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle = open_battery_engine(engine)
    if not handle.available or handle.backend is None:
        close_battery_engine_handle(handle)
        pytest.skip(handle.unavailable_reason or f"{engine} unavailable")
    po2 = (
        Po2Request("oxygen_balance_effusion", None, "W")
        if engine == "internal-analytical"
        else Po2Request("engine_default", None)
    )
    backend = handle.backend
    original_alpha_state: tuple[Any, Any, Any] | None = None
    if engine == "alphamelts":
        # Exercise the warm Python API path even when the installed adapter
        # selected its subprocess transport. The fake worker holds the model
        # code captured at initialization, matching WarmEngineWorker's
        # bootstrap contract without constructing native engine state.
        original_alpha_state = (
            backend._mode,
            backend._python_api_model,
            backend._pet_worker,
        )
        from simulator.melt_backend.alphamelts import resolve_alphamelts_python_api_model

        python_api_model = backend._python_api_model or resolve_alphamelts_python_api_model(
            backend._model
        )
        backend._mode = "python_api"
        backend._python_api_model = python_api_model
        if backend._pet_worker is None:
            backend._pet_worker = SimpleNamespace(
                _bootstrap_args=(python_api_model[1],),
                close=lambda: None,
            )
    worker_settings: dict[str, Any] = {}
    if engine == "magemin" and backend._subprocess_pool is not None:
        args = backend._subprocess_pool.workers[0]._bootstrap_args
        worker_settings = {
            "database": str(args[1]),
            "fO2_buffer": copy.deepcopy(args[2]).get("fO2_buffer"),
        }
    baseline = battery._engine_cache_request_vector(
        handle,
        _pot(),
        temperature_K=1703.0,
        po2=po2,
        physical_pressure_bar=1.0,
        native_activity_formula=None,
    )
    assert baseline is not None
    if engine == "internal-analytical":
        assert backend._oxygen_balance_backend is not None

    active_path: list[tuple[str, ...]] = [("model",)]

    def direct_equilibrate(**_kwargs: Any) -> Any:
        value = _initialized_effective_setting(
            engine, backend, active_path[0], worker_settings
        )
        return SimpleNamespace(
            status="ok",
            diagnostics={},
            warnings=[],
            activity_coefficients={},
            vapor_pressures_Pa={"SiO2": _setting_marker(value)},
            liquid_fraction=0.5,
            phase_assemblage_available=True,
        )

    def isolated_cell(
        _handle: EngineHandle,
        pot: BinaryPot,
        *,
        temperature_K: float,
        po2: Po2Request,
        engine_settings: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> EquilibrateCell:
        vector_value = _path_value(engine_settings or {}, active_path[0])
        return replace(
            _cell_for_marker(engine, po2, _setting_marker(vector_value)),
            pot_id=pot.pot_id,
            temperature_K=temperature_K,
        )

    monkeypatch.setattr(backend, "equilibrate", direct_equilibrate)
    monkeypatch.setattr(battery, "_run_cell_in_subprocess", isolated_cell)

    try:
        for parent_path, key_path, first_value, second_value in _INITIALIZED_SETTING_MUTATIONS[engine]:
            if key_path not in {
                (name,)
                for name in baseline["engine_settings"]
                if isinstance(baseline["engine_settings"], dict)
            } and not (
                key_path == ("oxygen_balance_backend", "pack")
                and engine == "internal-analytical"
            ):
                continue
            if key_path == ("raw_model_name",):
                # raw_model_name is retained as an alias of the active model
                # for isolated reconstruction, not as independent state.
                assert baseline["engine_settings"].get("raw_model_name") == backend._model
            parent_path = (
                (parent_path,) if isinstance(parent_path, str) else parent_path
            )
            old_parent = _path_value(backend, parent_path)
            replacement = first_value if old_parent != first_value else second_value
            active_path[0] = key_path
            before = _path_value(baseline["engine_settings"], key_path)
            before_effective = _initialized_effective_setting(
                engine, backend, key_path, worker_settings
            )
            _set_path_value(backend, parent_path, replacement)
            current = battery._engine_cache_request_vector(
                handle,
                _pot(),
                temperature_K=1703.0,
                po2=po2,
                physical_pressure_bar=1.0,
                native_activity_formula=None,
            )
            assert current is not None
            keyed = _path_value(current["engine_settings"], key_path)
            effective = _initialized_effective_setting(
                engine, backend, key_path, worker_settings
            )
            assert keyed == effective, f"{engine} {key_path} is not its initialized setting"
            if effective == before_effective:
                assert keyed == before
            else:
                assert keyed != before

            direct = equilibrate_cell(
                handle,
                _pot(),
                temperature_K=1703.0,
                po2=po2,
                physical_pressure_bar=1.0,
                isolated=False,
            )
            isolated = equilibrate_cell(
                handle,
                _pot(),
                temperature_K=1703.0,
                po2=po2,
                physical_pressure_bar=1.0,
                isolated=True,
            )
            assert direct.status == isolated.status
            assert direct.gas_partial_pressures_Pa == isolated.gas_partial_pressures_Pa
            _set_path_value(backend, parent_path, old_parent)
    finally:
        if original_alpha_state is not None:
            (
                backend._mode,
                backend._python_api_model,
                backend._pet_worker,
            ) = original_alpha_state
        close_battery_engine_handle(handle)


def test_real_thermoengine_transport_model_matches_spawned_cache_paths(
    tmp_path: Path,
) -> None:
    handle = open_battery_engine("thermoengine")
    if not handle.available or handle.backend is None:
        close_battery_engine_handle(handle)
        pytest.skip(handle.unavailable_reason or "thermoengine unavailable")
    try:
        backend = handle.backend
        transport_model = backend._thermoengine_transport._model_name
        backend._model = "review-nonexistent-model"
        vector = battery._engine_cache_request_vector(
            handle,
            _pot(),
            temperature_K=1703.0,
            po2=Po2Request("engine_default", None),
            physical_pressure_bar=1.0,
            native_activity_formula=None,
        )
        assert vector is not None
        assert vector["engine_settings"]["model"] == transport_model

        direct_cache = EngineCache(tmp_path / "thermoengine-direct.sqlite")
        isolated_cache = EngineCache(tmp_path / "thermoengine-isolated.sqlite")
        common = {
            "temperature_K": 1703.0,
            "po2": Po2Request("engine_default", None),
            "physical_pressure_bar": 1.0,
        }
        outcomes = (
            equilibrate_cell(handle, _pot(), isolated=False, **common),
            equilibrate_cell(
                handle,
                _pot(),
                isolated=False,
                engine_cache=direct_cache,
                **common,
            ),
            equilibrate_cell(
                handle,
                _pot(),
                isolated=False,
                engine_cache=direct_cache,
                **common,
            ),
            equilibrate_cell(handle, _pot(), isolated=True, **common),
            equilibrate_cell(
                handle,
                _pot(),
                isolated=True,
                engine_cache=isolated_cache,
                **common,
            ),
            equilibrate_cell(
                handle,
                _pot(),
                isolated=True,
                engine_cache=isolated_cache,
                **common,
            ),
        )
        assert len(
            {
                replace(cell, wall_s=0.0, cpu_s=0.0, hostname="fixture").to_cache_payload()
                for cell in outcomes
            }
        ) == 1
        assert direct_cache.counters["thermoengine"]["stores"] == 1
        assert direct_cache.counters["thermoengine"]["hits"] == 1
        assert isolated_cache.counters["thermoengine"]["stores"] == 1
        assert isolated_cache.counters["thermoengine"]["hits"] == 1
    finally:
        close_battery_engine_handle(handle)


@pytest.mark.parametrize(
    ("engine", "first", "second"),
    [
        ("thermoengine", 1.0e-6, 1.0e-9),
        ("vaporock", 1.0e-6, -1.0),
    ],
)
def test_request_vector_keeps_pressure_seen_by_adapter(
    engine: str, first: float, second: float
) -> None:
    handle = _handle(engine)

    first_vector = _vector(handle, pressure_bar=first)
    second_vector = _vector(handle, pressure_bar=second)

    assert first_vector != second_vector


def test_openimcc_request_vector_ignores_unused_physical_pressure() -> None:
    handle = _handle("openimcc")

    first = _vector(handle, pressure_bar=1.0)
    second = _vector(handle, pressure_bar=2.0)

    assert first == second


def test_alphamelts_subprocess_keys_its_effective_pressure() -> None:
    handle = _handle("alphamelts")
    handle.backend._mode = "subprocess"

    subbar_first = _vector(handle, pressure_bar=1.0e-6)
    subbar_second = _vector(handle, pressure_bar=1.0e-9)
    higher_pressure = _vector(handle, pressure_bar=10.0)

    assert subbar_first == subbar_second
    assert subbar_first != higher_pressure


def test_alphamelts_subprocess_subbar_pressure_reuses_cached_result(
    tmp_path: Path,
) -> None:
    backend = _Backend("alphamelts")
    backend._mode = "subprocess"
    handle = _handle("alphamelts", backend)
    cache = EngineCache(tmp_path / "alphamelts-effective-pressure.sqlite")

    def solve(pressure_bar: float) -> Any:
        return equilibrate_cell(
            handle,
            _pot(),
            temperature_K=1703.0,
            po2=Po2Request("engine_default", None),
            physical_pressure_bar=pressure_bar,
            isolated=False,
            engine_cache=cache,
        )

    subbar = solve(1.0e-6)
    same_effective_pressure = solve(1.0e-9)
    higher_pressure = solve(10.0)

    def stable_payload(cell: Any) -> dict[str, Any]:
        payload = cell.as_payload()
        payload.pop("wall_s", None)
        payload.pop("cpu_s", None)
        return payload

    assert backend.calls == 2
    assert stable_payload(subbar) == stable_payload(same_effective_pressure)
    assert not hasattr(subbar, "physical_pressure_bar")
    assert stable_payload(higher_pressure) == stable_payload(subbar)
    assert cache.counters["alphamelts"]["hits"] == 1
    assert cache.counters["alphamelts"]["misses"] == 2


def test_request_vector_omits_qualification_decoration() -> None:
    handle = _handle("thermoengine")

    ordinary = _engine_cache_request_vector(
        handle,
        _pot(),
        temperature_K=1703.0,
        po2=Po2Request("engine_default", None),
        physical_pressure_bar=1.0,
        native_activity_formula=None,
    )
    qualification = _engine_cache_request_vector(
        handle,
        _pot(),
        temperature_K=1703.0,
        po2=Po2Request("engine_default", None),
        physical_pressure_bar=1.0,
        native_activity_formula=None,
    )

    assert ordinary is not None and qualification is not None
    assert ordinary == qualification
    assert "qualification" not in ordinary


def test_isolated_cell_uses_parent_magemin_database_for_off_cold_and_warm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.diagnostic_helpers.binary_pot_battery as battery

    class DatabaseBackend(_Backend):
        def __init__(self, database: str) -> None:
            super().__init__("magemin")
            self._database = database

        def equilibrate(self, **_kwargs: Any) -> Any:
            self.calls += 1
            supported = self._database == "ig"
            return SimpleNamespace(
                status="ok" if supported else "out_of_domain",
                diagnostics=(
                    {}
                    if supported
                    else {"backend_status_reason": "unsupported_database"}
                ),
                warnings=[],
                activity_coefficients={"SiO2": 0.5} if supported else {},
                vapor_pressures_Pa={},
                liquid_fraction=0.9 if supported else None,
                phase_assemblage_available=supported,
            )

    backend = DatabaseBackend("unsupported-db")
    handle = _handle("magemin", backend)
    cache = EngineCache(tmp_path / "magemin-isolation.sqlite")
    common = {
        "temperature_K": 1703.0,
        "po2": Po2Request("engine_default", None),
    }

    opened_settings: list[dict[str, Any]] = []

    def open_resolved(name: str, engine_settings: dict[str, Any] | None = None) -> Any:
        assert name == "magemin"
        settings = dict(engine_settings or {})
        opened_settings.append(settings)
        return DatabaseBackend(str(settings.get("database") or "ig"))

    monkeypatch.setattr(battery, "_open_resolved_backend", open_resolved)

    class InlineWorker:
        def request(self, payload: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
            del timeout_s
            settings = payload["engine_settings"]
            resource, _worker_name = battery._bootstrap_isolated_cell_engine(
                str(payload["engine"]), dict(settings)
            )
            try:
                return battery.run_isolated_cell_worker(
                    payload, handle=resource.handle
                )
            finally:
                resource.close()

        def retire(self) -> None:
            pass

    monkeypatch.setattr(
        battery, "_isolated_cell_worker", lambda *_args: InlineWorker()
    )

    direct = equilibrate_cell(handle, _pot(), isolated=False, **common)
    isolated_off = equilibrate_cell(handle, _pot(), isolated=True, **common)
    cold = equilibrate_cell(
        handle,
        _pot(),
        isolated=True,
        engine_cache=cache,
        **common,
    )
    warm = equilibrate_cell(
        handle,
        _pot(),
        isolated=False,
        engine_cache=cache,
        **common,
    )

    assert (
        direct.status
        == isolated_off.status
        == cold.status
        == warm.status
        == "refusal"
    )
    assert (
        direct.engine_reason
        == isolated_off.engine_reason
        == cold.engine_reason
        == warm.engine_reason
    )
    assert opened_settings == [
        _vector(handle)["engine_settings"],
        _vector(handle)["engine_settings"],
    ]
    assert cache.counters["magemin"]["misses"] == 1
    assert cache.counters["magemin"]["hits"] == 1


def test_magemin_ctypes_failure_has_environment_disposition() -> None:
    from simulator.melt_backend.magemin import (
        MAGEMinBackend,
        _MAGEMinEngineCallError,
    )

    backend = MAGEMinBackend()
    backend._bridge = "ctypes"
    with pytest.raises(_MAGEMinEngineCallError) as raised:
        backend._call_magemin(
            None,
            temperature_C=1000.0,
            pressure_bar=1.0,
            fO2_log=-8.0,
        )

    assert raised.value.failure_disposition == "environment"


def test_real_isolated_magemin_unsupported_database_is_cached_stably(
    tmp_path: Path,
) -> None:
    import simulator.diagnostic_helpers.binary_pot_battery as battery

    handle = open_battery_engine("magemin")
    if not handle.available or handle.backend is None:
        reason = handle.unavailable_reason or "MAGEMin binary or package is absent"
        close_battery_engine_handle(handle)
        pytest.skip(f"magemin unavailable: {reason}")

    try:
        assert handle.backend.initialize(
            {"database": "unsupported-db", "warm_worker": True}
        )
        common = {
            "temperature_K": 1703.0,
            "po2": Po2Request("engine_default", None),
            "physical_pressure_bar": 1.0,
        }
        off = equilibrate_cell(
            handle, _pot(), isolated=False, **common
        )
        isolated_off = equilibrate_cell(
            handle, _pot(), isolated=True, **common
        )
        cache = EngineCache(tmp_path / "magemin-real-isolated.sqlite")
        cold = equilibrate_cell(
            handle, _pot(), isolated=True, engine_cache=cache, **common
        )
        warm = equilibrate_cell(
            handle, _pot(), isolated=True, engine_cache=cache, **common
        )

        assert (
            off.status
            == isolated_off.status
            == cold.status
            == warm.status
            == "refusal"
        )
        assert (
            off.refusal_reason
            == isolated_off.refusal_reason
            == cold.refusal_reason
            == warm.refusal_reason
            == "out_of_basis"
        )
        assert cache.counters["magemin"]["stores"] == 1
        assert cache.counters["magemin"]["hits"] == 1
    finally:
        battery._close_isolated_cell_workers()
        close_battery_engine_handle(handle)


def test_isolated_backend_reconstruction_disables_nested_pools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import simulator.backends as backends
    import simulator.melt_backend.vaporock as vaporock
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _open_resolved_backend,
    )

    configs: dict[str, dict[str, object]] = {}

    class FakeVapoRockBackend:
        def initialize(self, config: dict[str, object]) -> bool:
            configs["vaporock"] = config
            return True

    def fake_try_backend(_factory: object, config: dict[str, object]) -> object:
        configs["magemin"] = config
        return object()

    def fake_resolve_backend(
        name: str, _policy: object, *, backend_config: dict[str, object]
    ) -> object:
        configs[name] = backend_config
        return object()

    monkeypatch.setattr(vaporock, "VapoRockBackend", FakeVapoRockBackend)
    monkeypatch.setattr(backends, "_try_backend", fake_try_backend)
    monkeypatch.setattr(backends, "resolve_backend", fake_resolve_backend)
    monkeypatch.setenv("ALPHAMELTS_CALC_MODE", "subprocess")

    _open_resolved_backend("vaporock", {})
    _open_resolved_backend(
        "magemin", {"database": "db", "fO2_buffer": "FMQ", "bridge": "subprocess"}
    )
    _open_resolved_backend(
        "alphamelts",
        {
            "ambient_calc_mode": "subprocess",
            "transport": "subprocess",
            "raw_model_name": "MELTS",
            "redox_buffer": "FMQ",
            "fO2_offset": 0.0,
            "Fe3Fet_ratio": None,
            "configured_vapor_transport_pO2_bar": None,
            "configured_vapor_transport_floor_bar": None,
        },
    )

    assert configs["magemin"]["warm_worker"] is False
    assert configs["vaporock"]["warm_worker"] is False
    assert configs["alphamelts"]["warm_worker"] is False


def test_internal_analytical_oxygen_balance_tracks_openimcc_epoch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    oxygen_backend = _Backend("internal-analytical")
    oxygen_handle = _handle("internal-analytical", oxygen_backend)
    oxygen_cache = EngineCache(tmp_path / "oxygen-balance.sqlite")
    oxygen_request = Po2Request("oxygen_balance_effusion", None, "W")

    def call(handle: EngineHandle, request: Po2Request, cache: EngineCache) -> Any:
        return equilibrate_cell(
            handle,
            _pot(),
            temperature_K=1703.0,
            po2=request,
            isolated=False,
            engine_cache=cache,
        )

    call(oxygen_handle, oxygen_request, oxygen_cache)
    monkeypatch.setitem(
        ENGINE_CHEMISTRY_VERSION,
        "openimcc",
        ENGINE_CHEMISTRY_VERSION["openimcc"] + 1,
    )
    call(oxygen_handle, oxygen_request, oxygen_cache)

    ordinary_backend = _Backend("internal-analytical")
    ordinary_handle = _handle("internal-analytical", ordinary_backend)
    ordinary_cache = EngineCache(tmp_path / "ordinary-cell.sqlite")
    ordinary_request = Po2Request("engine_default", None)
    call(ordinary_handle, ordinary_request, ordinary_cache)
    monkeypatch.setitem(
        ENGINE_CHEMISTRY_VERSION,
        "openimcc",
        ENGINE_CHEMISTRY_VERSION["openimcc"] + 1,
    )
    call(ordinary_handle, ordinary_request, ordinary_cache)

    assert oxygen_backend.calls == 2
    assert oxygen_cache.counters["internal-analytical"]["misses"] == 2
    assert ordinary_backend.calls == 2
    assert ordinary_cache.counters["internal-analytical"]["misses"] == 2
    assert ordinary_cache.counters["internal-analytical"]["hits"] == 0


def test_internal_analytical_oxygen_pack_uses_parent_setting_off_cold_and_warm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.diagnostic_helpers.binary_pot_battery as battery

    pack_name = "review-nonexistent-pack"

    class OxygenBackend(_Backend):
        def __init__(self, pack: str) -> None:
            super().__init__("internal-analytical")
            self._oxygen_balance_pack = pack

        def equilibrate(self, po2_request: Po2Request | None = None, **_kwargs: Any) -> Any:
            self.calls += 1
            if (
                po2_request is not None
                and po2_request.mode == "oxygen_balance_effusion"
                and self._oxygen_balance_pack == pack_name
            ):
                return SimpleNamespace(
                    status="out_of_domain",
                    diagnostics={
                        "backend_status_reason": "unsupported_oxygen_balance_pack"
                    },
                    warnings=[],
                    activity_coefficients={},
                    vapor_pressures_Pa={},
                    liquid_fraction=None,
                    phase_assemblage_available=False,
                )
            return SimpleNamespace(
                status="ok",
                diagnostics={},
                warnings=[],
                activity_coefficients={"SiO2": 0.5},
                vapor_pressures_Pa={},
                liquid_fraction=0.9,
                phase_assemblage_available=True,
            )

    backend = OxygenBackend(pack_name)
    handle = _handle("internal-analytical", backend)
    request = Po2Request("oxygen_balance_effusion", None, "W")
    vector = _vector(handle, po2=request)
    assert vector["engine_settings"]["oxygen_balance_backend"]["pack"] == pack_name

    def open_resolved(name: str, engine_settings: dict[str, Any] | None = None) -> Any:
        assert name == "internal-analytical"
        settings = dict(engine_settings or {})
        oxygen = settings.get("oxygen_balance_backend") or {}
        return OxygenBackend(str(oxygen.get("pack") or "default"))

    monkeypatch.setattr(battery, "_open_resolved_backend", open_resolved)

    class InlineWorker:
        def request(self, payload: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
            del timeout_s
            resource, _worker_name = battery._bootstrap_isolated_cell_engine(
                str(payload["engine"]), dict(payload["engine_settings"])
            )
            try:
                return battery.run_isolated_cell_worker(
                    payload, handle=resource.handle
                )
            finally:
                resource.close()

        def retire(self) -> None:
            pass

    monkeypatch.setattr(
        battery, "_isolated_cell_worker", lambda *_args: InlineWorker()
    )
    common = {
        "temperature_K": 1703.0,
        "po2": request,
    }
    off = equilibrate_cell(handle, _pot(), isolated=False, **common)
    isolated_off = equilibrate_cell(handle, _pot(), isolated=True, **common)
    cache = EngineCache(tmp_path / "internal-analytical-pack.sqlite")
    cold = equilibrate_cell(
        handle, _pot(), isolated=True, engine_cache=cache, **common
    )
    warm = equilibrate_cell(
        handle, _pot(), isolated=False, engine_cache=cache, **common
    )

    assert off.status == isolated_off.status == cold.status == warm.status == "refusal"
    assert off.refusal_reason == isolated_off.refusal_reason == cold.refusal_reason == warm.refusal_reason
    assert cache.counters["internal-analytical"]["stores"] == 1
    assert cache.counters["internal-analytical"]["hits"] == 1


def test_magemin_bridge_change_does_not_reuse_subprocess_cell(
    tmp_path: Path,
) -> None:
    class BridgeBackend(_Backend):
        def equilibrate(self, **_kwargs: Any) -> Any:
            self.calls += 1
            if self._bridge == "ctypes":
                return SimpleNamespace(
                    status="not_converged",
                    diagnostics={"backend_failure_disposition": "environment"},
                    warnings=[],
                    activity_coefficients={},
                    vapor_pressures_Pa={},
                    liquid_fraction=None,
                )
            return SimpleNamespace(
                status="ok",
                diagnostics={},
                warnings=[],
                activity_coefficients={"SiO2": 0.5},
                vapor_pressures_Pa={},
                liquid_fraction=0.9,
            )

    backend = BridgeBackend("magemin")
    handle = _handle("magemin", backend)
    cache = EngineCache(tmp_path / "magemin-bridge.sqlite")
    common = {
        "temperature_K": 1703.0,
        "po2": Po2Request("engine_default", None),
        "isolated": False,
        "engine_cache": cache,
    }

    subprocess_result = equilibrate_cell(handle, _pot(), **common)
    backend._bridge = "ctypes"
    ctypes_result = equilibrate_cell(handle, _pot(), **common)

    assert subprocess_result.engine_status == "ok"
    assert ctypes_result.engine_status == "not_converged"
    assert ctypes_result.backend_failure_disposition == "environment"
    assert backend.calls == 2
    assert cache.counters["magemin"]["hits"] == 0


def test_request_vector_tracks_ambient_alphamelts_calc_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = _handle("alphamelts")
    monkeypatch.setenv("ALPHAMELTS_CALC_MODE", "MELTS")
    expected_mode = _vector(handle)
    monkeypatch.setenv("ALPHAMELTS_CALC_MODE", "pMELTS")
    conflicting_mode = _vector(handle)

    assert expected_mode != conflicting_mode


def test_alphamelts_calc_mode_conflict_matches_cache_off_and_warm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class CalcModeBackend(_Backend):
        def equilibrate(self, **_kwargs: Any) -> Any:
            self.calls += 1
            if os.environ.get("ALPHAMELTS_CALC_MODE") not in {None, "MELTS"}:
                return SimpleNamespace(
                    status="unavailable",
                    diagnostics={
                        "backend_status_reason": "alphamelts_calc_mode_mismatch"
                    },
                    warnings=[],
                    activity_coefficients={},
                    vapor_pressures_Pa={},
                    liquid_fraction=None,
                )
            return SimpleNamespace(
                status="ok",
                diagnostics={},
                warnings=[],
                activity_coefficients={"SiO2": 0.5},
                vapor_pressures_Pa={},
                liquid_fraction=0.9,
            )

    backend = CalcModeBackend("alphamelts")
    handle = _handle("alphamelts", backend)
    pot = _pot()
    cache = EngineCache(tmp_path / "alphamelts-calc-mode.sqlite")

    monkeypatch.setenv("ALPHAMELTS_CALC_MODE", "MELTS")
    cached_expected_mode = equilibrate_cell(
        handle,
        pot,
        temperature_K=1703.0,
        po2=Po2Request("engine_default", None),
        isolated=False,
        engine_cache=cache,
    )
    assert cached_expected_mode.status == "ok"

    monkeypatch.setenv("ALPHAMELTS_CALC_MODE", "pMELTS")
    conflict_off = equilibrate_cell(
        handle,
        pot,
        temperature_K=1703.0,
        po2=Po2Request("engine_default", None),
        isolated=False,
    )
    conflict_warm = equilibrate_cell(
        handle,
        pot,
        temperature_K=1703.0,
        po2=Po2Request("engine_default", None),
        isolated=False,
        engine_cache=cache,
    )

    assert conflict_off.status == conflict_warm.status == "refusal"
    assert conflict_off.refusal_reason == conflict_warm.refusal_reason
    assert conflict_warm.engine_reason == "alphamelts_calc_mode_mismatch"
    assert backend.calls == 3


@pytest.mark.parametrize(
    ("engine", "setting", "value"),
    [
        ("alphamelts", "_model", "model-b"),
        ("alphamelts", "_mode", "subprocess"),
        ("alphamelts", "_redox_buffer", "NNO"),
        ("alphamelts", "_fo2_offset", 0.25),
        ("alphamelts", "_fe3fet_ratio", 0.2),
        ("alphamelts", "_python_api_model", ("model-b", 2)),
        ("alphamelts", "_configured_vapor_transport_pO2_bar", 1.0e-8),
        ("alphamelts", "_configured_vapor_transport_floor_bar", 1.0e-10),
        ("thermoengine", "_model", "model-b"),
        ("thermoengine", "_configured_vapor_transport_pO2_bar", 1.0e-8),
        ("thermoengine", "_configured_vapor_transport_floor_bar", 1.0e-10),
        ("magemin", "_database", "mp"),
        ("magemin", "_bridge", "julia"),
        ("openimcc", "_pack_name", "published-core"),
        ("vaporock", "_temperature_units", "K"),
        ("vaporock", "_pressure_units", "Pa"),
        ("vaporock", "_vapor_pressure_units", "Pa"),
    ],
)
def test_request_vector_changes_when_engine_setting_changes(
    engine: str, setting: str, value: Any
) -> None:
    backend = _Backend(engine)
    handle = _handle(engine, backend)
    first = _vector(handle)

    setattr(backend, setting, value)
    second = _vector(handle)

    assert first != second, f"{engine}.{setting} is missing from the cache key"


@pytest.mark.parametrize(
    ("engine", "setting", "value"),
    [
        ("alphamelts", "_raw_model_name", "raw-model-b"),
        ("alphamelts", "_timeout_s", 42.0),
        ("alphamelts", "_pet_warm_enabled", True),
        ("alphamelts", "_pet_warm_timeout_s", 43.0),
        ("alphamelts", "_binary_path", "/other/alphamelts"),
        ("alphamelts", "_engine_path", "/other/alphamelts-engine"),
        ("thermoengine", "_health_timeout_s", 42.0),
        ("thermoengine", "_mode", "alternate-transport"),
        (
            "thermoengine",
            "_thermoengine_transport",
            SimpleNamespace(equilibrate_timeout_s=43.0),
        ),
        ("magemin", "_binary_path", "/other/MAGEMin"),
        ("vaporock", "_database_path", "/other/vaporock"),
        ("vaporock", "_reuse_system", True),
        ("vaporock", "_warm_worker_enabled", True),
        ("vaporock", "_warm_pool_size", 2),
        ("vaporock", "_warm_call_timeout_s", 42.0),
        ("vaporock", "_worker_startup_timeout_s", 43.0),
    ],
)
def test_request_vector_ignores_operational_engine_setting_changes(
    engine: str, setting: str, value: Any
) -> None:
    backend = _Backend(engine)
    handle = _handle(engine, backend)
    first = _vector(handle)

    setattr(backend, setting, value)
    second = _vector(handle)

    assert first == second, f"operational setting {engine}.{setting} changed the cache key"


def test_request_vector_changes_when_magemin_buffer_setting_changes() -> None:
    backend = _Backend("magemin")
    handle = _handle("magemin", backend)
    first = _vector(handle)

    backend._config["fO2_buffer"] = "nno"
    second = _vector(handle)

    assert first != second


@pytest.mark.parametrize(
    ("engine", "setting", "value"),
    [
        ("alphamelts", "warm_worker", True),
        ("alphamelts", "petthermotools_warm_call_timeout_s", 42.0),
        ("alphamelts", "timeout_s", 43.0),
        ("alphamelts", "python_bridge", "subprocess"),
        ("alphamelts", "require_petthermotools", False),
        ("thermoengine", "thermoengine_health_timeout_s", 42.0),
        ("thermoengine", "thermoengine_equilibrate_timeout_s", 43.0),
        ("magemin", "python_bridge", "julia"),
        ("magemin", "binary_path", "/other/MAGEMin"),
        ("magemin", "warm_worker", False),
        ("magemin", "warm_call_timeout_s", 42.0),
        ("magemin", "warm_pool_size", 2),
        ("magemin", "worker_startup_timeout_s", 43.0),
        ("magemin", "timeout_s", 44.0),
        ("vaporock", "database_path", "/other/vaporock"),
        ("vaporock", "reuse_system", True),
        ("vaporock", "warm_worker", True),
        ("vaporock", "warm_pool_size", 2),
        ("vaporock", "warm_call_timeout_s", 42.0),
        ("vaporock", "worker_startup_timeout_s", 43.0),
    ],
)
def test_request_vector_ignores_operational_backend_config_changes(
    engine: str, setting: str, value: Any
) -> None:
    backend = _Backend(engine)
    handle = _handle(engine, backend)
    first = _vector(handle)

    backend._config[setting] = value
    second = _vector(handle)

    assert first == second, f"operational config {engine}.{setting} changed the cache key"


def test_caller_timeout_changes_reuse_cached_cell_outcome(tmp_path: Path) -> None:
    backend = _Backend("magemin")
    handle = _handle("magemin", backend)
    cache = EngineCache(tmp_path / "magemin-timeout.sqlite")
    request = {
        "temperature_K": 1703.0,
        "po2": Po2Request("engine_default", None),
        "isolated": False,
        "engine_cache": cache,
    }

    first = equilibrate_cell(
        handle, _pot(), timeout_s=30.0, **request
    )
    second = equilibrate_cell(
        handle, _pot(), timeout_s=31.0, **request
    )

    assert first.status == second.status
    assert backend.calls == 1
    assert cache.counters["magemin"]["misses"] == 1
    assert cache.counters["magemin"]["hits"] == 1


def test_request_vector_ignores_magemin_slot_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = _handle("magemin")
    monkeypatch.setenv("REGOLITH_MAGEMIN_SLOTS", "1")
    serialized = _vector(handle)
    monkeypatch.setenv("REGOLITH_MAGEMIN_SLOTS", "3")

    assert serialized == _vector(handle)


def test_request_vector_ignores_vaporock_session_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = _handle("vaporock")
    monkeypatch.delenv("REGOLITH_VAPOROCK_SESSION_WARM", raising=False)
    ordinary = _vector(handle)
    monkeypatch.setenv("REGOLITH_VAPOROCK_SESSION_WARM", "1")

    assert ordinary == _vector(handle)


@pytest.mark.parametrize(
    "variable",
    ("DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH"),
)
def test_request_vector_ignores_thermoengine_library_environment(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    handle = _handle("thermoengine")
    monkeypatch.setenv(variable, "/first/thermoengine")
    first = _vector(handle)
    monkeypatch.setenv(variable, "/second/thermoengine")

    assert first == _vector(handle)


def test_request_vector_changes_when_magemin_native_activity_changes() -> None:
    handle = _handle("magemin")
    common = {
        "temperature_K": 1703.0,
        "po2": Po2Request("engine_default", None),
        "physical_pressure_bar": 1.0,
    }

    without_activity = _engine_cache_request_vector(
        handle,
        _pot(),
        **common,
        native_activity_formula=None,
    )
    with_activity = _engine_cache_request_vector(
        handle,
        _pot(),
        **common,
        native_activity_formula="SiO2",
    )

    assert without_activity is not None and with_activity is not None
    assert without_activity != with_activity


def test_request_vector_changes_when_vaporock_model_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import simulator.melt_backend.vaporock as vaporock

    handle = _handle("vaporock")
    first = _vector(handle)
    monkeypatch.setattr(vaporock, "_MELT_MODEL_ID", "MELTS-v2")
    second = _vector(handle)

    assert first != second


def test_request_vector_uses_exact_magemin_pressure() -> None:
    handle = _handle("magemin")

    left = _vector(handle, pressure_bar=1.0001)
    right = _vector(handle, pressure_bar=1.0002)

    assert left != right


@pytest.mark.parametrize(
    "engine",
    [
        "openimcc",
        "internal-analytical",
        "vaporock",
        "magemin",
        "alphamelts",
        "thermoengine",
    ],
)
def test_request_vector_keys_each_engine_call_input(engine: str) -> None:
    handle = _handle(engine)
    default = _vector(handle)
    warmer = _engine_cache_request_vector(
        handle,
        replace(_pot(), composition_wt_pct={"SiO2": 60.0, "Al2O3": 40.0}),
        temperature_K=1704.0,
        po2=Po2Request("commanded", 1.0e-7),
        physical_pressure_bar=1.0001,
        native_activity_formula=None,
    )

    assert warmer is not None
    assert default != warmer


def test_openimcc_ferric_and_ferrous_requests_have_distinct_keys() -> None:
    from simulator.accounting.formulas import resolve_species_formula

    backend = _Backend("openimcc")
    handle = _handle("openimcc", backend)

    def pot_for_moles(pot_id: str, oxides: dict[str, float]) -> BinaryPot:
        masses = {
            oxide: amount * resolve_species_formula(oxide).molar_mass_kg_per_mol()
            for oxide, amount in oxides.items()
        }
        total = sum(masses.values())
        return replace(
            _pot(pot_id),
            composition_wt_pct={
                oxide: mass / total * 100.0 for oxide, mass in masses.items()
            },
        )

    ferric = _engine_cache_request_vector(
        handle,
        pot_for_moles("ferric", {"Fe2O3": 1.0, "SiO2": 1.0}),
        temperature_K=1703.0,
        po2=Po2Request("engine_default", None),
        physical_pressure_bar=1.0,
        native_activity_formula=None,
    )
    ferrous = _engine_cache_request_vector(
        handle,
        pot_for_moles("ferrous", {"FeO": 2.0, "SiO2": 1.0}),
        temperature_K=1703.0,
        po2=Po2Request("engine_default", None),
        physical_pressure_bar=1.0,
        native_activity_formula=None,
    )

    assert ferric is not None and ferrous is not None
    assert ferric != ferrous


@pytest.mark.parametrize(
    ("engine", "good_pressure", "bad_pressure"),
    [
        ("thermoengine", 1.0e-6, 1.0e-9),
        ("vaporock", 1.0e-6, -1.0),
    ],
)
def test_pressure_boundary_outcome_matches_cache_off_and_warm(
    tmp_path: Path,
    engine: str,
    good_pressure: float,
    bad_pressure: float,
) -> None:
    backend = _Backend(engine)

    def adapter_equilibrate(**kwargs: Any) -> Any:
        backend.calls += 1
        pressure = float(kwargs["pressure_bar"])
        if engine == "thermoengine":
            from simulator.melt_backend.thermoengine import (
                thermoengine_effective_pressure_policy,
            )

            effective, _label = thermoengine_effective_pressure_policy(pressure)
            status = "ok" if pressure == effective else "out_of_domain"
            diagnostics = (
                {} if status == "ok" else {"backend_status_reason": "clamped_operating_point"}
            )
        else:
            from simulator.melt_backend.vaporock import vaporock_pressure_bar_admitted

            status = "ok" if vaporock_pressure_bar_admitted(pressure) else "out_of_domain"
            diagnostics = (
                {} if status == "ok" else {"backend_status_reason": "invalid_pressure_bar"}
            )
        return SimpleNamespace(
            status=status,
            diagnostics=diagnostics,
            warnings=[],
            activity_coefficients={"SiO2": 0.5} if status == "ok" else {},
            vapor_pressures_Pa={},
            liquid_fraction=0.9,
            phase_assemblage_available=True,
        )

    backend.equilibrate = adapter_equilibrate  # type: ignore[method-assign]
    handle = _handle(engine, backend)
    pot = _pot()
    po2 = Po2Request("engine_default", None)

    uncached = equilibrate_cell(
        handle,
        pot,
        temperature_K=1703.0,
        po2=po2,
        isolated=False,
        physical_pressure_bar=bad_pressure,
    )
    cache_path = tmp_path / f"{engine}.sqlite"
    cache = EngineCache(cache_path)
    equilibrate_cell(
        handle,
        pot,
        temperature_K=1703.0,
        po2=po2,
        isolated=False,
        physical_pressure_bar=good_pressure,
        engine_cache=cache,
    )
    warm = equilibrate_cell(
        handle,
        pot,
        temperature_K=1703.0,
        po2=po2,
        isolated=False,
        physical_pressure_bar=bad_pressure,
        engine_cache=cache,
    )

    assert warm.status == uncached.status
    assert warm.refusal_reason == uncached.refusal_reason


def test_qualification_cells_share_engine_result_and_rebuild_decorations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.diagnostic_helpers.binary_pot_battery as battery

    backend = _Backend("alphamelts")
    handle = _handle("alphamelts", backend)
    pot = _pot()
    assessment_calls = 0
    actual_assess = battery.assess_qualification_gate

    def count_assessment(*args: Any, **kwargs: Any) -> Any:
        nonlocal assessment_calls
        assessment_calls += 1
        return actual_assess(*args, **kwargs)

    monkeypatch.setattr(battery, "assess_qualification_gate", count_assessment)
    cache = EngineCache(tmp_path / "qualification.sqlite")
    first = equilibrate_cell(
        handle,
        pot,
        temperature_K=1703.0,
        po2=Po2Request("engine_default", None),
        isolated=False,
        engine_cache=cache,
    )
    second = equilibrate_cell(
        handle,
        replace(pot, pot_id="qualification-pot-2"),
        temperature_K=1703.0,
        po2=Po2Request("engine_default", None),
        qualification=True,
        isolated=False,
        engine_cache=cache,
    )

    assert backend.calls == 1
    assert assessment_calls == 1
    assert first.pot_id != second.pot_id
    assert not first.notices
    assert any(notice.get("kind") == "melts_domain_gate" for notice in second.notices)
    assert cache.counters["alphamelts"]["hits"] == 1

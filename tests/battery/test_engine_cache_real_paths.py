from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from simulator.battery.enums import Engine
from simulator.diagnostic_helpers.binary_pot_battery import (
    close_battery_engine_handle,
)
from simulator.melt_backend.base import EquilibriumResult
from tests.battery.cache_test_support import CACHE_TEST_ENGINES

CACHEABLE_TEST_ENGINES = tuple(
    engine
    for engine in CACHE_TEST_ENGINES
    if engine is not Engine.INTERNAL_ANALYTICAL
)


@dataclass(frozen=True)
class _SettingCase:
    label: str
    engine: Engine
    backend_factory: Callable[[], Any]
    mutate: Callable[[Any], None]
    output: Callable[[Any], float]


def _setting_cases() -> tuple[_SettingCase, ...]:
    from simulator.melt_backend.alphamelts import AlphaMELTSBackend
    from simulator.melt_backend.magemin import MAGEMinBackend
    from simulator.melt_backend.thermoengine import ThermoEngineBackend

    def alpha_backend() -> Any:
        backend = AlphaMELTSBackend()
        backend._mode = "subprocess"
        backend._model = "MELTS-v1.0"
        backend._redox_buffer = "QFM"
        backend._fo2_offset = 0.0
        backend._configured_vapor_transport_pO2_bar = 1.0e-8
        backend._configured_vapor_transport_floor_bar = 1.0e-9
        return backend

    def magemin_backend() -> Any:
        backend = MAGEMinBackend()
        backend._available = True
        backend._database = "ig"
        backend._bridge = "subprocess"
        backend._config = {"fO2_buffer": "qfm"}
        return backend

    def thermoengine_backend() -> Any:
        backend = ThermoEngineBackend()
        backend._mode = "thermoengine"
        backend._model = "MELTS-v1.0"
        backend._configured_vapor_transport_pO2_bar = None
        backend._configured_vapor_transport_floor_bar = None
        backend._thermoengine_transport = SimpleNamespace(close=lambda: None)
        return backend

    def vaporock_backend() -> Any:
        from simulator.melt_backend.vaporock import VapoRockBackend

        backend = VapoRockBackend()
        backend._temperature_units = "C"
        return backend

    def openimcc_backend() -> Any:
        from simulator.diagnostic_helpers.binary_pot_battery import (
            _OpenImccBatteryBackend,
        )

        backend = object.__new__(_OpenImccBatteryBackend)
        backend._pack_name = "openimcc-v1.0.2"
        backend._gas_pack_name = "default"
        backend._identity = {"pack": "openimcc-v1.0.2"}
        return backend

    return (
        _SettingCase(
            "AlphaMELTS transport mode",
            Engine.ALPHAMELTS,
            alpha_backend,
            lambda backend: setattr(backend, "_mode", "python_api"),
            lambda backend: {"subprocess": 0.25, "python_api": 0.75}[
                backend._mode
            ],
        ),
        _SettingCase(
            "AlphaMELTS model",
            Engine.ALPHAMELTS,
            alpha_backend,
            lambda backend: setattr(backend, "_model", "rhyolite-MELTS"),
            lambda backend: {"MELTS-v1.0": 0.25, "rhyolite-MELTS": 0.75}[
                backend._model
            ],
        ),
        _SettingCase(
            "AlphaMELTS Python API model",
            Engine.ALPHAMELTS,
            alpha_backend,
            lambda backend: setattr(backend, "_python_api_model", ("pMELTS", 1)),
            lambda backend: {
                None: 0.25,
                ("pMELTS", 1): 0.75,
            }[backend._python_api_model],
        ),
        _SettingCase(
            "AlphaMELTS redox buffer",
            Engine.ALPHAMELTS,
            alpha_backend,
            lambda backend: setattr(backend, "_redox_buffer", "NNO"),
            lambda backend: {"QFM": 0.25, "NNO": 0.75}[backend._redox_buffer],
        ),
        _SettingCase(
            "AlphaMELTS fO2 offset",
            Engine.ALPHAMELTS,
            alpha_backend,
            lambda backend: setattr(backend, "_fo2_offset", 0.5),
            lambda backend: {0.0: 0.25, 0.5: 0.75}[backend._fo2_offset],
        ),
        _SettingCase(
            "AlphaMELTS Fe3+/Fet ratio",
            Engine.ALPHAMELTS,
            alpha_backend,
            lambda backend: setattr(backend, "_fe3fet_ratio", 0.3),
            lambda backend: {None: 0.25, 0.3: 0.75}[backend._fe3fet_ratio],
        ),
        _SettingCase(
            "AlphaMELTS configured vapor pO2",
            Engine.ALPHAMELTS,
            alpha_backend,
            lambda backend: setattr(
                backend, "_configured_vapor_transport_pO2_bar", 1.0e-3
            ),
            lambda backend: {
                1.0e-8: 0.25,
                1.0e-3: 0.75,
            }[backend._configured_vapor_transport_pO2_bar],
        ),
        _SettingCase(
            "AlphaMELTS configured vapor floor",
            Engine.ALPHAMELTS,
            alpha_backend,
            lambda backend: setattr(
                backend, "_configured_vapor_transport_floor_bar", 1.0e-6
            ),
            lambda backend: {
                1.0e-9: 0.25,
                1.0e-6: 0.75,
            }[backend._configured_vapor_transport_floor_bar],
        ),
        _SettingCase(
            "MAGEMin database",
            Engine.MAGEMIN,
            magemin_backend,
            lambda backend: setattr(backend, "_database", "mp"),
            lambda backend: {"ig": 0.25, "mp": 0.75}[backend._database],
        ),
        _SettingCase(
            "MAGEMin worker fO2 buffer",
            Engine.MAGEMIN,
            magemin_backend,
            lambda backend: backend._config.__setitem__("fO2_buffer", "nno"),
            lambda backend: {"qfm": 0.25, "nno": 0.75}[
                backend._config["fO2_buffer"]
            ],
        ),
        _SettingCase(
            "MAGEMin bridge selection",
            Engine.MAGEMIN,
            magemin_backend,
            lambda backend: setattr(backend, "_bridge", "julia"),
            lambda backend: {"subprocess": 0.25, "julia": 0.75}[backend._bridge],
        ),
        _SettingCase(
            "ThermoEngine transport model",
            Engine.THERMOENGINE,
            thermoengine_backend,
            lambda backend: setattr(backend, "_model", "rhyolite-MELTS"),
            lambda backend: {
                "MELTS-v1.0": 0.25,
                "rhyolite-MELTS": 0.75,
            }[backend._model],
        ),
        _SettingCase(
            "ThermoEngine vapor transport pO2",
            Engine.THERMOENGINE,
            thermoengine_backend,
            lambda backend: setattr(
                backend, "_configured_vapor_transport_pO2_bar", 1.0e-3
            ),
            lambda backend: {
                None: 0.25,
                1.0e-3: 0.75,
            }[backend._configured_vapor_transport_pO2_bar],
        ),
        _SettingCase(
            "ThermoEngine vapor transport floor",
            Engine.THERMOENGINE,
            thermoengine_backend,
            lambda backend: setattr(
                backend, "_configured_vapor_transport_floor_bar", 1.0e-6
            ),
            lambda backend: {
                None: 0.25,
                1.0e-6: 0.75,
            }[backend._configured_vapor_transport_floor_bar],
        ),
        _SettingCase(
            "OpenIMCC loaded cell pack",
            Engine.OPENIMCC,
            openimcc_backend,
            lambda backend: (
                setattr(backend, "_pack_name", "openimcc-v1.0.3-ext-v1"),
                backend._identity.__setitem__("pack", "openimcc-v1.0.3-ext-v1"),
            ),
            lambda backend: {
                "openimcc-v1.0.2": 0.25,
                "openimcc-v1.0.3-ext-v1": 0.75,
            }[backend._effective_pack_name],
        ),
        _SettingCase(
            "OpenIMCC gas pack",
            Engine.OPENIMCC,
            openimcc_backend,
            lambda backend: setattr(backend, "_gas_pack_name", "published-gas-ext"),
            lambda backend: {
                "default": 0.25,
                "published-gas-ext": 0.75,
            }[backend._gas_pack_name],
        ),
        _SettingCase(
            "VapoRock temperature units",
            Engine.VAPOROCK,
            vaporock_backend,
            lambda backend: setattr(backend, "_temperature_units", "K"),
            lambda backend: {"C": 0.25, "K": 0.75}[backend._temperature_units],
        ),
        _SettingCase(
            "VapoRock pressure units",
            Engine.VAPOROCK,
            vaporock_backend,
            lambda backend: setattr(backend, "_pressure_units", "Pa"),
            lambda backend: {"bar": 0.25, "Pa": 0.75}[backend._pressure_units],
        ),
        _SettingCase(
            "VapoRock vapor pressure units",
            Engine.VAPOROCK,
            vaporock_backend,
            lambda backend: setattr(backend, "_vapor_pressure_units", "Pa"),
            lambda backend: {
                "bar": 0.25,
                "Pa": 0.75,
            }[backend._vapor_pressure_units],
        ),
    )


@pytest.mark.parametrize("case", _setting_cases(), ids=lambda case: case.label)
def test_each_projected_engine_setting_changes_the_key(case: _SettingCase) -> None:
    from simulator.engine_cache import (
        ENGINE_CHEMISTRY_VERSION,
        engine_cache_key,
        engine_cache_settings,
    )

    backend = case.backend_factory()
    before_settings = engine_cache_settings(case.engine.value, backend)
    before_key = engine_cache_key(
        case.engine.value,
        ENGINE_CHEMISTRY_VERSION[case.engine.value],
        {"temperature_K": 1600.0, "settings": before_settings},
    )
    assert case.output(backend) == 0.25

    case.mutate(backend)
    after_settings = engine_cache_settings(case.engine.value, backend)
    after_key = engine_cache_key(
        case.engine.value,
        ENGINE_CHEMISTRY_VERSION[case.engine.value],
        {"temperature_K": 1600.0, "settings": after_settings},
    )

    assert case.output(backend) == 0.75
    assert before_settings != after_settings, case.label
    assert before_key != after_key, case.label


@pytest.mark.parametrize(
    ("engine", "case_label", "expected_fields"),
    (
        (Engine.OPENIMCC, "OpenIMCC loaded cell pack", {"melt_pack", "gas_pack"}),
        (
            Engine.ALPHAMELTS,
            "AlphaMELTS model",
            {
                "transport_mode",
                "model",
                "python_api_model",
                "redox_buffer",
                "fo2_offset",
                "fe3fet_ratio",
                "vapor_transport_pO2_bar",
                "vapor_transport_floor_bar",
            },
        ),
        (
            Engine.VAPOROCK,
            "VapoRock temperature units",
            {"temperature_units", "pressure_units", "vapor_pressure_units"},
        ),
        (
            Engine.MAGEMIN,
            "MAGEMin database",
            {"database", "bridge", "fO2_buffer"},
        ),
        (
            Engine.THERMOENGINE,
            "ThermoEngine transport model",
            {"model", "vapor_transport_pO2_bar", "vapor_transport_floor_bar"},
        ),
    ),
)
def test_engine_cache_settings_are_the_named_prediction_projection(
    engine: Engine,
    case_label: str,
    expected_fields: set[str],
) -> None:
    from simulator.engine_cache import engine_cache_settings

    case = next(case for case in _setting_cases() if case.label == case_label)
    settings = engine_cache_settings(engine.value, case.backend_factory())

    assert set(settings) == expected_fields


@pytest.mark.parametrize(
    ("engine", "configuration", "setting", "changed"),
    (
        ("openimcc", {"pack": "core"}, "pack", "extended"),
        ("alphamelts", {"redox_buffer": "QFM"}, "redox_buffer", "NNO"),
        ("vaporock", {"temperature_units": "C"}, "temperature_units", "K"),
        ("magemin", {"database": "ig"}, "database", "mp"),
        ("thermoengine", {"model": "MELTS-v1.0"}, "model", "rhyolite-MELTS"),
    ),
)
def test_isolated_engine_config_prediction_settings_change_the_key(
    engine: str,
    configuration: dict[str, Any],
    setting: str,
    changed: str,
) -> None:
    from simulator.engine_cache import ENGINE_CHEMISTRY_VERSION, engine_cache_key, engine_cache_settings

    first = engine_cache_settings(engine, configuration)
    first_key = engine_cache_key(
        engine, ENGINE_CHEMISTRY_VERSION[engine], {"settings": first}
    )
    configuration[setting] = changed
    second = engine_cache_settings(engine, configuration)
    second_key = engine_cache_key(
        engine, ENGINE_CHEMISTRY_VERSION[engine], {"settings": second}
    )

    assert first_key != second_key


@pytest.mark.parametrize(
    ("engine", "configuration", "field", "changed"),
    (
        ("openimcc", {"pack": "core"}, "pack", "extended"),
        ("openimcc", {"gas_pack": "default"}, "gas_pack", "published-gas"),
        ("alphamelts", {"transport": "subprocess"}, "transport", "python_api"),
        ("alphamelts", {"ambient_calc_mode": "MELTS"}, "ambient_calc_mode", "pMELTS"),
        ("alphamelts", {"raw_model_name": "MELTS-v1.0"}, "raw_model_name", "rhyolite-MELTS"),
        ("alphamelts", {"redox_buffer": "QFM"}, "redox_buffer", "NNO"),
        ("alphamelts", {"fO2_offset": 0.0}, "fO2_offset", 0.5),
        ("alphamelts", {"Fe3Fet_ratio": 0.1}, "Fe3Fet_ratio", 0.3),
        ("alphamelts", {"python_api_model": ["MELTS", 1]}, "python_api_model", ["pMELTS", 1]),
        (
            "alphamelts",
            {"configured_vapor_transport_pO2_bar": 1.0e-8},
            "configured_vapor_transport_pO2_bar",
            1.0e-3,
        ),
        (
            "alphamelts",
            {"configured_vapor_transport_floor_bar": 1.0e-9},
            "configured_vapor_transport_floor_bar",
            1.0e-6,
        ),
        ("vaporock", {"temperature_units": "C"}, "temperature_units", "K"),
        ("vaporock", {"pressure_units": "bar"}, "pressure_units", "Pa"),
        ("vaporock", {"vapor_pressure_units": "bar"}, "vapor_pressure_units", "Pa"),
        ("magemin", {"database": "ig"}, "database", "mp"),
        ("magemin", {"bridge": "subprocess"}, "bridge", "julia"),
        ("magemin", {"python_bridge": "subprocess"}, "python_bridge", "julia"),
        ("magemin", {"fO2_buffer": "qfm"}, "fO2_buffer", "nno"),
        ("thermoengine", {"model": "MELTS-v1.0"}, "model", "rhyolite-MELTS"),
        (
            "thermoengine",
            {"configured_vapor_transport_pO2_bar": 1.0e-8},
            "configured_vapor_transport_pO2_bar",
            1.0e-3,
        ),
        (
            "thermoengine",
            {"configured_vapor_transport_floor_bar": 1.0e-9},
            "configured_vapor_transport_floor_bar",
            1.0e-6,
        ),
    ),
)
def test_isolated_public_engine_config_fields_change_the_projection(
    engine: str,
    configuration: dict[str, Any],
    field: str,
    changed: Any,
) -> None:
    from simulator.engine_cache import engine_cache_settings

    before = engine_cache_settings(engine, configuration)
    configuration[field] = changed

    assert engine_cache_settings(engine, configuration) != before, field


@pytest.mark.parametrize(
    "engine",
    ("openimcc", "alphamelts", "vaporock", "magemin", "thermoengine"),
)
def test_worker_scheduling_config_does_not_change_prediction_settings(engine: str) -> None:
    from simulator.engine_cache import (
        ENGINE_SETTINGS_PREDICTION_NEUTRAL_REASONS,
        engine_cache_settings,
    )

    reason = ENGINE_SETTINGS_PREDICTION_NEUTRAL_REASONS[engine]["warm_worker"]
    configuration = {"warm_worker": False}
    before = engine_cache_settings(engine, configuration)
    configuration["warm_worker"] = True

    assert reason
    assert engine_cache_settings(engine, configuration) == before


def test_engine_opener_and_cache_share_canonical_settings_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import backends
    from simulator.diagnostic_helpers import binary_pot_battery
    from simulator.engine_cache import (
        ENGINE_CHEMISTRY_VERSION,
        engine_cache_key,
        engine_cache_settings,
        normalize_engine_settings,
    )
    from simulator.melt_backend.alphamelts import (
        AlphaMELTSBackend,
        AlphaMELTSSubprocessContractError,
        AlphaMELTSSubprocessRunMode,
    )
    import signal

    parent_mode = "parent-process-sentinel"
    monkeypatch.setenv("ALPHAMELTS_CALC_MODE", parent_mode)
    common = {
        "transport": "subprocess",
        "raw_model_name": "MELTSv1.0.2",
        "redox_buffer": "QFM",
        "fO2_offset": 0.5,
        "Fe3Fet_ratio": 0.3,
    }
    p_melts_settings = normalize_engine_settings(
        "alphamelts", {**common, "ambient_calc_mode": "pMELTS"}
    )
    melts_settings = normalize_engine_settings(
        "alphamelts", {**common, "ambient_calc_mode": "MELTS"}
    )
    omitted_settings = normalize_engine_settings("alphamelts", common)
    assert omitted_settings["effective_calc_mode"] == "MELTS"
    assert engine_cache_settings("alphamelts", omitted_settings) == engine_cache_settings(
        "alphamelts", normalize_engine_settings(
            "alphamelts", {**common, "ambient_calc_mode": "MELTS"}
        )
    )

    def projection_key(settings: dict[str, Any]) -> bytes:
        return engine_cache_key(
            "alphamelts",
            ENGINE_CHEMISTRY_VERSION["alphamelts"],
            {"engine_settings": engine_cache_settings("alphamelts", settings)},
        )

    assert projection_key(p_melts_settings) != projection_key(melts_settings)
    assert projection_key(omitted_settings) == projection_key(melts_settings)

    captured_configs: list[dict[str, Any]] = []
    launched_modes: list[str] = []

    def fake_resolve_backend(
        _name: str, _policy: Any, *, backend_config: dict[str, Any]
    ) -> Any:
        captured_configs.append(dict(backend_config))
        backend = AlphaMELTSBackend(model_name=backend_config["model"])
        backend._mode = "subprocess"
        backend._binary_path = Path("fake-alphamelts")
        backend._engine_version = "captured-test"
        backend._effective_subprocess_calc_mode = backend_config[
            "effective_calc_mode"
        ]
        backend._subprocess_calc_mode_conflict = backend_config[
            "calc_mode_conflict"
        ]
        backend._subprocess_model_error = backend_config["subprocess_model_error"]
        backend._subprocess_calc_mode_configured = True
        return backend

    monkeypatch.setattr(backends, "resolve_backend", fake_resolve_backend)
    monkeypatch.setattr(
        "simulator.engine_cache.record_engine_open", lambda *_args: None
    )

    def fake_run(_args: Any, **kwargs: Any) -> Any:
        launched_modes.append(kwargs["env"]["ALPHAMELTS_CALC_MODE"])
        return SimpleNamespace(returncode=-signal.SIGABRT, stdout="", stderr="")

    monkeypatch.setattr(
        "simulator.melt_backend.alphamelts._run_alphamelts_subprocess", fake_run
    )
    p_melts_handle = binary_pot_battery._open_battery_engine_with_settings(
        "alphamelts", {**common, "ambient_calc_mode": "pMELTS"}
    )
    melts_handle = binary_pot_battery._open_battery_engine_with_settings(
        "alphamelts", {**common, "ambient_calc_mode": "MELTS"}
    )
    try:
        assert os.environ["ALPHAMELTS_CALC_MODE"] == parent_mode
        assert [config["effective_calc_mode"] for config in captured_configs] == [
            "pMELTS",
            "MELTS",
        ]
        assert p_melts_handle.engine_settings["effective_calc_mode"] == "pMELTS"
        assert melts_handle.engine_settings["effective_calc_mode"] == "MELTS"
        call = {
            "temperature_C": 1400.0,
            "comp_wt": {
                "SiO2": 49.0,
                "Al2O3": 15.0,
                "FeO": 10.0,
                "MgO": 10.0,
            },
            "fO2_log": -9.0,
            "pressure_bar": 1.0,
            "total_input_kg": 100.0,
            "run_mode": AlphaMELTSSubprocessRunMode.ISOTHERMAL,
        }
        with pytest.raises(AlphaMELTSSubprocessContractError):
            p_melts_handle.backend._equilibrate_subprocess(**call)
        melts_handle.backend._equilibrate_subprocess(**call)
        assert launched_modes == ["MELTS"]
        assert os.environ["ALPHAMELTS_CALC_MODE"] == parent_mode
        assert captured_configs[0]["calc_mode_conflict"] is True
        assert captured_configs[1]["calc_mode_conflict"] is False
    finally:
        close_battery_engine_handle(p_melts_handle)
        close_battery_engine_handle(melts_handle)


def test_magemin_python_bridge_alias_is_normalized_before_open_and_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.engine_cache import (
        engine_cache_settings,
        normalize_engine_settings,
    )
    from simulator import backends
    from simulator.diagnostic_helpers import binary_pot_battery

    captured: dict[str, Any] = {}

    def fake_try_backend(_backend_class: Any, config: dict[str, Any]) -> Any:
        captured.update(config)
        return SimpleNamespace()

    monkeypatch.setattr(backends, "_try_backend", fake_try_backend)

    settings = normalize_engine_settings("magemin", {"python_bridge": "julia"})
    backend = binary_pot_battery._open_resolved_backend("magemin", settings)

    assert backend is not None
    assert settings["bridge"] == "julia"
    assert "python_bridge" not in settings
    assert captured["python_bridge"] == "julia"
    assert engine_cache_settings("magemin", settings)["bridge"] == "julia"


def test_default_alphamelts_open_uses_resolved_mode_without_parent_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import backends
    from simulator.diagnostic_helpers import binary_pot_battery
    from simulator.engine_cache import engine_cache_settings, normalize_engine_settings

    captured: dict[str, Any] = {}

    def fake_resolve_backend(
        _name: str,
        _policy: Any,
        *,
        backend_config: dict[str, Any] | None = None,
    ) -> Any:
        captured.update(backend_config or {})
        return SimpleNamespace()

    monkeypatch.setattr(backends, "resolve_backend", fake_resolve_backend)
    monkeypatch.setenv("ALPHAMELTS_CALC_MODE", "pMELTS")
    effective = normalize_engine_settings("alphamelts", None)
    monkeypatch.setenv("ALPHAMELTS_CALC_MODE", "MELTS")

    binary_pot_battery._open_resolved_backend(
        "alphamelts", normalized_settings=effective
    )

    assert captured["effective_calc_mode"] == "MELTS"
    assert os.environ["ALPHAMELTS_CALC_MODE"] == "MELTS"
    assert engine_cache_settings("alphamelts", effective)[
        "effective_calc_mode"
    ] == "MELTS"


def test_openimcc_unused_gas_pack_setting_is_not_a_cache_input() -> None:
    from simulator.engine_cache import engine_cache_settings

    assert engine_cache_settings("openimcc", {"pack": "default"}) == (
        engine_cache_settings(
            "openimcc", {"pack": "default", "gas_pack": "unused-custom-pack"}
        )
    )


@pytest.mark.parametrize("engine", ("alphamelts", "thermoengine"))
def test_melts_redox_projection_covers_shared_normalizer_inputs(engine: str) -> None:
    from simulator.engine_cache import engine_cache_settings

    if engine == "alphamelts":
        settings = {
            "redox_buffer": "QFM",
            "fO2_offset": 0.25,
            "Fe3Fet_ratio": 0.35,
        }
        expected = {
            "redox_buffer": "QFM",
            "fo2_offset": 0.25,
            "fe3fet_ratio": 0.35,
        }
    else:
        settings = {
            "redox_buffer": "QFM",
            "fO2_offset": 0.25,
            "Fe3Fet_ratio": 0.35,
        }
        expected = {
            "redox_buffer": "QFM",
            "fo2_offset": 0.25,
            "fe3fet_ratio": 0.35,
        }

    projected = engine_cache_settings(engine, settings)

    for name, value in expected.items():
        assert projected[name] == value


@pytest.mark.parametrize(
    "engine,configuration",
    (
        ("openimcc", {"pack": "openimcc-v1.0.2", "gas_pack": "ignored"}),
        (
            "alphamelts",
            {
                "transport": "subprocess",
                "ambient_calc_mode": "MELTS",
                "raw_model_name": "MELTSv1.0.2",
                "redox_buffer": "QFM",
                "fO2_offset": 0.25,
                "Fe3Fet_ratio": 0.35,
                "python_api_model": ("MELTS", 1),
                "configured_vapor_transport_pO2_bar": 1.0e-8,
                "configured_vapor_transport_floor_bar": 1.0e-9,
                "warm_worker": False,
            },
        ),
        (
            "vaporock",
            {
                "temperature_units": "C",
                "pressure_units": "bar",
                "vapor_pressure_units": "bar",
                "warm_worker": False,
            },
        ),
        (
            "magemin",
            {
                "database": "ig",
                "bridge": None,
                "python_bridge": "subprocess",
                "fO2_buffer": "QFM",
                "warm_worker": False,
            },
        ),
        (
            "thermoengine",
            {
                "model": "MELTSv1.0.2",
                "configured_vapor_transport_pO2_bar": 1.0e-8,
                "configured_vapor_transport_floor_bar": 1.0e-9,
                "redox_buffer": "QFM",
                "fO2_offset": 0.25,
                "Fe3Fet_ratio": 0.35,
                "warm_worker": False,
            },
        ),
    ),
)
def test_opener_settings_reads_are_projected_or_declared_neutral(
    engine: str,
    configuration: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections.abc import Iterator, Mapping

    from simulator import engine_cache
    from simulator.backends import BackendUnavailableError
    from simulator.diagnostic_helpers import binary_pot_battery
    from simulator.melt_backend.alphamelts import AlphaMELTSBackend
    from simulator.melt_backend.magemin import MAGEMinBackend
    from simulator.melt_backend.thermoengine import ThermoEngineBackend
    from simulator.melt_backend.vaporock import VapoRockBackend

    from simulator.engine_cache import (
        ENGINE_SETTINGS_PREDICTION_NEUTRAL_REASONS,
        engine_cache_settings,
        normalize_engine_settings,
    )

    class RecordingMapping(Mapping[str, Any]):
        def __init__(self, values: dict[str, Any]) -> None:
            self.values = values
            self.reads: set[str] = set()

        def __getitem__(self, key: str) -> Any:
            self.reads.add(key)
            return self.values[key]

        def __iter__(self) -> Iterator[str]:
            return iter(self.values)

        def __len__(self) -> int:
            return len(self.values)

    backend_config_sources = {
        "alphamelts": {
            "mode": "transport",
            "python_bridge": "constructor:python_bridge",
            "alphamelts": "constructor:alphamelts",
            "model": "raw_model_name",
            "effective_calc_mode": "effective_calc_mode",
            "model_calc_mode": "constructor:model_calc_mode",
            "calc_mode_conflict": "constructor:calc_mode_conflict",
            "subprocess_model_error": "constructor:subprocess_model_error",
            "fO2_buffer": "redox_buffer",
            "redox_buffer": "constructor:redox_buffer_fallback",
            "fO2_offset": "fO2_offset",
            "Fe3Fet_Liq": "Fe3Fet_ratio",
            "fe3fet_ratio": "constructor:fe3fet_ratio_fallback",
            "warm_worker": "warm_worker",
            "petthermotools_warm_call_timeout_s": "constructor:petthermotools_warm_call_timeout_s",
            "timeout_s": "constructor:timeout_s",
            "require_petthermotools": "constructor:require_petthermotools",
            "vapor_transport_pO2_bar": "configured_vapor_transport_pO2_bar",
            "vapor_transport_floor_bar": "configured_vapor_transport_floor_bar",
        },
        "vaporock": {
            "temperature_units": "temperature_units",
            "pressure_units": "pressure_units",
            "vapor_pressure_units": "vapor_pressure_units",
            "warm_worker": "warm_worker",
        },
        "magemin": {
            "database": "database",
            "fO2_buffer": "fO2_buffer",
            "python_bridge": "bridge",
            "warm_worker": "warm_worker",
        },
        "thermoengine": {
            "mode": "constructor:mode",
            "alphamelts": "constructor:alphamelts",
            "thermoengine": "constructor:thermoengine",
            "model": "model",
            "vapor_transport_pO2_bar": "configured_vapor_transport_pO2_bar",
            "vapor_transport_floor_bar": "configured_vapor_transport_floor_bar",
            "fO2_buffer": "redox_buffer",
            "redox_buffer": "constructor:redox_buffer_fallback",
            "fO2_offset": "fO2_offset",
            "Fe3Fet_Liq": "Fe3Fet_ratio",
            "fe3fet_ratio": "constructor:fe3fet_ratio_fallback",
            "thermoengine_health_timeout_s": "constructor:thermoengine_health_timeout_s",
            "thermoengine_equilibrate_timeout_s": "constructor:thermoengine_equilibrate_timeout_s",
        },
    }

    raw = RecordingMapping(configuration)
    normalized = normalize_engine_settings(engine, raw)
    projected = engine_cache_settings(engine, normalized)
    neutral = ENGINE_SETTINGS_PREDICTION_NEUTRAL_REASONS[engine]

    for key in raw.reads - set(neutral):
        changed = dict(configuration)
        value = changed.get(key)
        if isinstance(value, bool):
            changed[key] = not value
        elif isinstance(value, (int, float)):
            changed[key] = value + 1
        elif isinstance(value, str):
            changed[key] = value + "-changed"
        elif isinstance(value, tuple):
            changed[key] = (*value, 2)
        elif value is None:
            changed[key] = "changed"
        else:
            pytest.fail(f"cannot mutate recorded setting {engine}.{key}: {value!r}")
        assert engine_cache_settings(engine, changed) != projected, key

    normalized_reads: list[RecordingMapping] = []
    constructor_reads: list[RecordingMapping] = []
    original_normalize = engine_cache.normalize_engine_settings

    def record_normalized_mapping(
        name: str, settings: Mapping[str, Any] | None
    ) -> RecordingMapping:
        result = RecordingMapping(original_normalize(name, settings))
        normalized_reads.append(result)
        return result

    monkeypatch.setattr(
        engine_cache, "normalize_engine_settings", record_normalized_mapping
    )

    def record_initializer(backend_class: type) -> None:
        original_initialize = backend_class.initialize

        def initialize(self: Any, settings: Mapping[str, Any]) -> Any:
            recorded = RecordingMapping(dict(settings))
            constructor_reads.append(recorded)
            return original_initialize(self, recorded)

        monkeypatch.setattr(backend_class, "initialize", initialize)

    if engine == "alphamelts":
        record_initializer(AlphaMELTSBackend)
    elif engine == "vaporock":
        record_initializer(VapoRockBackend)
    elif engine == "magemin":
        record_initializer(MAGEMinBackend)
    elif engine == "thermoengine":
        record_initializer(ThermoEngineBackend)

    opened_backend = None
    try:
        opened_backend = binary_pot_battery._open_resolved_backend(engine, raw)
    except BackendUnavailableError:
        pass
    finally:
        if opened_backend is not None:
            close_battery_engine_handle(
                SimpleNamespace(backend=opened_backend)
            )
    opener_settings = normalized_reads[-1]
    opener_projection = engine_cache_settings(engine, opener_settings)
    assert opener_settings.reads <= set(opener_settings)
    for key in opener_settings.reads - set(neutral):
        changed = dict(opener_settings)
        value = changed[key]
        if isinstance(value, bool):
            changed[key] = not value
        elif isinstance(value, (int, float)):
            changed[key] = value + 1
        elif isinstance(value, str):
            changed[key] = value + "-changed"
        elif isinstance(value, tuple):
            changed[key] = (*value, 2)
        elif value is None:
            changed[key] = "changed"
        else:
            pytest.fail(f"cannot mutate opened setting {engine}.{key}: {value!r}")
        assert engine_cache_settings(engine, changed) != opener_projection, key
    if engine != "openimcc":
        assert constructor_reads
        sources = backend_config_sources[engine]
        constructor_projection = engine_cache_settings(engine, opener_settings)
        for recorded in constructor_reads:
            for key in recorded.reads:
                source = sources[key]
                if source.startswith("constructor:"):
                    assert source in neutral and neutral[source], key
                    continue
                if source in neutral:
                    continue
                assert source in opener_settings
                changed = dict(opener_settings)
                value = changed[source]
                if isinstance(value, bool):
                    changed[source] = not value
                elif isinstance(value, (int, float)):
                    changed[source] = value + 1
                elif isinstance(value, str):
                    changed[source] = value + "-changed"
                elif isinstance(value, tuple):
                    changed[source] = (*value, 2)
                else:
                    pytest.fail(
                        f"cannot mutate backend setting {engine}.{key}: {value!r}"
                    )
                assert engine_cache_settings(engine, changed) != constructor_projection, key
    assert projected


def test_internal_analytical_projection_does_not_inspect_uncacheable_backend() -> None:
    from simulator.engine_cache import engine_cache_settings

    assert engine_cache_settings("internal-analytical", object()) == {}


def test_engine_cache_settings_do_not_read_or_mutate_unprojected_backend_state() -> None:
    from simulator.engine_cache import engine_cache_key, engine_cache_settings

    class LazyCore:
        touched = False

        @property
        def condensation_model(self) -> object:
            self.touched = True
            return object()

    core = LazyCore()
    backend = SimpleNamespace(_core=core, _mode="openimcc")
    before = {name: id(value) for name, value in vars(backend).items()}

    engine_cache_settings("internal-analytical", backend)

    assert not core.touched
    assert {name: id(value) for name, value in vars(backend).items()} == before

    class LazyOpenImcc:
        _identity = {"pack": "published-core"}
        _pack_name = "default"
        _gas_pack_name = "default"

        @property
        def _effective_pack_name(self) -> str:
            self.property_was_read = True
            return self._identity["pack"]

    openimcc = LazyOpenImcc()
    settings = engine_cache_settings("openimcc", openimcc)
    engine_cache_key("openimcc", 1, settings)

    assert settings["melt_pack"] == "published-core"
    assert "property_was_read" not in vars(openimcc)


@pytest.mark.parametrize(
    "case",
    tuple(case for case in _setting_cases() if case.engine is Engine.ALPHAMELTS),
    ids=lambda case: case.label,
)
def test_alphamelts_runtime_metadata_is_outside_the_prediction_projection(
    case: _SettingCase,
) -> None:
    from simulator.engine_cache import engine_cache_settings

    backend = case.backend_factory()
    before = engine_cache_settings(case.engine.value, backend)
    backend._binary_path = Path("/runtime/first")
    backend._engine_path = Path("/runtime/first")
    backend._engine_version = "runtime-v1"
    after = engine_cache_settings(case.engine.value, backend)

    assert after == before


@pytest.mark.parametrize("engine", CACHEABLE_TEST_ENGINES, ids=lambda value: value.value)
def test_each_real_backend_equilibrate_warm_pair_matches_cache_off(
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_cache
    from simulator.diagnostic_helpers.binary_pot_battery import (
        Po2Request,
        close_battery_engine_handle,
        equilibrate_cell,
        load_binary_pots,
    )
    from tests.battery.cache_test_support import open_available_engine

    handle = open_available_engine(engine, nodeid="real backend cache warm pair")
    pots, _metadata = load_binary_pots()
    if engine is Engine.ALPHAMELTS:
        from simulator.diagnostic_helpers.binary_pot_battery import BinaryPot

        pot = BinaryPot(
            pot_id="alphamelts_cache_in_domain",
            kato_1993_table4_system=None,
            why="Known liquid-forming AlphaMELTS fixture composition.",
            composition_wt_pct={
                "SiO2": 49.0,
                "Al2O3": 15.0,
                "FeO": 10.0,
                "Fe2O3": 1.0,
                "MgO": 10.0,
                "CaO": 10.0,
                "Na2O": 5.0,
            },
        )
    else:
        pot = next(pot for pot in pots if pot.pot_id == "mgo_sio2_40_60")
    common = {
        "temperature_K": 1700.0,
        "po2": Po2Request(mode="engine_default", po2_bar=None),
        "timeout_s": 90.0,
        "isolated": False,
    }

    def stable(cell: Any) -> Any:
        return cell.to_engine_cache_value().hot

    def consumer_flags(cell: Any) -> Any:
        return (
            cell.authority,
            cell.authoritative_for_requested_vapor_pressure,
            cell.vapor_pressure_backend_status,
            cell.vapor_pressure_backend_status_reason,
            tuple(dict(notice) for notice in cell.notices),
        )

    try:
        monkeypatch.delenv("REGOLITH_ENGINE_CACHE", raising=False)
        cache_off = equilibrate_cell(handle, pot, **common)
        monkeypatch.setenv(
            "REGOLITH_ENGINE_CACHE", str(tmp_path / f"{engine.value}.sqlite")
        )
        engine_cache.engine_cache_counters(reset=True)
        cold = equilibrate_cell(handle, pot, **common)
        warm = equilibrate_cell(handle, pot, **common)

        assert stable(cache_off) == stable(cold) == stable(warm)
        assert consumer_flags(cache_off) == consumer_flags(cold) == consumer_flags(warm)
        assert engine_cache.engine_cache_counters(reset=True)[engine.value] == {
            "hits": 1,
            "misses": 1,
            "stores": 1,
            "store_error": 0,
            "read_error": 0,
        }
    finally:
        close_battery_engine_handle(handle)


def test_alphamelts_out_of_domain_cell_is_not_stored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_cache
    from simulator.diagnostic_helpers.binary_pot_battery import (
        Po2Request,
        close_battery_engine_handle,
        equilibrate_cell,
        load_binary_pots,
    )
    from tests.battery.cache_test_support import open_available_engine

    handle = open_available_engine(
        Engine.ALPHAMELTS, nodeid="AlphaMELTS out-of-domain cache refusal"
    )
    pots, _metadata = load_binary_pots()
    pot = next(pot for pot in pots if pot.pot_id == "mgo_sio2_40_60")
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "alpha-ood.sqlite"))
    engine_cache.engine_cache_counters(reset=True)
    try:
        cell = equilibrate_cell(
            handle,
            pot,
            temperature_K=1700.0,
            po2=Po2Request(mode="engine_default", po2_bar=None),
            timeout_s=90.0,
            isolated=False,
        )
        assert cell.engine_status == "out_of_domain"
        assert engine_cache.engine_cache_counters(reset=True)["alphamelts"][
            "stores"
        ] == 0
    finally:
        close_battery_engine_handle(handle)


def test_disabled_cache_does_not_walk_opaque_equilibrate_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_cache
    from simulator.diagnostic_helpers.binary_pot_battery import (
        EngineHandle,
        _call_cached_backend_equilibrate,
    )

    monkeypatch.delenv("REGOLITH_ENGINE_CACHE", raising=False)
    monkeypatch.setattr(
        engine_cache,
        "engine_cache_settings",
        lambda *_args: pytest.fail("disabled cache built backend settings"),
    )
    handle = EngineHandle(
        name="openimcc",
        backend=object(),
        available=True,
        unavailable_reason=None,
        takes_fo2=False,
        supports_intrinsic_fo2=False,
    )
    result = EquilibriumResult(status="ok", liquid_fraction=0.75)

    assert _call_cached_backend_equilibrate(
        handle, {"temperature_C": 1000.0}, lambda: result
    ) is result


def test_disabled_cache_does_not_walk_opaque_internal_analytical_core(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_cache
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    from simulator.diagnostic_helpers.binary_pot_battery import (
        PO2_COMMANDED,
        Po2Request,
        internal_analytical_residue_vapor_pressure,
    )

    monkeypatch.delenv("REGOLITH_ENGINE_CACHE", raising=False)
    monkeypatch.setattr(engine_cache, "record_engine_open", lambda *_args: None)
    monkeypatch.setattr(
        engine_cache,
        "engine_cache_settings",
        lambda *_args: pytest.fail("disabled cache built backend settings"),
    )
    expected = SimpleNamespace(vapor_pressures_Pa={"SiO(g)": 2.0})
    monkeypatch.setattr(
        battery,
        "_internal_analytical_vapor_pressure_adapter",
        lambda **_kwargs: expected,
    )

    result = internal_analytical_residue_vapor_pressure(
        core=object(),
        temperature_C=1500.0,
        pressure_bar=1.0,
        composition_kg=None,
        composition_mol={"SiO2": 1.0},
        fO2_log=-8.0,
        po2_request=Po2Request(PO2_COMMANDED, 1e-8),
        include_diagnostic_shadows=False,
    )

    assert result is expected


def test_internal_analytical_equilibrate_bypasses_enabled_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_cache
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _call_cached_backend_equilibrate,
    )

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    monkeypatch.setattr(
        engine_cache,
        "engine_cache_settings",
        lambda *_args: pytest.fail("stateful internal analytical backend was projected"),
    )
    engine_cache.engine_cache_counters(reset=True)
    handle = SimpleNamespace(name="internal-analytical", backend=object())
    result = EquilibriumResult(status="ok", liquid_fraction=0.75)

    assert _call_cached_backend_equilibrate(handle, {"temperature_C": 1000.0}, lambda: result) is result
    assert engine_cache.engine_cache_counters(reset=True).get(
        "internal-analytical", {}
    ) == {}


def test_openimcc_battery_adapter_does_not_add_an_outer_cache_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from simulator import engine_cache
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _call_cached_backend_equilibrate,
    )

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    engine_cache.engine_cache_counters(reset=True)
    handle = SimpleNamespace(name="openimcc", backend=SimpleNamespace())
    calls: list[dict[str, float]] = []

    def solve(composition: dict[str, float]) -> EquilibriumResult:
        calls.append(composition)
        return EquilibriumResult(status="ok", liquid_fraction=0.5)

    ferric = {"SiO2": 0.5, "Fe2O3": 0.1}
    ferrous = {"SiO2": 0.5, "FeO": 0.2}
    for composition in (ferric, ferrous):
        _call_cached_backend_equilibrate(
            handle,
            {"composition_mol": composition, "temperature_C": 1500.0},
            lambda composition=composition: solve(composition),
        )

    assert calls == [ferric, ferrous]
    assert engine_cache.engine_cache_counters(reset=True).get("openimcc", {}) == {}


def test_openimcc_battery_adapter_does_not_cache_around_the_bridge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from simulator import engine_cache
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _call_cached_backend_equilibrate,
    )

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    engine_cache.engine_cache_counters(reset=True)
    handle = SimpleNamespace(name="openimcc", backend=SimpleNamespace())
    calls = 0

    def solve() -> EquilibriumResult:
        nonlocal calls
        calls += 1
        return EquilibriumResult(status="ok", liquid_fraction=0.625)

    first = _call_cached_backend_equilibrate(
        handle,
        {
            "temperature_C": 1500.0,
            "pressure_bar": 1.0,
            "call_timeout_s": 30.0,
        },
        solve,
    )
    second = _call_cached_backend_equilibrate(
        handle,
        {
            "temperature_C": 1500.0,
            "pressure_bar": 40.0,
            "call_timeout_s": 0.01,
        },
        solve,
    )

    assert first.liquid_fraction == second.liquid_fraction == 0.625
    assert calls == 2
    assert engine_cache.engine_cache_counters(reset=True).get("openimcc", {}) == {}


def test_isolated_backend_reconstruction_disables_nested_pools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    import simulator.backends as backends
    import simulator.melt_backend.vaporock as vaporock
    from simulator.diagnostic_helpers.binary_pot_battery import _open_resolved_backend

    configs: dict[str, dict[str, Any]] = {}

    class FakeVapoRockBackend:
        def initialize(self, config: dict[str, Any]) -> bool:
            configs["vaporock"] = dict(config)
            return True

    monkeypatch.setattr(vaporock, "VapoRockBackend", FakeVapoRockBackend)

    def try_backend(_backend_type: Any, config: dict[str, Any]) -> Any:
        configs["magemin"] = dict(config)
        return SimpleNamespace()

    monkeypatch.setattr(backends, "_try_backend", try_backend)

    def resolve_backend(name: str, _policy: Any, *, backend_config: dict[str, Any]):
        configs[name] = dict(backend_config)
        return SimpleNamespace()

    monkeypatch.setattr(backends, "resolve_backend", resolve_backend)
    monkeypatch.setenv("ALPHAMELTS_CALC_MODE", "MELTS")
    for engine in ("vaporock", "magemin", "alphamelts", "thermoengine"):
        _open_resolved_backend(engine, {})

    assert set(configs) == {"vaporock", "magemin", "alphamelts", "thermoengine"}
    assert set(configs["vaporock"]) == {
        "temperature_units",
        "pressure_units",
        "vapor_pressure_units",
        "warm_worker",
    }
    assert set(configs["magemin"]) == {
        "database",
        "fO2_buffer",
        "python_bridge",
        "warm_worker",
    }
    assert set(configs["alphamelts"]) == {
        "mode",
        "model",
        "fO2_buffer",
        "fO2_offset",
        "Fe3Fet_Liq",
        "warm_worker",
        "vapor_transport_pO2_bar",
        "vapor_transport_floor_bar",
    }
    assert set(configs["thermoengine"]) == {
        "model",
        "vapor_transport_pO2_bar",
        "vapor_transport_floor_bar",
    }
    # warm_worker is a scheduling option, so it is deliberately not projected.
    assert all(
        config["warm_worker"] is False
        for config in configs.values()
        if "warm_worker" in config
    )
    assert "warm_worker" not in configs["thermoengine"]


def test_cache_settings_exclude_warm_pool_runtime_state() -> None:
    from simulator.engine_cache import engine_cache_settings

    backend = SimpleNamespace(
        _temperature_units="C",
        _pressure_units="bar",
        _vapor_pressure_units="bar",
        _config={"warm_worker": True, "warm_pool_size": 4},
        _warm_pool=SimpleNamespace(call_count=0),
        _warm_worker_enabled=True,
        _warm_pool_size=1,
        _startup_failure_disposition=None,
    )
    first = engine_cache_settings("vaporock", backend)
    backend._warm_pool.call_count = 1
    backend._startup_failure_disposition = "worker_died"
    backend._warm_pool_size = 8
    second = engine_cache_settings("vaporock", backend)

    assert first == second == {
        "temperature_units": "C",
        "pressure_units": "bar",
        "vapor_pressure_units": "bar",
    }


@pytest.mark.parametrize("case", _setting_cases(), ids=lambda case: case.label)
def test_runtime_identity_is_outside_engine_settings(case: _SettingCase) -> None:
    from simulator.engine_cache import engine_cache_settings

    backend = case.backend_factory()
    first = engine_cache_settings(case.engine.value, backend)
    backend._engine_version = "runtime sha256:" + "a" * 64
    backend.runtime_identity = {
        "commit": "b" * 64,
        "absolute_path": "/engine/runtime",
        "hostname": "runtime-host",
    }
    backend.runtime_worker = SimpleNamespace(
        ready_payload={"engine_version": "runtime-v2", "identity": "worker"}
    )
    if case.engine is Engine.THERMOENGINE:
        backend._thermoengine_transport.engine_version = "runtime-v3"

    assert engine_cache_settings(case.engine.value, backend) == first


def test_magemin_bridge_selection_is_a_cache_setting() -> None:
    from simulator.engine_cache import engine_cache_key, engine_cache_settings

    case = next(case for case in _setting_cases() if case.label == "MAGEMin bridge selection")
    backend = case.backend_factory()
    first = engine_cache_settings("magemin", backend)
    backend._bridge = "julia"
    second = engine_cache_settings("magemin", backend)

    assert first["bridge"] == "subprocess"
    assert second["bridge"] == "julia"
    assert engine_cache_key("magemin", 1, first) != engine_cache_key("magemin", 1, second)


def test_openimcc_cache_settings_use_selected_loaded_packs() -> None:
    from simulator.engine_cache import engine_cache_key, engine_cache_settings
    from simulator.diagnostic_helpers.binary_pot_battery import _OpenImccBatteryBackend

    backend = object.__new__(_OpenImccBatteryBackend)
    backend._pack_name = "default"
    backend._gas_pack_name = "default"
    backend._identity = {"pack": "published-core"}

    first = engine_cache_settings("openimcc", backend)
    backend._identity["pack"] = "published-ext"
    second = engine_cache_settings("openimcc", backend)
    backend._gas_pack_name = "published-gas-ext"
    third = engine_cache_settings("openimcc", backend)

    assert first == {"melt_pack": "published-core", "gas_pack": "default"}
    assert second["melt_pack"] == "published-ext"
    assert third["gas_pack"] == "published-gas-ext"
    assert engine_cache_key("openimcc", 1, first) != engine_cache_key(
        "openimcc", 1, second
    )


def test_openimcc_gas_cache_key_tracks_pack_selection_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_cache
    from simulator.melt_backend.openimcc_bridge import evaluate_gas

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "gas-cache.sqlite"))
    engine_cache.engine_cache_counters(reset=True)
    calls = 0

    def fake_evaluate_gas(
        _activities: dict[str, float],
        _temperature_K: float,
        _pressure_bar: float,
        gas_pack: Any,
        **_kwargs: Any,
    ) -> dict[str, float]:
        nonlocal calls
        calls += 1
        return {"CO(g)": {"gas-a.csv": 1.0, "gas-b.csv": 2.0}[gas_pack.gas_path.name]}

    monkeypatch.setitem(
        sys.modules,
        "openimcc",
        SimpleNamespace(evaluate_gas=fake_evaluate_gas),
    )
    common = ({"SiO2": 0.5}, 1700.0, 1.0e-6)
    oxide_path = Path("condensate.csv")
    first_pack = SimpleNamespace(gas_path=Path("gas-a.csv"), oxide_path=oxide_path)
    second_pack = SimpleNamespace(gas_path=Path("gas-b.csv"), oxide_path=oxide_path)

    first = evaluate_gas(*common, first_pack, gas_pack_selection="published-core")
    same_selection = evaluate_gas(
        *common, second_pack, gas_pack_selection="published-core"
    )
    other_selection = evaluate_gas(
        *common, second_pack, gas_pack_selection="published-ext"
    )

    assert first == same_selection
    assert first["CO(g)"] != other_selection["CO(g)"]
    assert calls == 2
    assert engine_cache.engine_cache_counters(reset=True)["openimcc"] == {
        "hits": 1,
        "misses": 2,
        "stores": 2,
        "store_error": 0,
        "read_error": 0,
    }


def test_runtime_identity_is_captured_once_when_each_engine_opens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections import Counter

    from simulator import engine_cache
    import simulator.diagnostic_helpers.binary_pot_battery as battery

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    engine_cache.runtime_identity.cache_clear()
    engine_cache.engine_cache_runtime_identities(reset=True)
    calls: Counter[str] = Counter()

    def compute(engine: str) -> str:
        calls[engine] += 1
        return f"identity:{engine}"

    monkeypatch.setattr(engine_cache, "_compute_runtime_identity", compute)
    monkeypatch.setattr(
        battery,
        "_open_resolved_backend",
        lambda _name, _settings=None: SimpleNamespace(
            equilibrate=lambda **_kwargs: None,
            supports_intrinsic_fO2=False,
        ),
    )
    engines = tuple(engine_cache.ENGINE_CHEMISTRY_VERSION)
    for engine in engines:
        for _ in range(3):
            battery._open_battery_engine_with_settings(engine)

    assert calls == Counter(dict.fromkeys(engines, 1))


def test_internal_analytical_residue_cache_round_trips_named_observation_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_cache
    from simulator.battery.score import load_score_context, temperature_of
    from simulator.diagnostic_helpers import binary_pot_battery as battery

    source_id = "abdeyazdan-2026-na-sn-si-phase-thermodynamics"
    observation_id = (
        source_id
        + "::abdeyazdan_2026_t1_run04_NaSi11_glass_nao0p5"
    )
    context = load_score_context(Path(__file__).resolve().parents[2], sources=(source_id,))
    observation = context.observations[observation_id]
    temperature_K = temperature_of(observation.identity)
    assert temperature_K is not None and observation.value.point is not None
    temperature_C = float(temperature_K) - 273.15
    nao_half_fraction = float(observation.value.point)
    composition_mol = {
        "NaO0.5": nao_half_fraction,
        "SiO2": 1.0 - nao_half_fraction,
    }

    monkeypatch.delenv("REGOLITH_ENGINE_CACHE", raising=False)
    engine_cache.engine_cache_counters(reset=True)
    calls: list[dict[str, Any]] = []

    def fake_residue_pressure(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return SimpleNamespace(
            vapor_pressures_Pa={"Na(g)": 1.25},
            diagnostics=SimpleNamespace(unconsumed="not part of residue scoring"),
        )

    monkeypatch.setattr(
        battery, "_internal_analytical_vapor_pressure_adapter", fake_residue_pressure
    )
    from simulator.diagnostic_helpers.binary_pot_battery import (
        PO2_COMMANDED,
        Po2Request,
        internal_analytical_residue_vapor_pressure,
    )

    request = {
        "core": object(),
        "temperature_C": temperature_C,
        "pressure_bar": 1.0,
        "composition_kg": None,
        "composition_mol": composition_mol,
        "fO2_log": -8.0,
        "po2_request": Po2Request(PO2_COMMANDED, 10.0**-8.0),
        "include_diagnostic_shadows": False,
    }
    uncached = internal_analytical_residue_vapor_pressure(**request)
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    cold = internal_analytical_residue_vapor_pressure(**request)
    warm = internal_analytical_residue_vapor_pressure(**request)

    assert uncached == cold == warm
    assert cold.vapor_pressures_Pa == {"Na(g)": 1.25}
    assert cold.diagnostics.unconsumed == "not part of residue scoring"
    assert len(calls) == 3
    assert calls[0]["temperature_C"] == temperature_C
    assert calls[0]["composition_mol"] == composition_mol
    assert calls[1]["composition_mol"] == composition_mol
    assert engine_cache.engine_cache_counters(reset=True).get(
        "internal-analytical", {}
    ) == {}


def test_internal_analytical_residue_uses_each_current_high_t_activity_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_cache
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    from simulator.diagnostic_helpers.binary_pot_battery import (
        PO2_COMMANDED,
        Po2Request,
        internal_analytical_residue_vapor_pressure,
    )

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    monkeypatch.setattr(engine_cache, "record_engine_open", lambda *_args: None)
    engine_cache.engine_cache_counters(reset=True)
    core = SimpleNamespace(
        _high_t_melt_activity="openimcc",
        _last_high_t_melt_activity={},
        _last_high_t_melt_activity_temperature_K=None,
    )
    calls = 0

    def solve(**kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        current_core = kwargs["core"]
        pressure = 2.0 if current_core._last_high_t_melt_activity else 1.0
        return SimpleNamespace(vapor_pressures_Pa={"SiO(g)": pressure})

    monkeypatch.setattr(battery, "_internal_analytical_vapor_pressure_adapter", solve)
    request = {
        "temperature_C": 1726.85,
        "pressure_bar": 0.0,
        "composition_kg": None,
        "composition_mol": {"SiO2": 0.44, "FeO": 0.08, "MgO": 0.36, "CaO": 0.12},
        "fO2_log": -10.0,
        "po2_request": Po2Request(PO2_COMMANDED, 1e-10),
        "include_diagnostic_shadows": False,
    }

    first = internal_analytical_residue_vapor_pressure(core=core, **request)
    core._last_high_t_melt_activity = {
        "temperature_K": 1950.0,
        "certified_effective_gamma_by_parent": {
            "SiO2": {
                "gamma_last": 1.25,
                "temperature_K": 1950.0,
                "composition_single_cation": {"SiO2": 1.0},
            }
        },
    }
    core._last_high_t_melt_activity_temperature_K = 1950.0
    second = internal_analytical_residue_vapor_pressure(core=core, **request)

    assert first.vapor_pressures_Pa == {"SiO(g)": 1.0}
    assert second.vapor_pressures_Pa == {"SiO(g)": 2.0}
    assert calls == 2
    assert engine_cache.engine_cache_counters(reset=True).get(
        "internal-analytical", {}
    ) == {}


def test_internal_analytical_high_temperature_modes_are_uncached_and_equivalent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_cache
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    from simulator.diagnostic_helpers.binary_pot_battery import (
        PO2_COMMANDED,
        Po2Request,
        internal_analytical_residue_vapor_pressure,
    )
    from tests.battery.cache_test_support import open_available_engine

    cache_path = tmp_path / "internal-analytical-high-temperature.sqlite"
    request = {
        "temperature_C": 1726.85,
        "pressure_bar": 0.0,
        "composition_kg": None,
        "composition_mol": {
            "SiO2": 0.44,
            "Al2O3": 0.05,
            "FeO": 0.08,
            "MgO": 0.36,
            "CaO": 0.07,
        },
        "fO2_log": -10.0,
        "po2_request": Po2Request(PO2_COMMANDED, 1e-10),
        "include_diagnostic_shadows": False,
    }
    solver = battery._internal_analytical_vapor_pressure_adapter
    calls = 0

    def counted_solver(**kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return solver(**kwargs)

    monkeypatch.setattr(battery, "_internal_analytical_vapor_pressure_adapter", counted_solver)

    def evaluate(mode: str, *, cache: bool) -> Any:
        handle = open_available_engine(
            Engine.INTERNAL_ANALYTICAL,
            nodeid="internal analytical high-temperature cache bypass",
        )
        try:
            core = handle.backend._core
            core._high_t_melt_activity = mode
            if cache:
                monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(cache_path))
            else:
                monkeypatch.delenv("REGOLITH_ENGINE_CACHE", raising=False)
            return internal_analytical_residue_vapor_pressure(core=core, **request)
        finally:
            close_battery_engine_handle(handle)

    engine_cache.engine_cache_counters(reset=True)
    openimcc_off = evaluate("openimcc", cache=False)
    openimcc_on = evaluate("openimcc", cache=True)
    constant_gamma_off = evaluate("constant_gamma", cache=False)
    constant_gamma_on = evaluate("constant_gamma", cache=True)

    assert openimcc_on == openimcc_off
    assert constant_gamma_on == constant_gamma_off
    assert openimcc_off.vapor_pressures_Pa != constant_gamma_off.vapor_pressures_Pa
    assert calls == 4
    assert engine_cache.engine_cache_counters(reset=True).get(
        "internal-analytical", {}
    ) == {}


def test_hashimoto_internal_residue_path_reads_diagnostics_without_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_cache
    import simulator.battery.residue as residue
    from simulator.diagnostic_helpers import binary_pot_battery as battery

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    engine_cache.engine_cache_counters(reset=True)
    calls = 0

    def fake_residue_pressure(**_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            vapor_pressures_Pa={"Na": 5.0},
            diagnostics={
                "vapor_pressure_diagnostic": {
                    "pO2_floor_inversion_notices_by_species": {
                        "Na": {"reason": "recorded floor inversion"}
                    }
                }
            },
        )

    monkeypatch.setattr(battery, "_new_internal_analytical_core", lambda: object())
    monkeypatch.setattr(
        battery, "_internal_analytical_vapor_pressure_adapter", fake_residue_pressure
    )
    floor_diagnostics: dict[str, dict[str, object]] = {}
    pressure = residue._make_residue_pressure_model(
        "internal-analytical",
        (("Na", (None, 1.0, -0.5)),),
        None,
        temperature_K=1800.0,
        total_pressure_Pa=100_000.0,
        engine_metadata={},
        engine_flags=set(),
        missing_channels_reason="residue_pressure_channels_missing",
        caller="Hashimoto cache diagnostics",
        floor_inversion_diagnostics=floor_diagnostics,
    )

    first = pressure({"Na2O": 1.0}, -8.0)
    second = pressure({"Na2O": 1.0}, -8.0)
    pressure._on_accepted_evaluation(-8.0)

    assert first == second == {"Na": 5.0}
    assert floor_diagnostics["Na"]["detail"] == {
        "reason": "recorded floor inversion"
    }
    assert calls == 2

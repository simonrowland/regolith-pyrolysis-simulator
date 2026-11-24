from __future__ import annotations

import os
import signal
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

pytestmark = pytest.mark.engine_cache_off

@dataclass(frozen=True)
class _SettingCase:
    label: str
    engine: Engine
    backend_factory: Callable[[], Any]
    mutate: Callable[[Any], None]
    output: Callable[[Any], float]
    configuration: dict[str, Any]
    setting: str
    changed_value: Any


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
            {"transport": "subprocess"},
            "transport",
            "python_api",
        ),
        _SettingCase(
            "AlphaMELTS model",
            Engine.ALPHAMELTS,
            alpha_backend,
            lambda backend: setattr(backend, "_model", "pMELTS"),
            lambda backend: {"MELTS-v1.0": 0.25, "pMELTS": 0.75}[
                backend._model
            ],
            {"transport": "subprocess", "raw_model_name": "MELTSv1.0.2"},
            "raw_model_name",
            "pMELTS",
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
            {
                "transport": "python_api",
                "python_api_model": ("MELTSv1.0.2", 1),
            },
            "python_api_model",
            ["pMELTS", 1],
        ),
        _SettingCase(
            "AlphaMELTS redox buffer",
            Engine.ALPHAMELTS,
            alpha_backend,
            lambda backend: setattr(backend, "_redox_buffer", "NNO"),
            lambda backend: {"QFM": 0.25, "NNO": 0.75}[backend._redox_buffer],
            {"transport": "subprocess", "redox_buffer": "QFM"},
            "redox_buffer",
            "NNO",
        ),
        _SettingCase(
            "AlphaMELTS fO2 offset",
            Engine.ALPHAMELTS,
            alpha_backend,
            lambda backend: setattr(backend, "_fo2_offset", 0.5),
            lambda backend: {0.0: 0.25, 0.5: 0.75}[backend._fo2_offset],
            {"transport": "subprocess", "fO2_offset": 0.0},
            "fO2_offset",
            0.5,
        ),
        _SettingCase(
            "AlphaMELTS Fe3+/Fet ratio",
            Engine.ALPHAMELTS,
            alpha_backend,
            lambda backend: setattr(backend, "_fe3fet_ratio", 0.3),
            lambda backend: {None: 0.25, 0.3: 0.75}[backend._fe3fet_ratio],
            {"transport": "subprocess", "Fe3Fet_ratio": None},
            "Fe3Fet_ratio",
            0.3,
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
            {
                "transport": "subprocess",
                "configured_vapor_transport_pO2_bar": 1.0e-8,
            },
            "configured_vapor_transport_pO2_bar",
            1.0e-3,
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
            {
                "transport": "subprocess",
                "configured_vapor_transport_floor_bar": 1.0e-9,
            },
            "configured_vapor_transport_floor_bar",
            1.0e-6,
        ),
        _SettingCase(
            "MAGEMin database",
            Engine.MAGEMIN,
            magemin_backend,
            lambda backend: setattr(backend, "_database", "mp"),
            lambda backend: {"ig": 0.25, "mp": 0.75}[backend._database],
            {"database": "ig"},
            "database",
            "mp",
        ),
        _SettingCase(
            "MAGEMin worker fO2 buffer",
            Engine.MAGEMIN,
            magemin_backend,
            lambda backend: backend._config.__setitem__("fO2_buffer", "nno"),
            lambda backend: {"qfm": 0.25, "nno": 0.75}[
                backend._config["fO2_buffer"]
            ],
            {"fO2_buffer": "qfm"},
            "fO2_buffer",
            "nno",
        ),
        _SettingCase(
            "MAGEMin bridge selection",
            Engine.MAGEMIN,
            magemin_backend,
            lambda backend: setattr(backend, "_bridge", "julia"),
            lambda backend: {"subprocess": 0.25, "julia": 0.75}[backend._bridge],
            {"bridge": "subprocess"},
            "bridge",
            "julia",
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
            {"model": "MELTSv1.0.2"},
            "model",
            "rhyolite-MELTS",
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
            {"configured_vapor_transport_pO2_bar": 1.0e-9},
            "configured_vapor_transport_pO2_bar",
            1.0e-3,
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
            {"configured_vapor_transport_floor_bar": 1.0e-9},
            "configured_vapor_transport_floor_bar",
            1.0e-6,
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
            {"pack": "openimcc-v1.0.2"},
            "pack",
            "openimcc-v1.0.3-ext-v1",
        ),
        _SettingCase(
            "VapoRock temperature units",
            Engine.VAPOROCK,
            vaporock_backend,
            lambda backend: setattr(backend, "_temperature_units", "K"),
            lambda backend: {"C": 0.25, "K": 0.75}[backend._temperature_units],
            {"temperature_units": "C"},
            "temperature_units",
            "K",
        ),
        _SettingCase(
            "VapoRock pressure units",
            Engine.VAPOROCK,
            vaporock_backend,
            lambda backend: setattr(backend, "_pressure_units", "Pa"),
            lambda backend: {"bar": 0.25, "Pa": 0.75}[backend._pressure_units],
            {"pressure_units": "bar"},
            "pressure_units",
            "Pa",
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
            {"vapor_pressure_units": "bar"},
            "vapor_pressure_units",
            "Pa",
        ),
    )


@pytest.mark.parametrize("case", _setting_cases(), ids=lambda case: case.label)
def test_each_projected_engine_setting_changes_the_key(case: _SettingCase) -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        normalize_engine_settings,
    )
    from simulator.engine_cache import (
        ENGINE_CHEMISTRY_VERSION,
        engine_cache_key,
        engine_cache_settings,
    )

    backend = case.backend_factory()
    configuration = dict(case.configuration)
    before_settings = engine_cache_settings(
        case.engine.value, normalize_engine_settings(case.engine.value, configuration)
    )
    before_key = engine_cache_key(
        case.engine.value,
        ENGINE_CHEMISTRY_VERSION[case.engine.value],
        {"temperature_K": 1600.0, "settings": before_settings},
    )
    assert case.output(backend) == 0.25

    case.mutate(backend)
    configuration[case.setting] = case.changed_value
    after_settings = engine_cache_settings(
        case.engine.value, normalize_engine_settings(case.engine.value, configuration)
    )
    after_key = engine_cache_key(
        case.engine.value,
        ENGINE_CHEMISTRY_VERSION[case.engine.value],
        {"temperature_K": 1600.0, "settings": after_settings},
    )

    assert case.output(backend) == 0.75
    assert before_settings != after_settings, case.label
    assert before_key != after_key, case.label


@pytest.mark.parametrize(
    ("engine", "expected_fields"),
    (
        (Engine.OPENIMCC, {"melt_pack", "gas_pack"}),
        (Engine.INTERNAL_ANALYTICAL, {"oxygen_balance_backend", "high_t_melt_activity"}),
        (
            Engine.ALPHAMELTS,
            {
                "transport_mode",
                "effective_calc_mode",
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
            {"temperature_units", "pressure_units", "vapor_pressure_units"},
        ),
        (
            Engine.MAGEMIN,
            {"database", "bridge", "fO2_buffer"},
        ),
        (
            Engine.THERMOENGINE,
            {
                "model",
                "vapor_transport_pO2_bar",
                "vapor_transport_floor_bar",
                "redox_buffer",
                "fo2_offset",
                "fe3fet_ratio",
            },
        ),
    ),
)
def test_engine_cache_settings_are_the_named_prediction_projection(
    engine: Engine,
    expected_fields: set[str],
) -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        normalize_engine_settings,
    )
    from simulator.engine_cache import engine_cache_settings

    settings = engine_cache_settings(
        engine.value, normalize_engine_settings(engine.value, None)
    )

    assert set(settings) == expected_fields


def test_normalized_engine_defaults_match_backend_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import environment
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _InternalAnalyticalBatteryBackend,
        _OpenImccBatteryBackend,
        normalize_engine_settings,
    )
    from simulator.melt_backend import openimcc_bridge
    from simulator.melt_backend import alphamelts as alphamelts_module
    from simulator.melt_backend.alphamelts import AlphaMELTSBackend
    from simulator.melt_backend.magemin import MAGEMinBackend
    from simulator.melt_backend.thermoengine import ThermoEngineBackend
    from simulator.melt_backend.vaporock import VapoRockBackend

    monkeypatch.setattr(
        environment,
        "vacuum_floor_bar_for_environment",
        lambda: environment.DEFAULT_VACUUM_FLOOR_BAR,
    )
    normalized = {
        engine: normalize_engine_settings(engine, None)
        for engine in (
            "internal-analytical",
            "openimcc",
            "thermoengine",
            "vaporock",
            "magemin",
        )
    }

    # Omitted transport is discovery. Exercise both outcomes through the
    # backend resolver and require the cache normalizer to use that result.
    monkeypatch.setattr(
        "simulator.engine_local_config.find_alphamelts_binary",
        lambda _root: None,
        raising=False,
    )
    monkeypatch.setattr(
        alphamelts_module,
        "_run_alphamelts_subprocess",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )
    for binary_available, expected_transport in (
        (False, "python_api"),
        (True, "subprocess"),
    ):
        binary_path = Path("/test/alphamelts2") if binary_available else None
        monkeypatch.setattr(
            AlphaMELTSBackend,
            "_find_project_binary",
            lambda _backend, _root, result=binary_path: result,
        )
        for requested_transport in (None, "", "auto"):
            alpha = AlphaMELTSBackend()
            resolved_transport = (
                alphamelts_module.resolve_alphamelts_transport_mode(
                    requested_transport, backend=alpha
                )
            )
            alpha_settings = normalize_engine_settings(
                "alphamelts", {"transport": requested_transport}
            )
            assert resolved_transport == expected_transport
            assert alpha_settings["transport"] == resolved_transport
            assert alpha._normalize_mode(requested_transport) is None
            if not binary_available and requested_transport is None:
                normalized["alphamelts"] = alpha_settings

    internal = _InternalAnalyticalBatteryBackend()
    assert normalized["internal-analytical"]["oxygen_balance_backend"] == {
        "pack": internal._oxygen_balance_pack
    }

    bridge_pack = openimcc_bridge.DEFAULT_BACKEND_PACK_NAME
    pack = SimpleNamespace(
        model_id="test",
        version="test",
        kernel_datapack=SimpleNamespace(domains=()),
    )
    monkeypatch.setattr(
        openimcc_bridge,
        "_require_openimcc",
        lambda: SimpleNamespace(__version__="test"),
    )
    monkeypatch.setattr(openimcc_bridge, "_load_pack", lambda _name: pack)
    monkeypatch.setattr(openimcc_bridge, "_pack_digest", lambda _pack: "test")
    monkeypatch.setattr(openimcc_bridge, "imported_openimcc_commit", lambda: None)
    monkeypatch.setattr(
        openimcc_bridge,
        "engine_binding_identity",
        lambda *_args: {"engine_binding_digest": "test"},
    )
    openimcc = _OpenImccBatteryBackend("openimcc")
    assert normalized["openimcc"]["pack"] == openimcc._pack_name == bridge_pack
    assert (
        normalized["openimcc"]["gas_pack"]
        == openimcc._gas_pack_name
        == openimcc_bridge.DEFAULT_GAS_PACK_SELECTION
    )

    monkeypatch.setattr(
        AlphaMELTSBackend,
        "_find_project_binary",
        lambda _backend, _root: None,
    )
    alpha = AlphaMELTSBackend()
    alpha._vaporock_helper = SimpleNamespace(
        initialize=lambda _config: None,
        is_available=lambda: True,
    )
    alpha._import_petthermotools = lambda: SimpleNamespace()
    alpha._preload_petthermotools_payload = lambda _module: None
    assert alpha.initialize({})
    assert normalized["alphamelts"]["transport"] == alpha._mode == "python_api"
    assert normalized["alphamelts"]["raw_model_name"] == alpha._raw_model_name
    assert normalized["alphamelts"]["effective_calc_mode"] == (
        alpha._effective_subprocess_calc_mode
    )
    assert normalized["alphamelts"]["redox_buffer"] == alpha._redox_buffer
    assert normalized["alphamelts"]["fO2_offset"] == alpha._fo2_offset
    assert normalized["alphamelts"]["Fe3Fet_ratio"] == alpha._fe3fet_ratio
    assert normalized["alphamelts"]["python_api_model"] == alpha._python_api_model
    assert normalized["alphamelts"]["configured_vapor_transport_pO2_bar"] == (
        alpha._configured_vapor_transport_pO2_bar
    )
    assert normalized["alphamelts"]["configured_vapor_transport_floor_bar"] == (
        alpha._configured_vapor_transport_floor_bar
    )

    thermo = ThermoEngineBackend()
    thermo._initialize_vapor_transport({})
    assert normalized["thermoengine"]["model"] == thermo._model
    assert normalized["thermoengine"]["redox_buffer"] == thermo._redox_buffer
    assert normalized["thermoengine"]["fO2_offset"] == thermo._fo2_offset
    assert normalized["thermoengine"]["Fe3Fet_ratio"] == thermo._fe3fet_ratio
    assert normalized["thermoengine"]["configured_vapor_transport_pO2_bar"] == (
        thermo._configured_vapor_transport_pO2_bar
    )
    assert normalized["thermoengine"]["configured_vapor_transport_floor_bar"] == (
        thermo._configured_vapor_transport_floor_bar
    )

    vaporock = VapoRockBackend()
    try:
        vaporock.initialize({})
        assert normalized["vaporock"]["temperature_units"] == (
            vaporock._temperature_units
        )
        assert normalized["vaporock"]["pressure_units"] == vaporock._pressure_units
        assert normalized["vaporock"]["vapor_pressure_units"] == (
            vaporock._vapor_pressure_units
        )
    finally:
        vaporock.close()

    magemin = MAGEMinBackend()
    magemin._binary_path = Path("MAGEMin")
    resolved_bridge, _ = magemin._import_magemin_bridge(requested=None)
    resolved_buffer, _buffer_offset, _buffer_warnings = magemin._resolve_buffer(
        temperature_C=1400.0,
        fO2_log=-9.0,
    )
    assert normalized["magemin"]["database"] == magemin._effective_database()
    assert normalized["magemin"]["bridge"] == resolved_bridge
    assert normalized["magemin"]["fO2_buffer"] == magemin._effective_fO2_buffer()
    assert normalized["magemin"]["fO2_buffer"] == resolved_buffer


def test_default_and_test_configuration_cache_keys_are_byte_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.melt_backend import alphamelts as alphamelts_module
    from simulator.melt_backend.alphamelts import AlphaMELTSBackend
    from simulator import environment
    from simulator import engine_cache
    from simulator.diagnostic_helpers.binary_pot_battery import (
        normalize_engine_settings,
    )
    from simulator.engine_cache import (
        ENGINE_CHEMISTRY_VERSION,
        engine_cache_key,
        engine_cache_settings,
    )

    def expected_current_key(engine: str, legacy_wire_hex: str) -> bytes:
        # These snapshots pin the canonical request tail; engine and chemistry
        # version are the complete key header.
        return (
            bytes((engine_cache._ENGINE_NUMBERS[engine],))
            + ENGINE_CHEMISTRY_VERSION[engine].to_bytes(2, "big")
            + bytes.fromhex(legacy_wire_hex[6:])
        )

    monkeypatch.setattr(
        environment,
        "vacuum_floor_bar_for_environment",
        lambda: environment.DEFAULT_VACUUM_FLOOR_BAR,
    )
    monkeypatch.setattr(
        "simulator.engine_local_config.find_alphamelts_binary",
        lambda _root: None,
        raising=False,
    )
    monkeypatch.setattr(
        AlphaMELTSBackend,
        "_find_project_binary",
        lambda _backend, _root: None,
    )
    monkeypatch.setattr(
        alphamelts_module,
        "_run_alphamelts_subprocess",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )
    # Leading bytes are the chemistry-version pin (engine number, u16 version).
    # Markova and Holzheid require one combined internal-analytical v5 bump,
    # so the version-derived prefix must follow.
    # The comparison rebuilds the header from ENGINE_CHEMISTRY_VERSION and
    # keeps the payload tail pinned.
    snapshots = (
        (
            "internal-analytical default",
            "internal-analytical",
            None,
            "0200040008018b08020514686967685f745f6d656c745f616374697669747905086f70656e696d636305166f787967656e5f62616c616e63655f6261636b656e64050764656661756c74",
        ),
        (
            "openimcc default",
            "openimcc",
            None,
            "0100010008018b080205086761735f7061636b050764656661756c7405096d656c745f7061636b050764656661756c74",
        ),
        (
            "AlphaMELTS default",
            "alphamelts",
            None,
            "050004ff7708a47acb6e5ca7dafe87caabd285f5",
        ),
        (
            "AlphaMELTS Python API default model",
            "alphamelts",
            {"transport": "python_api"},
            "050004ff7708a47acb6e5ca7dafe87caabd285f5",
        ),
        (
            "ThermoEngine default",
            "thermoengine",
            None,
            "060004ff2330afdabce1130820fde70d072941aa",
        ),
        (
            "ThermoEngine explicit None defaults",
            "thermoengine",
            {
                "model": None,
                "configured_vapor_transport_pO2_bar": None,
                "configured_vapor_transport_floor_bar": None,
            },
            "060004ff2330afdabce1130820fde70d072941aa",
        ),
        (
            "VapoRock default",
            "vaporock",
            None,
            "0300010008018b0803050e70726573737572655f756e6974730503626172051174656d70657261747572655f756e69747305014305147661706f725f70726573737572655f756e6974730503626172",
        ),
        (
            "VapoRock explicit None units",
            "vaporock",
            {
                "temperature_units": None,
                "pressure_units": None,
                "vapor_pressure_units": None,
            },
            "0300010008018b0803050e70726573737572655f756e6974730503626172051174656d70657261747572655f756e69747305014305147661706f725f70726573737572655f756e6974730503626172",
        ),
        (
            "MAGEMin default",
            "magemin",
            None,
            "0400040008018b08030506627269646765050a73756270726f636573730508646174616261736505026967050a664f325f627566666572050371666d",
        ),
        (
            "MAGEMin explicit None defaults",
            "magemin",
            {
                "database": None,
                "bridge": None,
                "python_bridge": None,
                "fO2_buffer": None,
            },
            "0400040008018b08030506627269646765050a73756270726f636573730508646174616261736505026967050a664f325f627566666572050371666d",
        ),
    )
    mismatches = {}
    for label, engine, configuration, legacy_wire_hex in snapshots:
        normalized = normalize_engine_settings(engine, configuration)
        key = engine_cache_key(
            engine,
            ENGINE_CHEMISTRY_VERSION[engine],
            {"engine_settings": engine_cache_settings(engine, normalized)},
        )
        if key != expected_current_key(engine, legacy_wire_hex):
            mismatches[label] = key.hex()

    case_legacy_wire_hex = {
        "AlphaMELTS transport mode": (
            "050004ff08aef82d27a383e073ad83ef3a95a1f5"
        ),
        "AlphaMELTS model": (
            "050004ff08aef82d27a383e073ad83ef3a95a1f5"
        ),
        "AlphaMELTS Python API model": (
            "050004ff7708a47acb6e5ca7dafe87caabd285f5"
        ),
        "AlphaMELTS redox buffer": (
            "050004ff84021d1fcf18c54719c9792ae3309859"
        ),
        "AlphaMELTS fO2 offset": (
            "050004ff8b7a9acff52ddfac563117c0e162ab62"
        ),
        "AlphaMELTS Fe3+/Fet ratio": (
            "050004ff08aef82d27a383e073ad83ef3a95a1f5"
        ),
        "AlphaMELTS configured vapor pO2": (
            "050004ffed5e2dd0542d245c6de74c7e5b9191bb"
        ),
        "AlphaMELTS configured vapor floor": (
            "050004ff08aef82d27a383e073ad83ef3a95a1f5"
        ),
        "MAGEMin database": (
            "0400040008018b08030506627269646765050a73756270726f636573730508646174616261736505026967050a664f325f627566666572050371666d"
        ),
        "MAGEMin worker fO2 buffer": (
            "0400040008018b08030506627269646765050a73756270726f636573730508646174616261736505026967050a664f325f627566666572050371666d"
        ),
        "MAGEMin bridge selection": (
            "0400040008018b08030506627269646765050a73756270726f636573730508646174616261736505026967050a664f325f627566666572050371666d"
        ),
        "ThermoEngine transport model": (
            "060004ff2330afdabce1130820fde70d072941aa"
        ),
        "ThermoEngine vapor transport pO2": (
            "060004ff2330afdabce1130820fde70d072941aa"
        ),
        "ThermoEngine vapor transport floor": (
            "060004ff2330afdabce1130820fde70d072941aa"
        ),
        "OpenIMCC loaded cell pack": (
            "0100010008018b080205086761735f7061636b050764656661756c7405096d656c745f7061636b050f6f70656e696d63632d76312e302e32"
        ),
        "VapoRock temperature units": (
            "0300010008018b0803050e70726573737572655f756e6974730503626172051174656d70657261747572655f756e69747305014305147661706f725f70726573737572655f756e6974730503626172"
        ),
        "VapoRock pressure units": (
            "0300010008018b0803050e70726573737572655f756e6974730503626172051174656d70657261747572655f756e69747305014305147661706f725f70726573737572655f756e6974730503626172"
        ),
        "VapoRock vapor pressure units": (
            "0300010008018b0803050e70726573737572655f756e6974730503626172051174656d70657261747572655f756e69747305014305147661706f725f70726573737572655f756e6974730503626172"
        ),
    }
    for case in _setting_cases():
        normalized = normalize_engine_settings(case.engine.value, case.configuration)
        key = engine_cache_key(
            case.engine.value,
            ENGINE_CHEMISTRY_VERSION[case.engine.value],
            {
                "engine_settings": engine_cache_settings(
                    case.engine.value, normalized
                )
            },
        )
        if key != expected_current_key(
            case.engine.value, case_legacy_wire_hex[case.label]
        ):
            mismatches[case.label] = key.hex()
    assert not mismatches, repr(mismatches)


def test_default_alphamelts_projection_matches_resolved_opener_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import open_battery_engine
    from simulator.engine_cache import (
        ENGINE_CHEMISTRY_VERSION,
        engine_cache_key,
        engine_cache_settings,
    )

    handle = open_battery_engine(Engine.ALPHAMELTS.value)
    if not handle.available or handle.backend is None:
        pytest.skip("default AlphaMELTS backend is unavailable")
    try:
        normalized_projection = engine_cache_settings(handle.name, handle.engine_settings)
        requests, projection_inputs = _capture_engine_cache_settings_paths(
            handle.name,
            handle.engine_settings,
            handle.backend,
            monkeypatch,
        )
        assert len(requests) == 2
        assert projection_inputs[0] is handle.engine_settings
        for request in requests:
            assert engine_cache_key(
                handle.name,
                ENGINE_CHEMISTRY_VERSION[handle.name],
                {"engine_settings": request["engine_settings"]},
            ) == engine_cache_key(
                handle.name,
                ENGINE_CHEMISTRY_VERSION[handle.name],
                {"engine_settings": normalized_projection},
            )
    finally:
        close_battery_engine_handle(handle)


@pytest.mark.parametrize("isolated", (False, True))
def test_alphamelts_builtin_fe_notice_survives_binary_cell_cache_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated: bool,
) -> None:
    from simulator import engine_cache
    from simulator.battery.enums import Authority, Quantity
    from simulator.battery.score import cell_notices
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    from simulator.diagnostic_helpers.binary_pot_battery import (
        BinaryPot,
        Po2Request,
        close_battery_engine_handle,
        equilibrate_cell,
    )
    from tests.battery.cache_test_support import skip_if_engine_missing

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "alphamelts.sqlite"))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_MODE", raising=False)
    engine_cache._MEMORY_CACHE.clear()
    engine_cache.engine_cache_counters(reset=True)
    handle = battery._open_battery_engine_with_settings(
        "alphamelts", {"transport": "subprocess"}
    )
    if not handle.available or handle.backend is None:
        skip_if_engine_missing(
            handle,
            "alphamelts",
            nodeid="builtin Fe pressure notice cache round trip",
        )
    try:
        assert getattr(handle.backend, "_mode", None) == "subprocess"
        pot = BinaryPot(
            pot_id="fe-holzheid-alphamelts-cache",
            kato_1993_table4_system=None,
            why="scoped builtin Fe pressure notice cache probe",
            composition_wt_pct={"FeO": 20.0, "Na2O": 20.0, "SiO2": 60.0},
        )
        cold = equilibrate_cell(
            handle,
            pot,
            temperature_K=1878.15,
            po2=Po2Request(mode="commanded", po2_bar=1.0e-8),
            isolated=isolated,
        )
        engine_cache._MEMORY_CACHE.clear()
        warm = equilibrate_cell(
            handle,
            pot,
            temperature_K=1878.15,
            po2=Po2Request(mode="commanded", po2_bar=1.0e-8),
            isolated=isolated,
        )
    finally:
        close_battery_engine_handle(handle)

    reason = "Holzheid stoichiometric FeO gamma conversion extrapolated"
    for cell in (cold, warm):
        assert cell.status == "ok", cell.engine_reason
        assert cell.gas_partial_pressures_Pa["Fe"] > 0.0
        fe_notices = cell_notices(
            Quantity.P_PARTIAL,
            Engine.ALPHAMELTS,
            cell,
            species="Fe",
        )
        matching_notices = [notice for notice in fe_notices if notice.reason == reason]
        assert len(matching_notices) == 1
        # The builtin out-of-band record has no authority field, and the
        # typed converter preserves that. The cell authority resolver still
        # reports extrapolation for the scoped Fe pressure below.
        assert matching_notices[0].authority is None
        assert not any(
            notice.reason == reason
            for notice in cell_notices(
                Quantity.P_PARTIAL,
                Engine.ALPHAMELTS,
                cell,
                species="SiO",
            )
        )
        assert battery.cell_score_authority(
            cell,
            refused=False,
            quantity=Quantity.P_PARTIAL.value,
            species="Fe",
        ) == battery.AUTHORITY_EXTRAPOLATED
    assert cold.notices == warm.notices
    assert engine_cache.engine_cache_counters(reset=True)["alphamelts"] == {
        "hits": 1,
        "misses": 1,
        "stores": 1,
        "store_error": 0,
        "read_error": 0,
        "conflicts": 0,
    }


def test_internal_residue_builtin_fe_notice_survives_cache_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_cache
    from simulator.battery.enums import Quantity
    from simulator.battery.score import cell_notices
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    from simulator.diagnostic_helpers.binary_pot_battery import (
        PO2_COMMANDED,
        Po2Request,
        engine_flags_from_result,
        internal_analytical_residue_vapor_pressure,
    )

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "residue.sqlite"))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_MODE", raising=False)
    engine_cache._MEMORY_CACHE.clear()
    engine_cache.engine_cache_counters(reset=True)
    request = {
        "core": battery._new_internal_analytical_core(),
        "temperature_C": 1605.0,
        "pressure_bar": 1.0,
        "composition_kg": {"FeO": 0.2, "Na2O": 0.2, "SiO2": 0.6},
        "composition_mol": None,
        "fO2_log": -8.0,
        "po2_request": Po2Request(PO2_COMMANDED, 1.0e-8),
        "include_diagnostic_shadows": False,
    }

    cold = internal_analytical_residue_vapor_pressure(**request)
    engine_cache._MEMORY_CACHE.clear()
    warm = internal_analytical_residue_vapor_pressure(**request)

    reason = "Holzheid stoichiometric FeO gamma conversion extrapolated"
    for result in (cold, warm):
        flags, authority, _band = engine_flags_from_result(result)
        scored = SimpleNamespace(notices=flags, authority=authority)
        assert any(
            notice.reason == reason
            for notice in cell_notices(
                Quantity.P_PARTIAL,
                Engine.INTERNAL_ANALYTICAL,
                scored,
                species="Fe",
            )
        )
        assert not any(
            notice.reason == reason
            for notice in cell_notices(
                Quantity.P_PARTIAL,
                Engine.INTERNAL_ANALYTICAL,
                scored,
                species="SiO",
            )
        )
    assert cold.vapor_pressures_Pa == warm.vapor_pressures_Pa
    assert engine_cache.engine_cache_counters(reset=True)["internal-analytical"][
        "hits"
    ] == 1


def _capture_engine_cache_settings_paths(
    engine: str,
    normalized_settings: dict[str, Any],
    backend: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[dict[str, Any]], list[Any]]:
    from threading import Lock

    from simulator import engine_cache
    from simulator.diagnostic_helpers import binary_pot_battery

    requests: list[dict[str, Any]] = []
    projection_inputs: list[Any] = []
    original_projection = engine_cache.engine_cache_settings

    def record_projection(name: str, settings: Any, **kwargs: Any) -> dict[str, Any]:
        projection_inputs.append(settings)
        return original_projection(name, settings, **kwargs)

    def cached_call(
        _name: str, request: dict[str, Any], solve: Callable[[], Any]
    ) -> Any:
        requests.append(dict(request))
        return solve()

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", "test")
    monkeypatch.setattr(engine_cache, "engine_cache_settings", record_projection)
    monkeypatch.setattr(engine_cache, "cached_engine_call", cached_call)

    if engine != "openimcc":
        handle = binary_pot_battery.EngineHandle(
            name=engine,
            backend=backend,
            available=True,
            unavailable_reason=None,
            takes_fo2=False,
            supports_intrinsic_fo2=False,
            engine_settings=normalized_settings,
        )
        binary_pot_battery._call_cached_backend_equilibrate(
            handle, {}, lambda: None
        )

    worker = object.__new__(binary_pot_battery._IsolatedCellWorker)
    worker.engine = engine
    worker.engine_settings = dict(normalized_settings)
    worker._lock = Lock()
    worker._worker = SimpleNamespace(
        call=lambda _payload, *, timeout_s: {"status": "refusal"}
    )
    worker.request(
        {
            "composition_wt_pct": {},
            "temperature_K": 1600.0,
            "po2": {},
        },
        timeout_s=1.0,
    )
    return requests, projection_inputs


@pytest.mark.parametrize(
    "engine",
    (
        "internal-analytical",
        "alphamelts",
        "thermoengine",
        "vaporock",
        "magemin",
        "openimcc",
    ),
)
def test_default_engine_cache_settings_match_across_call_paths(
    engine: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        normalize_engine_settings,
    )
    from simulator.engine_cache import (
        ENGINE_CHEMISTRY_VERSION,
        engine_cache_key,
        engine_cache_settings,
    )

    normalized = normalize_engine_settings(engine, None)
    expected_projection = engine_cache_settings(engine, normalized)
    expected_key = engine_cache_key(
        engine,
        ENGINE_CHEMISTRY_VERSION[engine],
        {"engine_settings": expected_projection},
    )
    case = next(
        (case for case in _setting_cases() if case.engine.value == engine), None
    )
    from simulator.diagnostic_helpers.binary_pot_battery import _InternalAnalyticalBatteryBackend

    backend = (
        _InternalAnalyticalBatteryBackend() if engine == "internal-analytical"
        else object() if case is None else case.backend_factory()
    )

    requests, projection_inputs = _capture_engine_cache_settings_paths(
        engine, normalized, backend, monkeypatch
    )

    expected_request_count = (
        1 if engine == "openimcc" else 2
    )
    assert len(requests) == expected_request_count
    if engine == "internal-analytical":
        assert expected_projection["oxygen_balance_backend"] == normalized["oxygen_balance_backend"]["pack"]
        assert len(projection_inputs) == 2

    for request in requests:
        assert engine_cache_key(
            engine,
            ENGINE_CHEMISTRY_VERSION[engine],
            {"engine_settings": request["engine_settings"]},
        ) == expected_key

    if engine not in {"internal-analytical", "openimcc"}:
        assert projection_inputs[0] is normalized
        assert dict(projection_inputs[1]) == normalized
    else:
        assert dict(projection_inputs[0]) == normalized


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
    from simulator.diagnostic_helpers.binary_pot_battery import (
        normalize_engine_settings,
    )
    from simulator.engine_cache import (
        ENGINE_CHEMISTRY_VERSION,
        engine_cache_key,
        engine_cache_settings,
    )

    first = engine_cache_settings(
        engine, normalize_engine_settings(engine, configuration)
    )
    first_key = engine_cache_key(
        engine, ENGINE_CHEMISTRY_VERSION[engine], {"settings": first}
    )
    configuration[setting] = changed
    second = engine_cache_settings(
        engine, normalize_engine_settings(engine, configuration)
    )
    second_key = engine_cache_key(
        engine, ENGINE_CHEMISTRY_VERSION[engine], {"settings": second}
    )

    assert first_key != second_key


@pytest.mark.parametrize(
    ("engine", "configuration", "field", "changed"),
    (
        ("openimcc", {"pack": "core"}, "pack", "extended"),
        ("alphamelts", {"transport": "subprocess"}, "transport", "python_api"),
        ("alphamelts", {"ambient_calc_mode": "MELTS"}, "ambient_calc_mode", "pMELTS"),
        (
            "alphamelts",
            {"raw_model_name": "MELTSv1.2.0"},
            "raw_model_name",
            "MELTSv1.1.0",
        ),
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
    from simulator.diagnostic_helpers.binary_pot_battery import (
        normalize_engine_settings,
    )
    from simulator.engine_cache import engine_cache_settings

    before = engine_cache_settings(
        engine, normalize_engine_settings(engine, configuration)
    )
    configuration[field] = changed

    assert engine_cache_settings(
        engine, normalize_engine_settings(engine, configuration)
    ) != before, field


@pytest.mark.parametrize(
    "engine",
    ("openimcc", "alphamelts", "vaporock", "magemin", "thermoengine"),
)
def test_worker_scheduling_config_does_not_change_prediction_settings(engine: str) -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        normalize_engine_settings,
    )
    from simulator.engine_cache import (
        ENGINE_CHEMISTRY_VERSION,
        ENGINE_SETTINGS_PREDICTION_NEUTRAL_REASONS,
        engine_cache_key,
        engine_cache_settings,
    )

    reason = ENGINE_SETTINGS_PREDICTION_NEUTRAL_REASONS[engine]["warm_worker"]
    configuration = {"warm_worker": False}
    before = engine_cache_settings(
        engine, normalize_engine_settings(engine, configuration)
    )
    configuration["warm_worker"] = True

    assert reason
    assert engine_cache_settings(
        engine, normalize_engine_settings(engine, configuration)
    ) == before


def test_engine_opener_and_cache_share_canonical_settings_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import backends
    from simulator.diagnostic_helpers import binary_pot_battery
    from simulator.engine_cache import (
        ENGINE_CHEMISTRY_VERSION,
        engine_cache_key,
        engine_cache_settings,
    )
    from simulator.melt_backend.alphamelts import (
        AlphaMELTSBackend,
        AlphaMELTSSubprocessContractError,
        AlphaMELTSSubprocessRunMode,
    )

    normalize_engine_settings = binary_pot_battery.normalize_engine_settings
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
        assert projection_key(p_melts_handle.engine_settings) != projection_key(
            melts_handle.engine_settings
        )
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
    from simulator.engine_cache import engine_cache_settings
    from simulator import backends
    from simulator.diagnostic_helpers import binary_pot_battery
    normalize_engine_settings = binary_pot_battery.normalize_engine_settings

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
    from simulator.engine_cache import engine_cache_settings
    normalize_engine_settings = binary_pot_battery.normalize_engine_settings

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
    from simulator.diagnostic_helpers.binary_pot_battery import (
        normalize_engine_settings,
    )
    from simulator.engine_cache import engine_cache_settings

    assert engine_cache_settings(
        "openimcc", normalize_engine_settings("openimcc", {"pack": "default"})
    ) == (
        engine_cache_settings(
            "openimcc",
            normalize_engine_settings(
                "openimcc",
                {"pack": "default", "gas_pack": "unused-custom-pack"},
            ),
        )
    )


@pytest.mark.parametrize("engine", ("alphamelts", "thermoengine"))
def test_melts_redox_projection_covers_shared_normalizer_inputs(engine: str) -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        normalize_engine_settings,
    )
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

    projected = engine_cache_settings(
        engine, normalize_engine_settings(engine, settings)
    )

    for name, value in expected.items():
        assert projected[name] == value


@pytest.mark.parametrize(
    "engine,configuration",
    (
        ("openimcc", {}),
        ("alphamelts", {}),
        ("vaporock", {}),
        ("magemin", {}),
        ("thermoengine", {}),
        ("openimcc", {"pack": "published-core", "gas_pack": "ignored"}),
        (
            "alphamelts",
            {
                "transport": None,
                "raw_model_name": None,
                "configured_vapor_transport_pO2_bar": None,
                "configured_vapor_transport_floor_bar": None,
            },
        ),
        ("alphamelts", {"transport": "python_api"}),
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
                "temperature_units": None,
                "pressure_units": None,
                "vapor_pressure_units": None,
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
                "database": None,
                "bridge": None,
                "python_bridge": None,
                "fO2_buffer": None,
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
                "model": None,
                "configured_vapor_transport_pO2_bar": None,
                "configured_vapor_transport_floor_bar": None,
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
        ENGINE_CHEMISTRY_VERSION,
        ENGINE_SETTINGS_PREDICTION_NEUTRAL_REASONS,
        engine_cache_key,
        engine_cache_settings,
    )

    normalize_engine_settings = binary_pot_battery.normalize_engine_settings

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
        if engine == "alphamelts" and key == "transport":
            changed[key] = (
                "python_api"
                if projected["transport_mode"] != "python_api"
                else "subprocess"
            )
        elif engine == "alphamelts" and key == "raw_model_name":
            changed[key] = "pMELTS"
        elif isinstance(value, bool):
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
        assert engine_cache_settings(
            engine, normalize_engine_settings(engine, changed)
        ) != projected, key

    normalized_reads: list[RecordingMapping] = []
    constructor_reads: list[RecordingMapping] = []
    original_normalize = binary_pot_battery.normalize_engine_settings

    def record_normalized_mapping(
        name: str, settings: Mapping[str, Any] | None
    ) -> RecordingMapping:
        result = RecordingMapping(original_normalize(name, settings))
        normalized_reads.append(result)
        return result

    monkeypatch.setattr(
        binary_pot_battery, "normalize_engine_settings", record_normalized_mapping
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
    except RuntimeError as exc:
        if engine != "thermoengine" or "No module named 'thermoengine'" not in str(exc):
            raise
    finally:
        if opened_backend is not None:
            close_battery_engine_handle(
                SimpleNamespace(backend=opened_backend)
            )
    opener_settings = normalized_reads[-1]
    opener_projection = engine_cache_settings(engine, opener_settings)
    cache_backend = opened_backend or next(
        case.backend_factory()
        for case in _setting_cases()
        if case.engine.value == engine
    )
    cache_requests, projection_inputs = _capture_engine_cache_settings_paths(
        engine, opener_settings, cache_backend, monkeypatch
    )
    if engine == "openimcc":
        assert len(cache_requests) == 1
        assert dict(projection_inputs[0]) == dict(opener_settings)
    else:
        assert len(cache_requests) == 2
        assert projection_inputs[0] is opener_settings
        assert dict(projection_inputs[1]) == dict(opener_settings)
    expected_projection_key = engine_cache_key(
        engine,
        ENGINE_CHEMISTRY_VERSION[engine],
        {"engine_settings": opener_projection},
    )
    for request in cache_requests:
        assert engine_cache_key(
            engine,
            ENGINE_CHEMISTRY_VERSION[engine],
            {"engine_settings": request["engine_settings"]},
        ) == expected_projection_key
    assert opener_settings.reads <= set(opener_settings)
    if engine == "alphamelts":
        assert {
            "model_calc_mode",
            "calc_mode_conflict",
            "subprocess_model_error",
        }.isdisjoint(opener_settings)
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
                elif value is None:
                    changed[source] = "changed"
                else:
                    pytest.fail(
                        f"cannot mutate backend setting {engine}.{key}: {value!r}"
                    )
                assert engine_cache_settings(engine, changed) != constructor_projection, key
        if engine == "alphamelts":
            constructor_settings = constructor_reads[-1].values
            assert constructor_settings["model_calc_mode"] == "MELTS"
            assert constructor_settings["calc_mode_conflict"] is False
            assert constructor_settings["subprocess_model_error"] is None
    assert projected


def test_internal_analytical_projection_requires_normalized_inputs() -> None:
    from simulator.engine_cache import EngineCacheSettingsError, engine_cache_settings

    with pytest.raises(EngineCacheSettingsError):
        engine_cache_settings("internal-analytical", object())


@pytest.mark.parametrize(
    "case",
    tuple(case for case in _setting_cases() if case.engine is Engine.ALPHAMELTS),
    ids=lambda case: case.label,
)
def test_alphamelts_runtime_metadata_is_outside_the_prediction_projection(
    case: _SettingCase,
) -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        normalize_engine_settings,
    )
    from simulator.engine_cache import engine_cache_settings

    backend = case.backend_factory()
    settings = normalize_engine_settings(case.engine.value, case.configuration)
    before = engine_cache_settings(case.engine.value, settings)
    backend._binary_path = Path("/runtime/first")
    backend._engine_path = Path("/runtime/first")
    backend._engine_version = "runtime-v1"
    after = engine_cache_settings(case.engine.value, settings)

    assert after == before


@pytest.mark.parametrize("engine", CACHE_TEST_ENGINES, ids=lambda value: value.value)
def test_all_cacheable_real_cell_paths_preserve_scorer_fields_through_sqlite(
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    from simulator import engine_cache
    from simulator.diagnostic_helpers.binary_pot_battery import (
        EngineHandle,
        _OpenImccBatteryBackend,
        Po2Request,
        close_battery_engine_handle,
        equilibrate_cell,
        load_binary_pots,
        normalize_engine_settings,
        open_battery_engine,
    )
    from tests.battery.cache_test_support import missing_engine_reason

    monkeypatch.delenv("REGOLITH_ENGINE_CACHE", raising=False)
    handle = open_battery_engine(engine.value)
    using_recorded_result = not handle.available or handle.backend is None
    if using_recorded_result and engine is Engine.INTERNAL_ANALYTICAL:
        close_battery_engine_handle(handle)
        pytest.skip(missing_engine_reason(handle, engine.value))
    expected_nonempty_scorer_fields = {
        Engine.ALPHAMELTS: (
            "status",
            "engine_status",
            "liquid_composition_wt_pct",
        ),
        Engine.THERMOENGINE: (
            "status",
            "engine_status",
            "liquid_composition_wt_pct",
            "fe_redox_split",
        ),
        Engine.MAGEMIN: ("status", "engine_status"),
        Engine.INTERNAL_ANALYTICAL: ("status", "engine_status", "gas_partial_pressures_Pa"),
        Engine.VAPOROCK: (
            "status",
            "engine_status",
            "gas_partial_pressures_Pa",
        ),
        Engine.OPENIMCC: (
            "status",
            "engine_status",
            "melt_activities",
            "melt_activity_coefficients",
            "melt_activity_coefficient_details",
        ),
    }[engine]
    if using_recorded_result and engine is Engine.THERMOENGINE:
        expected_nonempty_scorer_fields += (
            "melt_activities",
            "vapor_pressure_backend_status",
            "vapor_pressure_backend_status_reason",
        )
    if using_recorded_result:
        missing_reason = missing_engine_reason(handle, engine.value)
        assert missing_reason is not None
        close_battery_engine_handle(handle)

        class _RecordedBackend:
            def __init__(self, result: Any) -> None:
                self.result = result

            def equilibrate(self, **_kwargs: Any) -> Any:
                return self.result

        recorded_openimcc_result = None
        if engine is Engine.ALPHAMELTS:
            from tests.battery.test_melts_fe_redox_known_engine_output import (
                _load as load_melts_fixture,
                _parse_alphamelts,
            )

            recorded_result = _parse_alphamelts(load_melts_fixture())
        elif engine is Engine.THERMOENGINE:
            fixture_path = (
                Path(__file__).resolve().parents[1]
                / "fixtures"
                / "engine_cache_cells"
                / "thermoengine-mgo-sio2-40-60-1700K.json"
            )
            fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
            recorded_result = EquilibriumResult.from_engine_cache_value(
                fixture["backend_result"], engine_id=engine.value
            )
            assert recorded_result.activity_coefficients
            assert recorded_result.liquid_composition_wt_pct
            assert recorded_result.fe_redox_split
        elif engine is Engine.MAGEMIN:
            from simulator.melt_backend.magemin import MAGEMinBackend

            recorded_result = EquilibriumResult(liquid_fraction=0.0)
            stdout = (
                Path(__file__).resolve().parents[1]
                / "fixtures"
                / "magemin"
                / "lfdrop-diag-1-1700-phase-mode.stdout"
            ).read_text(encoding="utf-8")
            magemin = MAGEMinBackend()
            magemin._populate_result(
                recorded_result,
                {"phases": magemin._parse_subprocess_stdout(stdout)},
            )
        elif engine is Engine.VAPOROCK:
            fixture_path = (
                Path(__file__).resolve().parents[1]
                / "fixtures"
                / "engine_cache_cells"
                / "vaporock-mgo-sio2-40-60-1700K.json"
            )
            fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
            recorded_result = EquilibriumResult.from_engine_cache_value(
                fixture["backend_result"], engine_id=engine.value
            )
            assert recorded_result.vaporock_full_speciation_Pa
        else:
            from simulator.melt_backend import openimcc_bridge

            fixture_path = (
                Path(__file__).resolve().parents[1]
                / "fixtures"
                / "openimcc_temperature_bridge.json"
            )
            fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
            record = fixture["records"]["c2b7b4e:62"]
            assert record["ok"] is True
            assert "allow_extrapolation=False" in fixture["provenance"]
            package_result = SimpleNamespace(
                parent_oxides=tuple(record["parent_oxides"]),
                parent_activity=tuple(record["parent_activity"]),
                parent_x=None,
                parent_x_star=None,
                labels=SimpleNamespace(
                    identity={
                        "model_id": "openimcc-recorded-fixture",
                        "datapack_version": "recorded",
                    },
                    coverage={},
                    flags=(),
                    notices=(),
                    acid_sink_ratio=None,
                    # This successful recorded call used strict evaluation;
                    # its envelope label was omitted from the older capture.
                    envelope_status="inside",
                ),
                extrapolated=False,
            )
            package = SimpleNamespace(
                __version__="recorded",
                evaluate=lambda *_args, **_kwargs: package_result,
            )
            monkeypatch.setattr(openimcc_bridge, "_require_openimcc", lambda: package)
            monkeypatch.setattr(openimcc_bridge, "_load_pack", lambda _pack: object())
            monkeypatch.setattr(
                openimcc_bridge,
                "_pack_digest",
                lambda _pack: fixture["provenance"],
            )
            recorded_openimcc_result = openimcc_bridge._evaluate_uncached(
                composition_mol=record["composition"],
                temperature_K=record["T_K"],
                pack="default",
            )
            assert recorded_openimcc_result.parent_oxide_activities
            assert recorded_openimcc_result.activity_coefficients
            assert all(
                row["value"] > 0.0
                and row["coefficient_basis"] == "single_cation"
                and row["standard_state"]
                for row in recorded_openimcc_result.activity_coefficients.values()
            )
            monkeypatch.setattr(
                openimcc_bridge,
                "_evaluate_uncached",
                lambda **_kwargs: recorded_openimcc_result,
            )
            backend = object.__new__(_OpenImccBatteryBackend)
            backend.engine_name = engine.value
            backend._bridge = openimcc_bridge
            backend._pack_name = "openimcc-v1.0.2"
            backend._gas_pack_name = "default"
            backend._identity = {"pack": backend._pack_name}
            backend.model_id = "openimcc-recorded-fixture"
            backend._gas = None
            backend._gas_error = None
            backend._certified_temperature_domain = None
            handle = EngineHandle(
                name=engine.value,
                backend=backend,
                available=True,
                unavailable_reason=None,
                takes_fo2=False,
                supports_intrinsic_fo2=False,
                engine_settings=normalize_engine_settings(engine.value, None),
            )

        if engine is not Engine.OPENIMCC:
            backend = _RecordedBackend(recorded_result)
            handle = EngineHandle(
                name=engine.value,
                backend=backend,
                available=True,
                unavailable_reason=None,
                takes_fo2=False,
                supports_intrinsic_fo2=False,
                engine_settings=normalize_engine_settings(engine.value, None),
            )
    pots, _metadata = load_binary_pots()
    if using_recorded_result and engine in (Engine.THERMOENGINE, Engine.VAPOROCK):
        pot = next(pot for pot in pots if pot.pot_id == "mgo_sio2_40_60")
        common = {
            "temperature_K": 1700.0,
            "po2": Po2Request(mode="engine_default", po2_bar=None),
            "timeout_s": 90.0,
            "isolated": False,
        }
    elif engine in (Engine.ALPHAMELTS, Engine.THERMOENGINE):
        from simulator.diagnostic_helpers.binary_pot_battery import BinaryPot
        from tests.battery.test_melts_fe_redox_known_engine_output import (
            _FIXTURE as melts_fixture_path,
        )

        fixture = json.loads(melts_fixture_path.read_text(encoding="utf-8"))
        conditions = fixture["provenance"]["conditions"]
        pot = BinaryPot(
            pot_id=f"{engine.value}_recorded_fe_cache_case",
            kato_1993_table4_system=None,
            why="Captured liquid-forming MELTS Fe-redox fixture composition.",
            composition_wt_pct=dict(conditions["composition_wt_pct"]),
        )
        common = {
            "temperature_K": float(conditions["temperature_C"]) + 273.15,
            "po2": Po2Request(
                mode="commanded",
                po2_bar=10.0 ** float(conditions["fO2_log"]),
            ),
            "timeout_s": 90.0,
            "isolated": False,
        }
    elif engine is Engine.VAPOROCK:
        import yaml
        from simulator.diagnostic_helpers.binary_pot_battery import BinaryPot

        fixture_path = (
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "corpus"
            / "wolf-2022-vaporock"
            / "benchmark-fixture.yaml"
        )
        fixture = yaml.safe_load(fixture_path.read_text(encoding="utf-8"))
        anchor = fixture["expected"]["vapor_partial_pressures_Pa"]["anchors"][0]
        pot = BinaryPot(
            pot_id="vaporock_recorded_bse_cache_case",
            kato_1993_table4_system=None,
            why="Captured Wolf et al. VapoRock BSE fixture composition.",
            composition_wt_pct=dict(fixture["feedstock"]["composition_wt_pct"]),
        )
        common = {
            "temperature_K": float(anchor["T_K"]),
            "po2": Po2Request(
                mode="commanded",
                po2_bar=10.0 ** float(anchor["fO2"]["fO2_log10_bar"]),
            ),
            "timeout_s": 90.0,
            "isolated": False,
        }
    elif engine is Engine.INTERNAL_ANALYTICAL:
        pot = next(pot for pot in pots if pot.pot_id == "mgo_sio2_40_60")
        common = {
            "temperature_K": 1700.0, "po2": Po2Request(mode="commanded", po2_bar=1e-8),
            "timeout_s": 90.0, "isolated": False,
        }
    else:
        pot = next(pot for pot in pots if pot.pot_id == "mgo_sio2_40_60")
        common = {
            "temperature_K": 1700.0,
            "po2": Po2Request(mode="engine_default", po2_bar=None),
            "timeout_s": 90.0,
            "isolated": False,
        }

    def stable(cell: Any) -> Any:
        return cell.to_engine_cache_value()

    def scorer_fields(cell: Any) -> dict[str, Any]:
        names = (
            "status",
            "refusal_reason",
            "engine_status",
            "engine_reason",
            "exit_signal",
            "exit_code",
            "model_id",
            "melt_activities",
            "gas_partial_pressures_Pa",
            "invalid_gas_partial_pressure_components",
            "melt_activity_coefficients",
            "melt_activity_coefficient_details",
            "vapor_pressure_backend_status",
            "vapor_pressure_backend_status_reason",
            "notices",
            "authority",
            "certified_band",
            "liquid_composition_wt_pct",
            "fe_redox_split",
        )
        return {name: getattr(cell, name) for name in names}

    try:
        cache_off = equilibrate_cell(handle, pot, **common)
        for name in expected_nonempty_scorer_fields:
            value = getattr(cache_off, name)
            assert value not in (None, "", {}, [], ())

        monkeypatch.setenv(
            "REGOLITH_ENGINE_CACHE", str(tmp_path / f"{engine.value}.sqlite")
        )
        engine_cache.engine_cache_counters(reset=True)
        cold = equilibrate_cell(handle, pot, **common)
        # Force the warm request through SQLite's decoder, not the process LRU.
        engine_cache._MEMORY_CACHE.clear()
        warm = equilibrate_cell(handle, pot, **common)

        assert stable(cache_off) == stable(cold) == stable(warm)
        assert scorer_fields(cache_off) == scorer_fields(cold) == scorer_fields(warm)
        assert engine_cache.engine_cache_counters(reset=True)[engine.value] == {
            "hits": 1,
            "misses": 1,
            "stores": 1,
            "store_error": 0,
            "read_error": 0,
            "conflicts": 0,
        }
    finally:
        close_battery_engine_handle(handle)


def test_alphamelts_no_convergence_cell_is_stored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MgO-SiO2 at 1700 K stores the parser's no_convergence refusal.

    Round 2 allowlisted ``no_convergence`` after the s3 probe: two requests,
    each identical across three fresh processes, status ``out_of_domain``.
    This cell is that code. Studio diagnostic e06b0d242ff84deda488373e2d73af05
    (Mac-Studio-256-2) returned engine_reason no_convergence, engine_status
    out_of_domain, no exit code and no signal, and stores 1. It is not a
    crash. A non-allowlisted out-of-domain death stays unstored:
    test_alphamelts_signal_death_is_stored_only_for_a_binary_fault
    (exit_signal) and test_alphamelts_timeout_is_not_stored_as_a_crash
    (process_failure).
    """

    from simulator import engine_cache
    from simulator.diagnostic_helpers.binary_pot_battery import (
        Po2Request,
        close_battery_engine_handle,
        equilibrate_cell,
        load_binary_pots,
    )
    from simulator.melt_backend.alphamelts import ALPHAMELTS_REASON_NO_CONVERGENCE
    from tests.battery.cache_test_support import open_available_engine

    handle = open_available_engine(
        Engine.ALPHAMELTS, nodeid="AlphaMELTS no_convergence cache store"
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
        assert cell.engine_reason == ALPHAMELTS_REASON_NO_CONVERGENCE
        assert cell.exit_code is None
        assert cell.exit_signal is None
        counts = engine_cache.engine_cache_counters(reset=True)["alphamelts"]
        assert counts["misses"] == 1
        assert counts["hits"] == 0
        assert counts["stores"] == 1
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


def test_stateless_internal_analytical_equilibrate_hits_enabled_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_cache
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _call_cached_backend_equilibrate,
    )

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    engine_cache.engine_cache_counters(reset=True)
    handle = SimpleNamespace(name="internal-analytical", engine_settings={}, backend=SimpleNamespace(_core=SimpleNamespace(_high_t_melt_activity="openimcc")))
    result = EquilibriumResult(status="ok", liquid_fraction=0.75)

    for _ in range(2):
        assert _call_cached_backend_equilibrate(handle, {"temperature_C": 1000.0}, lambda: result).liquid_fraction == result.liquid_fraction
    counts = engine_cache.engine_cache_counters(reset=True)["internal-analytical"]
    assert counts["hits"] == counts["misses"] == counts["stores"] == 1


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


def test_openimcc_cache_store_time_is_outside_the_hard_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sqlite3
    import threading
    import time

    from simulator import engine_cache
    from simulator.diagnostic_helpers.binary_pot_battery import (
        BinaryPot,
        Po2Request,
        close_battery_engine_handle,
        equilibrate_cell,
    )
    from tests.battery.cache_test_support import open_available_engine

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "slow-store.sqlite"))
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_MODE", raising=False)
    engine_cache._MEMORY_CACHE.clear()
    engine_cache.engine_cache_counters(reset=True)
    store_finished = threading.Event()

    def slow_failing_insert(*args: Any, **kwargs: Any) -> tuple[bool, bool]:
        try:
            time.sleep(1.1)
            raise sqlite3.OperationalError("simulated slow cache store failure")
        finally:
            store_finished.set()

    monkeypatch.setattr(
        engine_cache, "_insert_cache_row_if_absent", slow_failing_insert
    )
    handle = open_available_engine(
        Engine.OPENIMCC, nodeid="OpenIMCC slow store timeout boundary"
    )
    pot = BinaryPot(
        pot_id="openimcc-slow-store-timeout",
        kato_1993_table4_system=None,
        why="OpenIMCC cache store timeout boundary",
        composition_wt_pct={"MgO": 40.0, "SiO2": 60.0},
    )
    try:
        cell = equilibrate_cell(
            handle,
            pot,
            temperature_K=1700.0,
            po2=Po2Request(mode="engine_default", po2_bar=None),
            timeout_s=1.0,
            isolated=False,
        )
        assert store_finished.wait(timeout=5.0)
        assert cell.status == "ok", cell.engine_reason
        counters = engine_cache.engine_cache_counters(reset=True)["openimcc"]
        assert counters["stores"] + counters["store_error"] == 1
        assert counters["store_error"] == 1

        hit = equilibrate_cell(
            handle,
            pot,
            temperature_K=1700.0,
            po2=Po2Request(mode="engine_default", po2_bar=None),
            timeout_s=0.001,
            isolated=False,
        )
        assert hit.status == "ok", hit.engine_reason
        hit_counters = engine_cache.engine_cache_counters(reset=True)["openimcc"]
        assert hit_counters["hits"] == 1
        assert hit_counters["misses"] == 0
        assert hit_counters["stores"] == hit_counters["store_error"] == 0
    finally:
        close_battery_engine_handle(handle)


def test_openimcc_slow_solve_still_hits_the_hard_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading
    import time

    from simulator import engine_cache
    from simulator.diagnostic_helpers.binary_pot_battery import (
        BinaryPot,
        Po2Request,
        close_battery_engine_handle,
        equilibrate_cell,
    )
    from simulator.melt_backend import openimcc_bridge
    from tests.battery.cache_test_support import open_available_engine

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "slow-solve.sqlite"))
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_MODE", raising=False)
    engine_cache._MEMORY_CACHE.clear()
    engine_cache.engine_cache_counters(reset=True)
    solve_finished = threading.Event()
    original_solve = openimcc_bridge._evaluate_uncached

    def slow_solve(**kwargs: Any) -> Any:
        try:
            time.sleep(0.1)
            return original_solve(**kwargs)
        finally:
            solve_finished.set()

    monkeypatch.setattr(openimcc_bridge, "_evaluate_uncached", slow_solve)
    handle = open_available_engine(
        Engine.OPENIMCC, nodeid="OpenIMCC slow solve timeout boundary"
    )
    pot = BinaryPot(
        pot_id="openimcc-slow-solve-timeout",
        kato_1993_table4_system=None,
        why="OpenIMCC solve timeout boundary",
        composition_wt_pct={"MgO": 40.0, "SiO2": 60.0},
    )
    try:
        cell = equilibrate_cell(
            handle,
            pot,
            temperature_K=1700.0,
            po2=Po2Request(mode="engine_default", po2_bar=None),
            timeout_s=0.02,
            isolated=False,
        )
        assert cell.status == "refusal"
        assert cell.refusal_reason == "engine_timeout"
        assert solve_finished.wait(timeout=5.0)
        assert engine_cache.engine_cache_counters(reset=True)["openimcc"]["stores"] == 0
    finally:
        close_battery_engine_handle(handle)


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
        "effective_calc_mode",
        "model_calc_mode",
        "calc_mode_conflict",
        "subprocess_model_error",
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
        "fO2_buffer",
        "fO2_offset",
        "Fe3Fet_Liq",
    }
    # warm_worker is a scheduling option, so it is deliberately not projected.
    assert all(
        config["warm_worker"] is False
        for config in configs.values()
        if "warm_worker" in config
    )
    assert "warm_worker" not in configs["thermoengine"]


@pytest.mark.parametrize("case", _setting_cases(), ids=lambda case: case.label)
def test_runtime_identity_is_outside_normalized_engine_settings(case: _SettingCase) -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        normalize_engine_settings,
    )
    from simulator.engine_cache import engine_cache_settings

    configuration = dict(case.configuration)
    first = engine_cache_settings(
        case.engine.value, normalize_engine_settings(case.engine.value, configuration)
    )
    configuration["runtime_identity"] = {
        "commit": "b" * 64,
        "absolute_path": "/engine/runtime",
        "hostname": "runtime-host",
    }

    assert engine_cache_settings(
        case.engine.value, normalize_engine_settings(case.engine.value, configuration)
    ) == first


def test_magemin_bridge_selection_is_a_cache_setting() -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        normalize_engine_settings,
    )
    from simulator.engine_cache import engine_cache_key, engine_cache_settings

    first = engine_cache_settings(
        "magemin", normalize_engine_settings("magemin", {"bridge": "subprocess"})
    )
    second = engine_cache_settings(
        "magemin", normalize_engine_settings("magemin", {"bridge": "julia"})
    )

    assert first["bridge"] == "subprocess"
    assert second["bridge"] == "julia"
    assert engine_cache_key("magemin", 1, first) != engine_cache_key("magemin", 1, second)


def test_openimcc_cache_settings_use_selected_opener_pack() -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        normalize_engine_settings,
    )
    from simulator.engine_cache import engine_cache_key, engine_cache_settings

    first = engine_cache_settings(
        "openimcc", normalize_engine_settings("openimcc", {"pack": "default"})
    )
    second = engine_cache_settings(
        "openimcc", normalize_engine_settings("openimcc", {"pack": "published-ext"})
    )

    assert first == {"melt_pack": "default", "gas_pack": "default"}
    assert second == {"melt_pack": "published-ext", "gas_pack": "default"}
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
        "conflicts": 0,
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
    def fake_open_resolved_backend(
        _name: str,
        _settings: Any = None,
        **_kwargs: Any,
    ) -> Any:
        return SimpleNamespace(
            equilibrate=lambda **_kwargs: None,
            supports_intrinsic_fO2=False,
        )

    monkeypatch.setattr(battery, "_open_resolved_backend", fake_open_resolved_backend)
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
        "core": SimpleNamespace(_high_t_melt_activity="openimcc"),
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
    counts = engine_cache.engine_cache_counters(reset=True)["internal-analytical"]
    assert counts["uncacheable:SimpleNamespace"] == 2


def test_internal_analytical_residue_key_ignores_previous_call_state(
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
        return SimpleNamespace(status="ok", phase_assemblage_available=False, diagnostics={}, vapor_pressures_Pa={"SiO(g)": 1.0})

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
    assert second.vapor_pressures_Pa == {"SiO(g)": 1.0}
    assert calls == 1
    assert engine_cache.engine_cache_counters(reset=True)["internal-analytical"]["hits"] == 1


def test_internal_analytical_high_temperature_modes_cache_separately_and_exactly(
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
    openimcc_warm = evaluate("openimcc", cache=True)
    constant_gamma_off = evaluate("constant_gamma", cache=False)
    constant_gamma_on = evaluate("constant_gamma", cache=True)
    constant_gamma_warm = evaluate("constant_gamma", cache=True)

    from simulator.melt_backend.base import equilibrium_cache_value

    def consumed(result):
        return equilibrium_cache_value(vars(result), engine_id="internal-analytical")

    assert consumed(openimcc_on) == consumed(openimcc_off) == consumed(openimcc_warm)
    assert consumed(constant_gamma_on) == consumed(constant_gamma_off) == consumed(constant_gamma_warm)
    assert openimcc_off.vapor_pressures_Pa != constant_gamma_off.vapor_pressures_Pa
    assert calls == 4
    counts = engine_cache.engine_cache_counters(reset=True)["internal-analytical"]
    assert counts["hits"] == counts["misses"] == counts["stores"] == 2


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

    monkeypatch.setattr(battery, "_new_internal_analytical_core", lambda: SimpleNamespace(_high_t_melt_activity="openimcc"))
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
        starting_inventory={"Na2O": 1.0},
        missing_channels_reason="residue_pressure_channels_missing",
        caller="Hashimoto cache diagnostics",
        floor_inversion_diagnostics=floor_diagnostics,
    )

    first = pressure({"Na2O": 1.0}, -8.0)
    second = pressure({"Na2O": 1.0}, -8.0)
    pressure._on_accepted_evaluation(-8.0)

    assert first.pressures_Pa == second.pressures_Pa == {"Na": 5.0}
    assert floor_diagnostics["Na"]["detail"] == {
        "reason": "recorded floor inversion"
    }
    assert calls == 2

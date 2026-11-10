from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from simulator.battery.enums import Engine
from simulator.battery.score import _equilibrate_phase_assemblage
from simulator.diagnostic_helpers.binary_pot_battery import (
    close_battery_engine_handle,
    open_battery_engine,
)
from simulator.melt_backend.base import EquilibriumResult


@dataclass(frozen=True)
class _SettingCase:
    label: str
    engine: Engine
    backend_factory: Callable[[], Any]
    mutate: Callable[[Any], None]
    output: Callable[[Any], float]


class _Worker:
    def __init__(self, bootstrap_args: tuple[Any, ...]) -> None:
        self._bootstrap_args = bootstrap_args

    def close(self) -> None:
        pass


class _Pool:
    def __init__(self, worker: _Worker) -> None:
        self.workers = [worker]

    def close(self, *, cancel_pending: bool) -> None:
        del cancel_pending


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
        backend._bridge = "subprocess"
        backend._config = {"fO2_buffer": "qfm"}
        backend._subprocess_pool = _Pool(
            _Worker(("magemin", "ig", {"fO2_buffer": "qfm"}))
        )
        return backend

    def thermoengine_backend() -> Any:
        backend = ThermoEngineBackend()
        backend._mode = "thermoengine"
        backend._model = "MELTS-v1.0"
        backend._thermoengine_transport = SimpleNamespace(
            _model_name="MELTS-v1.0", close=lambda: None
        )
        return backend

    def vaporock_backend() -> Any:
        from simulator.melt_backend.vaporock import VapoRockBackend

        backend = VapoRockBackend()
        backend._temperature_units = "C"
        return backend

    def internal_analytical_backend() -> Any:
        from simulator.diagnostic_helpers.binary_pot_battery import (
            _InternalAnalyticalBatteryBackend,
        )

        backend = object.__new__(_InternalAnalyticalBatteryBackend)
        backend._core = object()
        backend._oxygen_balance_backend = SimpleNamespace(
            _pack_name="openimcc-v1.0.2", _model="oxygen-balance-v1"
        )
        backend._oxygen_balance_pack = "openimcc-v1.0.2"
        return backend

    def openimcc_backend() -> Any:
        from simulator.diagnostic_helpers.binary_pot_battery import (
            _OpenImccBatteryBackend,
        )

        backend = object.__new__(_OpenImccBatteryBackend)
        backend._pack_name = "openimcc-v1.0.2"
        backend._identity = {"pack": "openimcc-v1.0.2"}
        return backend

    def alpha_python_api_backend() -> Any:
        backend = alpha_backend()
        backend._mode = "python_api"
        backend._python_api_model = ("pMELTS", 1)
        backend._pet_worker = _Worker((1,))
        return backend

    return (
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
            "AlphaMELTS Python API model code",
            Engine.ALPHAMELTS,
            alpha_python_api_backend,
            lambda backend: setattr(backend._pet_worker, "_bootstrap_args", (2,)),
            lambda backend: {
                1: 0.25,
                2: 0.75,
            }[backend._pet_worker._bootstrap_args[0]],
        ),
        _SettingCase(
            "MAGEMin worker database",
            Engine.MAGEMIN,
            magemin_backend,
            lambda backend: setattr(
                backend._subprocess_pool.workers[0],
                "_bootstrap_args",
                ("magemin", "mp", {"fO2_buffer": "qfm"}),
            ),
            lambda backend: {"ig": 0.25, "mp": 0.75}[
                backend._effective_database()
            ],
        ),
        _SettingCase(
            "MAGEMin worker fO2 buffer",
            Engine.MAGEMIN,
            magemin_backend,
            lambda backend: setattr(
                backend._subprocess_pool.workers[0],
                "_bootstrap_args",
                ("magemin", "ig", {"fO2_buffer": "nno"}),
            ),
            lambda backend: {"qfm": 0.25, "nno": 0.75}[
                backend._subprocess_pool.workers[0]._bootstrap_args[2][
                    "fO2_buffer"
                ]
            ],
        ),
        _SettingCase(
            "ThermoEngine transport model",
            Engine.THERMOENGINE,
            thermoengine_backend,
            lambda backend: setattr(
                backend._thermoengine_transport, "_model_name", "rhyolite-MELTS"
            ),
            lambda backend: {
                "MELTS-v1.0": 0.25,
                "rhyolite-MELTS": 0.75,
            }[backend._thermoengine_transport._model_name],
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
            "VapoRock temperature units",
            Engine.VAPOROCK,
            vaporock_backend,
            lambda backend: setattr(backend, "_temperature_units", "K"),
            lambda backend: {"C": 0.25, "K": 0.75}[backend._temperature_units],
        ),
        _SettingCase(
            "Internal-analytical oxygen-balance pack",
            Engine.INTERNAL_ANALYTICAL,
            internal_analytical_backend,
            lambda backend: setattr(
                backend, "_oxygen_balance_pack", "openimcc-v1.0.3-ext-v1"
            ),
            lambda backend: {
                "openimcc-v1.0.2": 0.25,
                "openimcc-v1.0.3-ext-v1": 0.75,
            }[backend._oxygen_balance_pack],
        ),
        _SettingCase(
            "Internal-analytical nested OpenIMCC pack",
            Engine.INTERNAL_ANALYTICAL,
            internal_analytical_backend,
            lambda backend: setattr(
                backend._oxygen_balance_backend,
                "_pack_name",
                "openimcc-v1.0.3-ext-v1",
            ),
            lambda backend: {
                "openimcc-v1.0.2": 0.25,
                "openimcc-v1.0.3-ext-v1": 0.75,
            }[backend._oxygen_balance_backend._pack_name],
        ),
    )


def _simple_setting_leaves(value: Any, path: tuple[Any, ...] = ()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _simple_setting_leaves(item, (*path, key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _simple_setting_leaves(item, (*path, index))
    elif value is None or isinstance(value, (bool, int, float, str)):
        yield path, value


def _setting_at(root: Any, path: tuple[Any, ...]) -> Any:
    value = root
    for part in path:
        value = value[part] if isinstance(value, (dict, list, tuple)) else getattr(value, part)
    return value


def _replace_setting(root: Any, path: tuple[Any, ...], value: Any) -> None:
    parent = root
    for part in path[:-1]:
        parent = parent[part] if isinstance(parent, (dict, list, tuple)) else getattr(parent, part)
    final = path[-1]
    if isinstance(parent, dict):
        parent[final] = value
    elif isinstance(parent, list):
        parent[final] = value
    elif isinstance(parent, tuple):
        replacement = list(parent)
        replacement[final] = value
        _replace_setting(root, path[:-1], tuple(replacement))
    else:
        setattr(parent, final, value)


def _alternate_simple_value(value: Any) -> Any:
    if value is None:
        return "cache-setting-test"
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return value + 0.125
    if isinstance(value, tuple):
        return (*value, "cache-setting-test")
    if isinstance(value, list):
        return [*value, "cache-setting-test"]
    if isinstance(value, dict):
        return {**value, "cache-setting-test": "changed"}
    return value + "-cache-setting-test"


@pytest.mark.parametrize(
    "engine",
    tuple(dict.fromkeys(case.engine for case in _setting_cases())),
    ids=lambda engine: engine.value,
)
def test_scorer_cache_key_covers_every_enumerated_configuration_attribute(
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_cache
    import simulator.diagnostic_helpers.binary_pot_battery as battery

    cases = {case.engine: case for case in _setting_cases()}
    case = cases[engine]
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    backend = case.backend_factory()
    backend_settings = engine_cache.engine_cache_backend_settings(backend)
    paths = tuple(path for path, _value in _simple_setting_leaves(backend_settings))
    assert paths, f"{engine.value} has no discovered configuration attributes"

    for index, path in enumerate(paths):
        backend = case.backend_factory()
        monkeypatch.setattr(
            battery,
            "_open_resolved_backend",
            lambda _name, _engine_settings=None, backend=backend: backend,
        )
        handle = open_battery_engine(engine.value)
        before = _setting_at(backend, path)
        calls = 0

        def fake_equilibrate(**_kwargs: Any) -> EquilibriumResult:
            nonlocal calls
            calls += 1
            current = _setting_at(backend, path)
            return EquilibriumResult(
                status="ok", liquid_fraction=0.25 if current == before else 0.75
            )

        backend.equilibrate = fake_equilibrate

        def score() -> EquilibriumResult:
            return _equilibrate_phase_assemblage(
                engine,
                temperature_C=1000.0 + index,
                composition_kg={"SiO2": 1.0},
                fO2_log=-8.0,
                pressure_bar=1.0,
                handles={engine.value: handle},
            )

        try:
            cold = score()
            _replace_setting(backend, path, _alternate_simple_value(before))
            warm = score()
            assert cold.liquid_fraction == 0.25, (engine.value, path)
            assert warm.liquid_fraction == 0.75, (engine.value, path)
            assert calls == 2, (engine.value, path)
        finally:
            close_battery_engine_handle(handle)


def test_cache_settings_include_unanticipated_simple_configuration_attributes() -> None:
    from simulator.engine_cache import engine_cache_backend_settings

    backend = SimpleNamespace(_new_chemistry_knob="first")
    first = engine_cache_backend_settings(backend)
    backend._new_chemistry_knob = "second"
    second = engine_cache_backend_settings(backend)

    assert first["_new_chemistry_knob"] == "first"
    assert second["_new_chemistry_knob"] == "second"
    assert first != second


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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections import Counter

    from simulator import engine_cache
    import simulator.diagnostic_helpers.binary_pot_battery as battery

    engine_cache.runtime_identity.cache_clear()
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
    assert len(calls) == 2
    assert calls[0]["temperature_C"] == temperature_C
    assert calls[0]["composition_mol"] == composition_mol
    assert calls[1]["composition_mol"] == composition_mol
    assert engine_cache.engine_cache_counters(reset=True)["internal-analytical"] == {
        "hits": 1,
        "misses": 1,
        "stores": 1,
        "store_error": 0,
        "read_error": 0,
    }


def test_hashimoto_internal_residue_path_reads_diagnostics_from_cache(
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
            vapor_pressures_Pa={"Na(g)": 5.0},
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

    assert first == second == {"Na": 5.0}
    assert floor_diagnostics["Na"] == {"reason": "recorded floor inversion"}
    assert calls == 1

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

    def openimcc_backend() -> Any:
        from simulator.diagnostic_helpers.binary_pot_battery import (
            _OpenImccBatteryBackend,
        )

        backend = object.__new__(_OpenImccBatteryBackend)
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
            lambda backend: {"QFM": 1.0, "NNO": 2.0}[backend._redox_buffer],
        ),
        _SettingCase(
            "AlphaMELTS fO2 offset",
            Engine.ALPHAMELTS,
            alpha_backend,
            lambda backend: setattr(backend, "_fo2_offset", 0.5),
            lambda backend: {0.0: 1.0, 0.5: 2.0}[backend._fo2_offset],
        ),
        _SettingCase(
            "AlphaMELTS Python API model code",
            Engine.ALPHAMELTS,
            alpha_python_api_backend,
            lambda backend: setattr(backend._pet_worker, "_bootstrap_args", (2,)),
            lambda backend: float(backend._pet_worker._bootstrap_args[0]),
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
            lambda backend: {"ig": 1.0, "mp": 2.0}[
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
            lambda backend: {"qfm": 1.0, "nno": 2.0}[
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
                "MELTS-v1.0": 1.0,
                "rhyolite-MELTS": 2.0,
            }[backend._thermoengine_transport._model_name],
        ),
        _SettingCase(
            "OpenIMCC loaded cell pack",
            Engine.OPENIMCC,
            openimcc_backend,
            lambda backend: backend._identity.__setitem__(
                "pack", "openimcc-v1.0.3-ext-v1"
            ),
            lambda backend: {
                "openimcc-v1.0.2": 1.0,
                "openimcc-v1.0.3-ext-v1": 2.0,
            }[backend._effective_pack_name],
        ),
    )


@pytest.mark.parametrize("case", _setting_cases(), ids=lambda case: case.label)
def test_scorer_cache_key_tracks_effective_adapter_setting(
    case: _SettingCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_cache
    import simulator.diagnostic_helpers.binary_pot_battery as battery

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    engine_cache.engine_cache_counters(reset=True)
    backend = case.backend_factory()
    monkeypatch.setattr(
        battery,
        "_open_resolved_backend",
        lambda _name, _engine_settings=None: backend,
    )
    handle = open_battery_engine(case.engine.value)
    assert handle.available and handle.backend is backend
    assert handle.engine_settings == {}

    calls = 0

    def fake_equilibrate(**_kwargs: Any) -> EquilibriumResult:
        nonlocal calls
        calls += 1
        return EquilibriumResult(status="ok", liquid_fraction=case.output(backend))

    backend.equilibrate = fake_equilibrate

    def score() -> EquilibriumResult:
        return _equilibrate_phase_assemblage(
            case.engine,
            temperature_C=1000.0,
            composition_kg={"SiO2": 1.0},
            fO2_log=-8.0,
            pressure_bar=1.0,
            handles={case.engine.value: handle},
        )

    try:
        before = score()
        case.mutate(backend)
        after = score()
        assert before.liquid_fraction != after.liquid_fraction, case.label
        assert calls == 2, case.label

        # Handle settings are not authoritative. An operational-only change
        # must not split a chemistry-equivalent cache entry.
        handle.engine_settings = {"warm_pool_size": 99}
        again = score()
        assert again.liquid_fraction == after.liquid_fraction
        assert calls == 2, case.label
        assert engine_cache.engine_cache_counters(reset=True)[case.engine.value] == {
            "hits": 1,
            "misses": 2,
            "stores": 2,
        }
    finally:
        close_battery_engine_handle(handle)


def test_openimcc_gas_cache_key_tracks_loaded_pack_filename(
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

    first = evaluate_gas(*common, first_pack, gas_pack_selection="default")
    second = evaluate_gas(*common, second_pack, gas_pack_selection="default")

    assert first["CO(g)"] != second["CO(g)"]
    assert calls == 2
    assert engine_cache.engine_cache_counters(reset=True)["openimcc"] == {
        "hits": 0,
        "misses": 2,
        "stores": 2,
    }

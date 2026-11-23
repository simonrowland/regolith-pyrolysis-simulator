"""The phase-row scorer names AlphaMELTS' isothermal subprocess run."""

from __future__ import annotations

from types import SimpleNamespace

from simulator.battery.enums import Engine
from simulator.battery.score import _equilibrate_phase_assemblage
from simulator.diagnostic_helpers.binary_pot_battery import (
    EngineHandle,
    normalize_engine_settings,
)


def _handle(name: str, equilibrate) -> EngineHandle:
    return EngineHandle(
        name=name,
        backend=SimpleNamespace(equilibrate=equilibrate),
        available=True,
        unavailable_reason=None,
        takes_fo2=True,
        supports_intrinsic_fo2=False,
    )


def test_phase_row_alphamelts_passes_isothermal_run_mode(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def equilibrate(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine",
        lambda name: _handle("alphamelts", equilibrate),
    )
    _equilibrate_phase_assemblage(
        Engine.ALPHAMELTS,
        temperature_C=1030.0,
        composition_kg={"SiO2": 0.05},
        fO2_log=-4.31,
        pressure_bar=2010.0,
    )
    assert captured["subprocess_run_mode"] == "isothermal"
    assert captured["temperature_C"] == 1030.0
    assert captured["pressure_bar"] == 2010.0
    assert captured["fO2_log"] == -4.31


def test_phase_row_thermoengine_does_not_receive_subprocess_run_mode(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def equilibrate(temperature_C, composition_kg, fO2_log, pressure_bar):
        captured["temperature_C"] = temperature_C
        captured["composition_kg"] = composition_kg
        captured["fO2_log"] = fO2_log
        captured["pressure_bar"] = pressure_bar
        return SimpleNamespace()

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine",
        lambda name: _handle("thermoengine", equilibrate),
    )
    _equilibrate_phase_assemblage(
        Engine.THERMOENGINE,
        temperature_C=1030.0,
        composition_kg={"SiO2": 0.05},
        fO2_log=-4.31,
        pressure_bar=2010.0,
    )
    assert captured == {
        "temperature_C": 1030.0,
        "composition_kg": {"SiO2": 0.05},
        "fO2_log": -4.31,
        "pressure_bar": 2010.0,
    }


def test_phase_row_preserves_phase_maps_when_engine_cache_is_configured(
    monkeypatch, tmp_path
) -> None:
    calls = 0

    def equilibrate(**_kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            status="ok",
            phase_assemblage_available=True,
            liquid_fraction=1.0,
            phase_compositions={"liquid": {"SiO2": 100.0}},
        )

    handle = _handle("alphamelts", equilibrate)
    handle.engine_settings = normalize_engine_settings("alphamelts", None)
    monkeypatch.setenv(
        "REGOLITH_ENGINE_CACHE", str(tmp_path / "engine-cache.sqlite")
    )
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_MODE", raising=False)

    for _ in range(2):
        result = _equilibrate_phase_assemblage(
            Engine.ALPHAMELTS,
            temperature_C=1030.0,
            composition_kg={"SiO2": 0.05},
            fO2_log=-4.31,
            pressure_bar=2010.0,
            handles={"alphamelts": handle},
        )
        assert result.phase_compositions == {"liquid": {"SiO2": 100.0}}

    assert calls == 1

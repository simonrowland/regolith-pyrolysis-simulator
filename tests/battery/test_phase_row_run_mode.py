"""The phase-row scorer names AlphaMELTS' isothermal subprocess run."""

from __future__ import annotations

from types import SimpleNamespace

from simulator.battery.enums import Engine
from simulator.battery.score import _equilibrate_phase_assemblage
from simulator.diagnostic_helpers.binary_pot_battery import EngineHandle


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

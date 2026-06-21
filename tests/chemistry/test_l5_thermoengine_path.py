"""L5 ThermoEngine vapor-pressure source tagging."""

from __future__ import annotations

from typing import Any

import pytest

from simulator.chemistry.kernel import ChemistryIntent, IntentResult
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import EquilibriumResult
from simulator.runner import _vapor_pressure_source_report
from simulator.state import CampaignPhase
from tests.chemistry.conftest import _force_vaporock_unavailable_for_sim


class _MatchingThermoEngineBackend:
    """Fake L5 backend: ThermoEngine activities produce kernel-parity Pa."""

    def __init__(self) -> None:
        self.sim: PyrolysisSimulator | None = None

    def initialize(self, config: dict[str, Any]) -> bool:
        return True

    def is_available(self) -> bool:
        return True

    def equilibrate(
        self,
        temperature_C: float,
        composition_kg: dict[str, float] | None = None,
        fO2_log: float = -9.0,
        pressure_bar: float = 1e-6,
        **_: Any,
    ) -> EquilibriumResult:
        if self.sim is None:
            raise RuntimeError("test backend not attached to simulator")
        dispatch = self.sim._chem_kernel.dispatch(
            ChemistryIntent.VAPOR_PRESSURE,
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            control_inputs={"pO2_bar": self.sim._commanded_pO2_bar()},
        )
        kernel_vp = dict((dispatch.diagnostic or {}).get("vapor_pressures_Pa") or {})
        thermoengine_vp = {
            species: kernel_vp[species]
            for species in ("Na", "K")
            if species in kernel_vp
        }
        if not thermoengine_vp:
            raise RuntimeError("kernel fixture produced no Na/K vapor pressure")
        return EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            liquid_fraction=1.0,
            vapor_pressures_Pa=thermoengine_vp,
            vapor_pressures_source={
                species: "thermoengine"
                for species in thermoengine_vp
            },
            activity_coefficients={
                species: 1.0
                for species in thermoengine_vp
            },
            fO2_log=fO2_log,
            status="ok",
        )


def _setpoints_with_builtin_vapor_fallback(setpoints_data: dict) -> dict:
    setpoints = dict(setpoints_data)
    kernel_cfg = dict(setpoints.get("chemistry_kernel", {}) or {})
    kernel_cfg["allow_fallback_vapor"] = True
    kernel_cfg["allow_unmeasured_alpha_fallback"] = True
    setpoints["chemistry_kernel"] = kernel_cfg
    return setpoints


def test_l5_thermoengine_vapor_sources_survive_c2a_tick(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    backend = _MatchingThermoEngineBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        _setpoints_with_builtin_vapor_fallback(setpoints_data),
        feedstocks_data,
        vapor_pressure_data,
    )
    backend.sim = sim
    _force_vaporock_unavailable_for_sim(sim)
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    _force_vaporock_unavailable_for_sim(sim)

    sim.start_campaign(CampaignPhase.C2A)
    sim.melt.temperature_C = 1700.0
    sim.step()

    sources = dict(sim._last_vapor_pressures_source)
    assert sources
    assert sources["Na"] == "thermoengine"
    assert sources["K"] == "thermoengine"
    diagnostic_sources = dict(
        sim._last_vapor_pressure_diagnostic["vapor_pressures_source"]
    )
    assert sources == diagnostic_sources

    fallback_species = {
        species
        for species, source in sources.items()
        if source == "builtin_authoritative"
    }
    report = _vapor_pressure_source_report(sim)
    assert report["species"] == sources
    if fallback_species:
        assert report["summary"]["builtin_authoritative"]["count"] == len(
            fallback_species
        )
    assert report["summary"]["thermoengine"]["count"] == 2
    assert "builtin_authoritative" not in {
        sources["Na"],
        sources["K"],
    }


def test_l5_thermoengine_mismatch_keeps_kernel_value(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    backend = _MatchingThermoEngineBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        _setpoints_with_builtin_vapor_fallback(setpoints_data),
        feedstocks_data,
        vapor_pressure_data,
    )
    backend.sim = sim
    _force_vaporock_unavailable_for_sim(sim)
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    _force_vaporock_unavailable_for_sim(sim)
    sim.start_campaign(CampaignPhase.C2A)
    sim.melt.temperature_C = 1700.0

    kernel = sim._chem_kernel.dispatch(
        ChemistryIntent.VAPOR_PRESSURE,
        temperature_C=sim.melt.temperature_C,
        pressure_bar=sim.melt.p_total_mbar / 1000.0,
        control_inputs={"pO2_bar": sim._commanded_pO2_bar()},
        fO2_log=sim._compute_intrinsic_melt_fO2(),
    )
    kernel_vp = dict((kernel.diagnostic or {}).get("vapor_pressures_Pa") or {})
    bad_na = kernel_vp["Na"] * 2.0
    result = EquilibriumResult(
        temperature_C=sim.melt.temperature_C,
        pressure_bar=sim.melt.p_total_mbar / 1000.0,
        liquid_fraction=1.0,
        vapor_pressures_Pa={"Na": bad_na},
        vapor_pressures_source={"Na": "thermoengine"},
        status="ok",
    )

    sim._refresh_vapor_pressures_from_kernel(result)

    assert result.vapor_pressures_Pa["Na"] == pytest.approx(kernel_vp["Na"])
    assert result.vapor_pressures_source["Na"] == "builtin_authoritative"


def test_kernel_ok_empty_vapor_pressures_zero_backend_surface(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    monkeypatch,
):
    backend = _MatchingThermoEngineBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        _setpoints_with_builtin_vapor_fallback(setpoints_data),
        feedstocks_data,
        vapor_pressure_data,
    )
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    sim.melt.temperature_C = 1700.0
    result = EquilibriumResult(
        temperature_C=sim.melt.temperature_C,
        pressure_bar=sim.melt.p_total_mbar / 1000.0,
        liquid_fraction=1.0,
        vapor_pressures_Pa={"Na": 12.0},
        vapor_pressures_source={"Na": "thermoengine"},
        status="ok",
    )

    def _dispatch_empty_ok(*_, **__):
        return IntentResult(
            intent=ChemistryIntent.VAPOR_PRESSURE,
            status="ok",
            diagnostic={"vapor_pressures_Pa": {}},
        )

    monkeypatch.setattr(sim, "_dispatch_only", _dispatch_empty_ok)

    sim._refresh_vapor_pressures_from_kernel(result)

    assert result.vapor_pressures_Pa == {}
    assert result.vapor_pressures_source == {}
    assert sim._last_vapor_pressures_source == {}
    assert (
        sim._last_vapor_pressure_diagnostic["vapor_pressure_zero_reason"]
        == "kernel_ok_empty"
    )

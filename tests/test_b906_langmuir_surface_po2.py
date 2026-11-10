"""b-906: free Langmuir evaporation closes surface pO2 below the certified band.

Green (16c8dabfa) ideal-alpha Ca flux for pure CaO at 2200 C with melt
fO2 at the 1e-30 bar floor is 52330509702177.99 kg m^-2 h^-1. The provider
pressure stays that mass-action value. The flux map must not.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.chemistry.langmuir_knudsen import hertz_knudsen_k_kg_s_m2_pa
from simulator.environment import DEFAULT_VACUUM_FLOOR_BAR
from simulator.evaporation import EvaporationMixin
from simulator.langmuir_surface_oxygen import (
    LANGMUIR_SURFACE_PO2_REASON,
    apply_langmuir_surface_oxygen,
)
from simulator.vapour_rail.catalog import compile_vapour_rail_catalog

_T_C = 2200.0
_T_K = _T_C + 273.15
_DATA = Path(__file__).resolve().parents[1] / "data" / "vapor_pressures.yaml"
_CA_MOLAR_MASS_KG = 40.078 / 1000.0


def _catalog():
    payload = yaml.safe_load(_DATA.read_text())
    compiled = compile_vapour_rail_catalog(payload)
    return compiled, compiled.legacy_view()


def _cao_answer(intrinsic_fO2_log: float):
    payload = yaml.safe_load(_DATA.read_text())
    provider = BuiltinVaporPressureProvider(payload)
    return provider.dispatch(
        IntentRequest(
            intent=ChemistryIntent.VAPOR_PRESSURE,
            account_view=ProviderAccountView(
                accounts={"process.cleaned_melt": {"CaO": 1.0}},
                species_formula_registry={},
            ),
            temperature_C=_T_C,
            pressure_bar=0.01,
            control_inputs={
                "pO2_bar": DEFAULT_VACUUM_FLOOR_BAR,
                "intrinsic_fO2_log": intrinsic_fO2_log,
                "vacuum_floor_bar": DEFAULT_VACUUM_FLOOR_BAR,
            },
        )
    )


def _ca_flux_kg_m2_h(pressure_Pa: float) -> float:
    return (
        pressure_Pa
        * hertz_knudsen_k_kg_s_m2_pa(_T_K, _CA_MOLAR_MASS_KG)
        * 3600.0
    )


def test_floor_pure_cao_langmuir_flux_is_physical() -> None:
    compiled, legacy = _catalog()
    answer = _cao_answer(-40.0)
    diagnostic = answer.diagnostic
    pressures = diagnostic["vapor_pressures_Pa"]
    raw_ca = pressures["Ca"]
    raw_flux = _ca_flux_kg_m2_h(raw_ca)
    # Fail-before at green: this raw flux is 5.233e13 kg m^-2 h^-1.
    assert raw_flux > 1.0e12
    assert pressures["Ca2"] > 1.0e15

    result = apply_langmuir_surface_oxygen(
        pressures,
        temperature_K=_T_K,
        provenance_by_species=diagnostic[
            "vapor_pressure_numerator_provenance"
        ],
        source_reaction_fO2_bar=diagnostic["source_reaction_fO2_bar"],
        transport_pO2_bar=diagnostic["pO2_bar"],
        legacy_catalog=legacy,
        compiled_catalog=compiled,
    )

    assert pressures["Ca"] == raw_ca
    assert result.applied is True
    assert result.refused_species == {}
    assert result.surface_pO2_bar is not None
    assert result.surface_pO2_bar > 1.0e-30
    assert result.relative_residual is not None
    assert result.relative_residual < 1.0e-4
    assert result.warning is not None
    assert LANGMUIR_SURFACE_PO2_REASON in result.warning
    # Ca2 provenance records transport 1e-9 bar. The law ran at 1e-30 bar.
    # Rescaling from the recorded 1e-9 bar cannot put Ca2 under 1e3 Pa
    # anywhere in the (1e-30, 1] bar bracket.
    assert result.pressures_Pa["Ca2"] < 1.0e3
    flux = _ca_flux_kg_m2_h(result.pressures_Pa["Ca"])
    assert 1.0e-6 < flux < 100.0, (
        f"surface_pO2_bar={result.surface_pO2_bar} "
        f"Ca_Pa={result.pressures_Pa['Ca']} flux={flux}"
    )


def test_in_band_pure_cao_flux_map_is_unchanged() -> None:
    compiled, legacy = _catalog()
    answer = _cao_answer(-9.0)
    diagnostic = answer.diagnostic
    pressures = diagnostic["vapor_pressures_Pa"]
    assert diagnostic["source_reaction_fO2_bar"] == pytest.approx(1.0e-9)
    assert pressures["Ca"] == pytest.approx(825.3377, rel=1.0e-4)

    result = apply_langmuir_surface_oxygen(
        pressures,
        temperature_K=_T_K,
        provenance_by_species=diagnostic[
            "vapor_pressure_numerator_provenance"
        ],
        source_reaction_fO2_bar=diagnostic["source_reaction_fO2_bar"],
        transport_pO2_bar=diagnostic["pO2_bar"],
        legacy_catalog=legacy,
        compiled_catalog=compiled,
    )

    assert result.applied is False
    assert result.warning is None
    assert result.pressures_Pa is pressures


def test_evaporation_hook_adjusts_floor_flux_map_only() -> None:
    compiled, legacy = _catalog()
    answer = _cao_answer(-40.0)
    diagnostic = dict(answer.diagnostic)
    pressures = dict(diagnostic["vapor_pressures_Pa"])
    equilibrium = SimpleNamespace(
        liquid_fraction=1.0,
        status="ok",
        diagnostics={},
        warnings=[],
        vapor_pressures_Pa=dict(pressures),
    )
    sim = SimpleNamespace(
        melt=SimpleNamespace(temperature_C=_T_C),
        setpoints={},
        vapor_pressures=legacy,
        vapour_rail_catalog=compiled,
        _build_partial_melt_offgassing_diagnostic=lambda *args, **kwargs: {},
        _resolve_evaporation_batch_flux_state=lambda equilibrium, *, temperature_K: (
            pressures,
            {},
            None,
            {},
            diagnostic,
            None,
        ),
    )

    with pytest.raises(AttributeError):
        EvaporationMixin._calculate_evaporation(sim, equilibrium)

    notice = sim._last_langmuir_surface_oxygen
    assert notice["applied"] is True
    assert LANGMUIR_SURFACE_PO2_REASON in notice["warning"]
    assert any(LANGMUIR_SURFACE_PO2_REASON in item for item in equilibrium.warnings)
    assert equilibrium.vapor_pressures_Pa["Ca"] == pressures["Ca"]
    assert equilibrium.vapor_pressures_Pa["Ca"] > 1.0e12

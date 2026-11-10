from __future__ import annotations

import math

import pytest

import simulator.chemistry.oxygen_interface as oxygen_interface
from simulator.accounting.formulas import parse_formula
from simulator.physical_constants import PA_PER_BAR


M_FE_KG_PER_MOL = parse_formula("Fe").molar_mass_kg_per_mol()
M_O2_KG_PER_MOL = parse_formula("O2").molar_mass_kg_per_mol()
TEMPERATURE_K = 1800.0
AREA_M2 = 0.3
K_BAR = 1.0e-6


def _closed_form_pressure_bar(k_bar: float = K_BAR) -> float:
    return (
        k_bar * math.sqrt(M_O2_KG_PER_MOL / M_FE_KG_PER_MOL) / 2.0
    ) ** (2.0 / 3.0)


def _solve(
    *,
    capacity: float = 0.0,
    reservoir_log10_fO2: float = -2.0,
    overhead_pO2_bar: float = 0.0,
    k_bar: float = K_BAR,
):
    channel = oxygen_interface.InterfaceOxygenChannel(
        species="Fe",
        molar_mass_kg_per_mol=M_FE_KG_PER_MOL,
        oxygen_atoms=0.0,
        parent_oxygen_demand=1.0,
        pO2_exponent=-0.5,
        alpha=1.0,
        r_gas_m_s=0.0,
    )

    def pressure_model(log10_pO2_bar: float) -> dict[str, float]:
        pO2_bar = 10.0**log10_pO2_bar
        p_fe_bar = k_bar / math.sqrt(pO2_bar)
        return {"Fe": p_fe_bar * PA_PER_BAR}

    return oxygen_interface.solve_interface_oxygen(
        pressure_model,
        (channel,),
        reservoir_log10_fO2_bar=reservoir_log10_fO2,
        melt_capacity_mol_per_ln_fO2=capacity,
        exchange_time_s=1.0,
        overhead_pO2_bar=overhead_pO2_bar,
        area_m2=AREA_M2,
        temperature_K=TEMPERATURE_K,
        oxygen_alpha=1.0,
        oxygen_r_gas_m_s=0.0,
    )


def test_zero_capacity_vacuum_matches_closed_form_langmuir_root() -> None:
    solution = _solve()

    assert solution.surface_pO2_bar == pytest.approx(_closed_form_pressure_bar(), rel=1e-10)
    assert solution.capacity_label == "none:no_melt_redox_capacity"
    assert solution.product_fluxes_mol_m2_s["Fe"] > 0.0


def test_large_melt_capacity_pins_surface_to_reservoir() -> None:
    reservoir_log10_fO2 = -2.0

    solution = _solve(
        capacity=1.0e8,
        reservoir_log10_fO2=reservoir_log10_fO2,
    )

    assert solution.surface_log10_pO2_bar == pytest.approx(
        reservoir_log10_fO2, abs=1e-6
    )


def test_overhead_above_vacuum_root_is_a_labeled_floor_with_product_flux() -> None:
    overhead_pO2_bar = 10.0 * _closed_form_pressure_bar()

    solution = _solve(overhead_pO2_bar=overhead_pO2_bar)

    assert solution.surface_pO2_bar == pytest.approx(overhead_pO2_bar, rel=1e-12)
    assert solution.label == "overhead_oxygen_floor"
    assert solution.product_fluxes_mol_m2_s["Fe"] > 0.0


def test_both_positive_bracket_endpoints_return_lower_edge_flux() -> None:
    solution = _solve(k_bar=1.0e-45)

    assert solution.surface_pO2_bar == 1.0e-30
    assert solution.label == "bracket_boundary=lower"
    assert solution.product_fluxes_mol_m2_s["Fe"] > 0.0


def test_brent_failure_is_typed_and_does_not_fall_back_to_midpoint(monkeypatch) -> None:
    def fail_brentq(*args, **kwargs):
        raise RuntimeError("forced Brent failure")

    monkeypatch.setattr(oxygen_interface, "brentq", fail_brentq)

    with pytest.raises(oxygen_interface.InterfaceOxygenSolveError) as caught:
        _solve()

    assert caught.value.reason == "brent_failure"

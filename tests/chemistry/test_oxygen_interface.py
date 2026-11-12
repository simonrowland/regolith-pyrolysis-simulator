from __future__ import annotations

import math

import pytest

import simulator.chemistry.oxygen_interface as oxygen_interface
from simulator.accounting.formulas import parse_formula
from simulator.physical_constants import GAS_CONSTANT, PA_PER_BAR


M_FE_KG_PER_MOL = parse_formula("Fe").molar_mass_kg_per_mol()
M_O2_KG_PER_MOL = parse_formula("O2").molar_mass_kg_per_mol()
TEMPERATURE_K = 1800.0
AREA_M2 = 0.3
K_BAR = 1.0e-6


def _closed_form_pressure_bar(k_bar: float = K_BAR) -> float:
    return (
        k_bar * math.sqrt(M_O2_KG_PER_MOL / M_FE_KG_PER_MOL) / 2.0
    ) ** (2.0 / 3.0)


def _expected_fe_flux(
    solution, *, k_bar: float = K_BAR, alpha: float = 1.0
) -> float:
    p_fe_bar = k_bar / math.sqrt(solution.surface_pO2_bar)
    p_fe_pa = p_fe_bar * PA_PER_BAR
    return alpha * p_fe_pa / math.sqrt(
        2.0 * math.pi * M_FE_KG_PER_MOL * GAS_CONSTANT * TEMPERATURE_K
    )


def _independent_sources(
    log10_pO2_bar: float,
    *,
    capacity: float,
    reservoir_log10_fO2: float,
    overhead_pO2_bar: float,
    k_bar: float = K_BAR,
) -> tuple[float, float, float, float, float]:
    # Independent FeO -> Fe + 1/2 O2 oracle: HK gives J_Fe, stoichiometry gives
    # S_evap=A*J_Fe/2, and the signed O2 film uses the Pa pressure difference.
    pO2_bar = 10.0**log10_pO2_bar
    p_fe_pa = k_bar / math.sqrt(pO2_bar) * PA_PER_BAR
    fe_flux = p_fe_pa / math.sqrt(
        2.0 * math.pi * M_FE_KG_PER_MOL * GAS_CONSTANT * TEMPERATURE_K
    )
    oxygen_resistance = 1.0 / math.sqrt(
        M_O2_KG_PER_MOL / (2.0 * math.pi * GAS_CONSTANT * TEMPERATURE_K)
    )
    melt_source = capacity * (reservoir_log10_fO2 - log10_pO2_bar) * math.log(10.0)
    evaporative_source = AREA_M2 * fe_flux / 2.0
    gas_source = (
        AREA_M2
        * (pO2_bar - overhead_pO2_bar)
        * PA_PER_BAR
        / (oxygen_resistance * M_O2_KG_PER_MOL)
    )
    residual = melt_source + evaporative_source - gas_source
    return fe_flux, melt_source, evaporative_source, gas_source, residual


def _independent_bisection(
    *,
    capacity: float,
    reservoir_log10_fO2: float,
    overhead_pO2_bar: float,
    k_bar: float = K_BAR,
) -> float:
    lower, upper = -30.0, 0.0
    lower_residual = _independent_sources(
        lower,
        capacity=capacity,
        reservoir_log10_fO2=reservoir_log10_fO2,
        overhead_pO2_bar=overhead_pO2_bar,
        k_bar=k_bar,
    )[-1]
    upper_residual = _independent_sources(
        upper,
        capacity=capacity,
        reservoir_log10_fO2=reservoir_log10_fO2,
        overhead_pO2_bar=overhead_pO2_bar,
        k_bar=k_bar,
    )[-1]
    assert lower_residual > 0.0 > upper_residual
    for _ in range(200):
        midpoint = (lower + upper) / 2.0
        midpoint_residual = _independent_sources(
            midpoint,
            capacity=capacity,
            reservoir_log10_fO2=reservoir_log10_fO2,
            overhead_pO2_bar=overhead_pO2_bar,
            k_bar=k_bar,
        )[-1]
        if midpoint_residual > 0.0:
            lower = midpoint
        else:
            upper = midpoint
    return (lower + upper) / 2.0


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
        oxygen_alpha=oxygen_interface.OXYGEN_GAS_ALPHA,
        oxygen_r_gas_m_s=0.0,
    )


def test_zero_capacity_vacuum_matches_closed_form_langmuir_root() -> None:
    solution = _solve()

    assert solution.surface_pO2_bar == pytest.approx(_closed_form_pressure_bar(), rel=1e-10)
    assert solution.capacity_label == "none:no_melt_redox_capacity"
    assert solution.product_fluxes_mol_m2_s["Fe"] == pytest.approx(
        _expected_fe_flux(solution), rel=1e-10
    )


def test_large_melt_capacity_pins_surface_to_reservoir() -> None:
    reservoir_log10_fO2 = -2.0

    solution = _solve(
        capacity=1.0e8,
        reservoir_log10_fO2=reservoir_log10_fO2,
    )

    assert solution.surface_log10_pO2_bar == pytest.approx(
        reservoir_log10_fO2, abs=1e-6
    )


def test_pinned_reservoir_below_overhead_keeps_signed_inward_transfer() -> None:
    solution = _solve(
        capacity=1.0e8,
        reservoir_log10_fO2=-8.0,
        overhead_pO2_bar=1.0e-6,
    )

    assert solution.surface_log10_pO2_bar == -8.0
    assert solution.label is None
    assert solution.melt_source_mol_s == pytest.approx(
        -2.070472267434015, rel=1e-10
    )
    assert solution.residual_mol_s == 0.0


def test_buffered_pin_preserves_closing_melt_source_at_capacity_boundary() -> None:
    brent_solution = _solve(capacity=1.0e6)
    pin_solution = _solve(capacity=1.0e6 + 1.0)

    assert brent_solution.melt_source_mol_s == pytest.approx(5.467009, rel=1e-4)
    assert pin_solution.melt_source_mol_s == pytest.approx(5.467009, rel=1e-4)
    assert pin_solution.melt_source_mol_s == pytest.approx(
        pin_solution.gas_source_mol_s - pin_solution.evaporative_source_mol_s,
        rel=1e-12,
    )
    assert pin_solution.residual_mol_s == pytest.approx(0.0, abs=1e-12)


def test_zero_capacity_overhead_solves_signed_balance_above_overhead() -> None:
    overhead_pO2_bar = 10.0 * _closed_form_pressure_bar()
    root_log10 = _independent_bisection(
        capacity=0.0,
        reservoir_log10_fO2=-2.0,
        overhead_pO2_bar=overhead_pO2_bar,
    )
    root_fe_flux = _independent_sources(
        root_log10,
        capacity=0.0,
        reservoir_log10_fO2=-2.0,
        overhead_pO2_bar=overhead_pO2_bar,
    )[0]

    solution = _solve(overhead_pO2_bar=overhead_pO2_bar)

    assert 10.0**root_log10 == pytest.approx(5.395244803492843e-4, rel=1e-10)
    assert solution.surface_pO2_bar == pytest.approx(10.0**root_log10, rel=1e-10)
    assert solution.surface_pO2_bar > overhead_pO2_bar
    assert solution.label is None
    assert root_fe_flux == pytest.approx(5.940992382704242e-2, rel=1e-8)
    assert solution.product_fluxes_mol_m2_s["Fe"] == pytest.approx(
        root_fe_flux, rel=1e-10
    )
    assert solution.gas_source_mol_s == pytest.approx(
        solution.evaporative_source_mol_s, rel=1e-12
    )
    assert abs(solution.residual_mol_s) <= 1.0e-12 * abs(
        solution.evaporative_source_mol_s
    )


def test_finite_capacity_root_can_have_signed_inward_overhead_flux() -> None:
    reservoir_log10_fO2 = -20.0
    overhead_pO2_bar = 1.0e-3
    root_log10 = _independent_bisection(
        capacity=3.0,
        reservoir_log10_fO2=reservoir_log10_fO2,
        overhead_pO2_bar=overhead_pO2_bar,
    )
    _, expected_melt, _, expected_gas, _ = _independent_sources(
        root_log10,
        capacity=3.0,
        reservoir_log10_fO2=reservoir_log10_fO2,
        overhead_pO2_bar=overhead_pO2_bar,
    )

    solution = _solve(
        capacity=3.0,
        reservoir_log10_fO2=reservoir_log10_fO2,
        overhead_pO2_bar=overhead_pO2_bar,
    )

    assert root_log10 == pytest.approx(-10.952212857667842, abs=1e-10)
    assert solution.surface_log10_pO2_bar == pytest.approx(root_log10, abs=1e-10)
    assert solution.label is None
    assert expected_melt == pytest.approx(-62.49989939555169, rel=1e-10)
    assert solution.melt_source_mol_s == pytest.approx(expected_melt, rel=1e-10)
    assert expected_gas == pytest.approx(-0.546910867980536, rel=1e-10)
    assert solution.gas_source_mol_s == pytest.approx(expected_gas, rel=1e-10)
    assert solution.gas_source_mol_s < 0.0
    assert solution.residual_mol_s == pytest.approx(0.0, abs=1e-10)


def test_both_positive_bracket_endpoints_return_lower_edge_flux() -> None:
    solution = _solve(k_bar=1.0e-45)

    assert solution.surface_pO2_bar == 1.0e-30
    assert solution.label == "bracket_boundary=lower"
    assert solution.product_fluxes_mol_m2_s["Fe"] == pytest.approx(
        _expected_fe_flux(solution, k_bar=1.0e-45), rel=1e-10
    )


@pytest.mark.parametrize("failure", [RuntimeError, ValueError])
def test_brent_failure_is_typed_and_does_not_fall_back_to_midpoint(
    monkeypatch, failure
) -> None:
    def fail_brentq(*args, **kwargs):
        raise failure("forced Brent failure")

    monkeypatch.setattr(oxygen_interface, "brentq", fail_brentq)

    with pytest.raises(oxygen_interface.InterfaceOxygenSolveError) as caught:
        _solve()

    assert caught.value.reason == "brent_failure"


def test_stage_c_film_notice_ratio_is_ledger_over_effusion() -> None:
    # Independent of the production divider: the series law at one
    # (P_eq - P_bulk) gives J_ledger / J_effusion = R_i / (R_i + R_g + R_m).
    r_interface = 1.0
    r_gas = 307.0
    r_melt = 0.0
    effusion_mol_h = 4.0
    ledger_mol_h = effusion_mol_h * r_interface / (r_interface + r_gas + r_melt)

    notice = oxygen_interface.stage_c_film_mismatch_notice(
        ledger_mol_h={"SiO": ledger_mol_h, "Na": 0.0},
        interface_effusion_mol_h={"SiO": effusion_mol_h, "Fe": 2.0},
    )

    assert notice is not None
    assert notice["kind"] == oxygen_interface.STAGE_C_FILM_MISMATCH_KIND
    assert notice["ratios"]["SiO"] == pytest.approx(
        1.0 / (1.0 + (r_gas + r_melt) / r_interface)
    )
    assert notice["ratios"]["Fe"] == pytest.approx(0.0)
    assert notice["ledger_mol_h"]["Fe"] == 0.0
    assert "Na" not in notice["ratios"]


def test_stage_c_film_notice_keeps_a_debit_with_no_effusion() -> None:
    notice = oxygen_interface.stage_c_film_mismatch_notice(
        ledger_mol_h={"Si": 1.5},
        interface_effusion_mol_h={"Si": 0.0},
    )

    assert notice is not None
    assert notice["ratios"]["Si"] is None
    assert notice["ledger_mol_h"]["Si"] == 1.5
    assert notice["interface_effusion_mol_h"]["Si"] == 0.0


def test_stage_c_film_notice_is_absent_when_nothing_moves() -> None:
    assert (
        oxygen_interface.stage_c_film_mismatch_notice(
            ledger_mol_h={"SiO": 0.0},
            interface_effusion_mol_h={},
        )
        is None
    )


def test_stage_c_film_notice_refuses_a_non_finite_rate() -> None:
    with pytest.raises(ValueError, match="SiO"):
        oxygen_interface.stage_c_film_mismatch_notice(
            ledger_mol_h={"SiO": math.nan},
            interface_effusion_mol_h={"SiO": 1.0},
        )

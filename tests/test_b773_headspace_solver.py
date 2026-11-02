import math

import pytest

from simulator.overhead import (
    OverheadGasModel,
    _headspace_partials_from_committed_flux_Pa,
    _headspace_partials_at_pressure_Pa,
    solve_quasi_steady_headspace_partials_Pa,
)
from simulator.state import EvaporationFlux


def _flux(rate_kg_s):
    result = EvaporationFlux(
        species_kg_hr={"Na": max(0.0, float(rate_kg_s)) * 3600.0}
    )
    result.update_totals()
    return result


def _single_species_solver(
    *,
    beta,
    equilibrium,
    pipe_K=1.0,
    molar_mass=0.0229898,
    downstream=0.0,
    fixed_pressure=None,
    molar_flow_override=None,
    limit=80,
):
    def evaluate(partials, _total_pressure):
        rate = beta * max(0.0, equilibrium - partials["Na"])
        return _flux(rate)

    def molar_flow(pressure):
        return pipe_K * max(0.0, pressure**2 - downstream**2)

    if molar_flow_override is not None:
        molar_flow = molar_flow_override

    fixed_flow = (
        molar_flow(fixed_pressure) if fixed_pressure is not None else None
    )

    return solve_quasi_steady_headspace_partials_Pa(
        equilibrium_Pa={"Na": equilibrium},
        fixed_partials_Pa={},
        beta_kg_s_Pa_by_species={"Na": beta},
        molar_mass_kg_mol_by_species={"Na": molar_mass},
        fixed_molar_flow_mol_s_by_species={},
        molar_flow_mol_s_at_pressure=molar_flow,
        pressure_bracket_Pa=(downstream, max(equilibrium, downstream) + 1.0),
        evaluate_flux=evaluate,
        fixed_pressure_Pa=fixed_pressure,
        fixed_pressure_molar_flow_mol_s=fixed_flow,
        max_iterations=limit,
    )


def test_carrier_free_pipe_matches_hand_solved_na_partial_and_closes_residual():
    # The Na root solves M*K*p² = beta*(P_eq-p). Coefficients and the
    # 1.20745 Pa root are the independently worked lunar/Mars hour-1 pipe
    # check, not an expected value copied from this solver's output.
    beta = 5.65443e-7  # kg/(s Pa)
    equilibrium = 1.47190  # Pa
    molar_mass = 0.02298976928  # kg/mol — existing Na molar mass
    coefficient = 4.46123e-6  # mol/(s Pa²), carrier-free pipe at 1065 °C
    model = OverheadGasModel()

    solved = _single_species_solver(
        beta=beta,
        equilibrium=equilibrium,
        pipe_K=coefficient,
        molar_mass=molar_mass,
        molar_flow_override=lambda pressure: model._pipe_molar_flow_mol_s(
            pressure,
            1065.0,
        ),
    )
    assert solved.converged
    assert solved.partials_Pa["Na"] == pytest.approx(
        1.20745, abs=5.0e-6
    )
    assert solved.residual_relative <= 1.0e-9
    # Independent dimensional check for the selected root: both sides are kg/s.
    p = solved.partials_Pa["Na"]
    assert beta * (equilibrium - p) == pytest.approx(
        molar_mass * coefficient * p * p, rel=2.0e-5
    )


def test_zero_pipe_removal_reaches_equilibrium_with_zero_net_source():
    equilibrium = 2.0
    beta = 1.0e-6
    solved = _single_species_solver(
        beta=beta,
        equilibrium=equilibrium,
        pipe_K=0.0,
    )
    assert solved.converged
    assert solved.partials_Pa["Na"] == pytest.approx(equilibrium)
    assert solved.flux.species_kg_hr["Na"] == pytest.approx(0.0)


def test_committed_source_partial_clamps_and_flags_negative_pressure():
    partials, clamped = _headspace_partials_from_committed_flux_Pa(
        {"Na": 7200.0},
        equilibrium_Pa={"Na": 1.0},
        beta_kg_s_Pa_by_species={"Na": 1.0},
    )

    assert partials == {"Na": 0.0}
    assert clamped == ("Na",)


def test_fixed_pressure_residual_uses_nonlinear_committed_source():
    # Direct counterexample from the review: q(p)=1-exp(-(2-p)) kg/s,
    # while removal is p kg/s. The root is p=0.72153546 Pa; a writer that
    # repeats the closed form reports the initial p=1 Pa as balanced.
    def evaluate(partials, _total_pressure):
        partial = float(partials["Na"])
        return _flux(1.0 - math.exp(-(2.0 - partial)))

    solved = solve_quasi_steady_headspace_partials_Pa(
        equilibrium_Pa={"Na": 2.0},
        fixed_partials_Pa={},
        beta_kg_s_Pa_by_species={"Na": 1.0},
        molar_mass_kg_mol_by_species={"Na": 1.0},
        fixed_molar_flow_mol_s_by_species={},
        molar_flow_mol_s_at_pressure=lambda _pressure: 1.0,
        pressure_bracket_Pa=(0.0, 3.0),
        evaluate_flux=evaluate,
        fixed_pressure_Pa=1.0,
        fixed_pressure_molar_flow_mol_s=1.0,
    )

    assert solved.converged
    assert solved.partials_Pa["Na"] == pytest.approx(
        0.72153546, abs=1.0e-8
    )
    assert solved.flux.species_kg_hr["Na"] / 3600.0 == pytest.approx(
        solved.partials_Pa["Na"], rel=1.0e-9
    )


def test_near_equilibrium_floor_uses_flattened_committed_source_slope():
    equilibrium = 1.0
    beta = 1.0e-6
    pressure = 1.0
    molar_flow = 1.0e-14
    molar_mass = 1.0
    # With G=M*n/P=1e-14 kg/(s Pa), the uncapped balance gives
    # p=beta*P_eq/(beta+G)=0.9999999900000001 Pa and
    # J=beta*(P_eq-p)=9.99999993922529e-15 kg/s. The inventory cap
    # 9.999999939062431e-15 kg/s makes the committed source flat there;
    # its 3.906243e-23 kg/s residual is 3.9e-9 relative. The old
    # beta-based half-ulp allowance is 5.55e-23 kg/s and erases it. The
    # committed-source secant is about G, so that residual remains open.
    committed_cap_kg_s = 9.999999939062431e-15

    def evaluate(partials, _total_pressure):
        source_kg_s = beta * max(
            0.0,
            equilibrium - float(partials["Na"]),
        )
        return _flux(source_kg_s)

    def flatten_inventory_source(raw_flux):
        # Once the frozen inventory is rate-limited, its committed source is
        # flat even though the unconstrained HK slope remains beta.
        raw_kg_s = float(raw_flux.species_kg_hr["Na"]) / 3600.0
        return _flux(min(raw_kg_s, committed_cap_kg_s))

    solved = solve_quasi_steady_headspace_partials_Pa(
        equilibrium_Pa={"Na": equilibrium},
        fixed_partials_Pa={},
        beta_kg_s_Pa_by_species={"Na": beta},
        molar_mass_kg_mol_by_species={"Na": molar_mass},
        fixed_molar_flow_mol_s_by_species={},
        molar_flow_mol_s_at_pressure=lambda _pressure: molar_flow,
        pressure_bracket_Pa=(0.0, 1.0),
        evaluate_flux=evaluate,
        commit_flux=flatten_inventory_source,
        fixed_pressure_Pa=pressure,
        fixed_pressure_molar_flow_mol_s=molar_flow,
        max_iterations=1,
    )

    committed_kg_s = min(
        float(solved.flux.species_kg_hr["Na"]) / 3600.0,
        committed_cap_kg_s,
    )
    removal_kg_s = (
        molar_mass
        * molar_flow
        / pressure
        * solved.partials_Pa["Na"]
    )
    raw_relative = abs(committed_kg_s - removal_kg_s) / max(
        committed_kg_s,
        removal_kg_s,
        1.0e-300,
    )
    assert not solved.converged
    assert raw_relative > 1.0e-9
    assert solved.residual_relative > 1.0e-9


def test_near_equilibrium_closure_stops_at_binary64_partial_resolution():
    equilibrium = 8.7805834e-9
    beta = 4.20421149e-7
    pressure = 5.71281515e-8
    molar_flow = 2.86761256e-20
    # These are the canonical lunar-hour source and fixed-pressure inputs.
    # G=M*n/P=1.1539991668011175e-14 kg/(s Pa), so the linear balance is
    # p=beta*P_eq/(beta+G)=8.780583158984875e-9 Pa. Its nearest lower float
    # has a raw residual 1.4284034885443587e-31 kg/s (1.4097e-9 relative),
    # while half an ulp times beta+G is 3.4776423310572506e-31 kg/s. That
    # representability bound is what allows this mathematically closed case.

    def evaluate(partials, _total_pressure):
        return _flux(beta * max(0.0, equilibrium - partials["Na"]))

    solved = solve_quasi_steady_headspace_partials_Pa(
        equilibrium_Pa={"Na": equilibrium},
        fixed_partials_Pa={},
        beta_kg_s_Pa_by_species={"Na": beta},
        molar_mass_kg_mol_by_species={"Na": 0.0229898},
        fixed_molar_flow_mol_s_by_species={},
        molar_flow_mol_s_at_pressure=lambda _pressure: molar_flow,
        pressure_bracket_Pa=(0.0, 1.0),
        evaluate_flux=evaluate,
        fixed_pressure_Pa=pressure,
        fixed_pressure_molar_flow_mol_s=molar_flow,
    )

    assert solved.converged
    assert solved.residual_relative <= 1.0e-9


def test_unbracketed_residual_remains_not_converged():
    def evaluate(_partials, _total_pressure):
        return _flux(4.0)

    solved = solve_quasi_steady_headspace_partials_Pa(
        equilibrium_Pa={"Na": 2.0},
        fixed_partials_Pa={},
        beta_kg_s_Pa_by_species={"Na": 1.0e-6},
        molar_mass_kg_mol_by_species={"Na": 1.0},
        fixed_molar_flow_mol_s_by_species={},
        molar_flow_mol_s_at_pressure=lambda _pressure: 1.0,
        pressure_bracket_Pa=(0.0, 3.0),
        evaluate_flux=evaluate,
        fixed_pressure_Pa=2.0,
        fixed_pressure_molar_flow_mol_s=1.0,
    )

    assert not solved.converged
    assert solved.residual_relative > 0.0


def test_committed_flux_bracket_uses_observed_residual_slope():
    def evaluate(_partials, _total_pressure):
        return _flux(1.0e-13)

    solved = solve_quasi_steady_headspace_partials_Pa(
        equilibrium_Pa={"Na": 2.0},
        fixed_partials_Pa={},
        beta_kg_s_Pa_by_species={"Na": 1.0e-6},
        molar_mass_kg_mol_by_species={"Na": 1.0},
        fixed_molar_flow_mol_s_by_species={},
        molar_flow_mol_s_at_pressure=lambda _pressure: 1.0e-12,
        pressure_bracket_Pa=(0.0, 3.0),
        evaluate_flux=evaluate,
        fixed_pressure_Pa=2.0,
        fixed_pressure_molar_flow_mol_s=1.0e-12,
    )

    assert solved.converged
    assert solved.partials_Pa["Na"] == pytest.approx(0.2, abs=1.0e-10)


def test_pressure_bracket_keeps_measured_endpoint_residuals():
    beta = 1.0
    equilibrium = 2.0

    def evaluate(partials, _total_pressure):
        return _flux(beta * max(0.0, equilibrium - partials["Na"]))

    def nonlinear_commit(raw_flux):
        raw_kg_s = float(raw_flux.species_kg_hr["Na"]) / 3600.0
        # This synthetic inventory response is monotone and nonlinear; the
        # raw-source square divided by its 2 kg/s scale still has units kg/s.
        return _flux(raw_kg_s * raw_kg_s / 2.0)

    solved = solve_quasi_steady_headspace_partials_Pa(
        equilibrium_Pa={"Na": equilibrium},
        fixed_partials_Pa={},
        beta_kg_s_Pa_by_species={"Na": beta},
        molar_mass_kg_mol_by_species={"Na": 1.0},
        fixed_molar_flow_mol_s_by_species={},
        molar_flow_mol_s_at_pressure=lambda pressure: pressure * pressure,
        pressure_bracket_Pa=(0.0, 4.0),
        evaluate_flux=evaluate,
        commit_flux=nonlinear_commit,
        fixed_pressure_Pa=1.0,
        fixed_pressure_molar_flow_mol_s=0.1,
        carrier_free_molar_flow_mol_s_at_pressure=(
            lambda pressure: pressure * pressure
        ),
        max_iterations=80,
    )

    assert abs(solved.pressure_residual_Pa) <= 1.0e-9


def test_nonlinear_pressure_secant_keeps_measured_low_residual():
    source_offset_kg_s = 3.0

    def evaluate(partials, _total_pressure):
        return _flux(2.0 - partials["Na"])

    def offset_committed_source(raw_flux):
        raw_kg_s = float(raw_flux.species_kg_hr["Na"]) / 3600.0
        return _flux(raw_kg_s + source_offset_kg_s)

    solved = solve_quasi_steady_headspace_partials_Pa(
        equilibrium_Pa={"Na": 2.0},
        fixed_partials_Pa={},
        beta_kg_s_Pa_by_species={"Na": 1.0},
        molar_mass_kg_mol_by_species={"Na": 1.0},
        fixed_molar_flow_mol_s_by_species={},
        molar_flow_mol_s_at_pressure=lambda pressure: pressure**2,
        pressure_bracket_Pa=(1.4, 2.5),
        evaluate_flux=evaluate,
        commit_flux=offset_committed_source,
        relative_tolerance=1.0e-12,
        max_iterations=2,
    )

    # At fixed P, committed source is (2 - p) + 3 kg/s. With M=1 kg/mol and
    # n(P)=P² mol/s under the synthetic unit scale, removal M*n/P*p is P*p
    # kg/s. Thus p=5/(1+P), so closure has P²+P-5=0. With zero offset, the
    # raw source returns to the unmodified 1 Pa root.
    expected_pressure = (-1.0 + math.sqrt(21.0)) / 2.0
    assert solved.total_pressure_Pa == pytest.approx(
        expected_pressure,
        abs=1.0e-3,
    )


def test_controlled_capacity_uses_shared_partial_solver_at_allowed_pressure():
    # At 200 Pa, a 0.25 mol/s pure 40 g/mol carrier is 36 kg/hr. With
    # beta=2e-6 kg/(s Pa) and P_eq=5 Pa, the hand balance gives p_Na=0.1923077
    # Pa; source and removal are each 9.6153846e-6 kg/s.
    beta = 2.0e-6
    equilibrium = 5.0
    molar_mass = 0.04

    def evaluate(partials, _total_pressure):
        return _flux(beta * (equilibrium - partials["Na"]))

    solved = solve_quasi_steady_headspace_partials_Pa(
        equilibrium_Pa={"Na": equilibrium},
        fixed_partials_Pa={},
        beta_kg_s_Pa_by_species={"Na": beta},
        molar_mass_kg_mol_by_species={"Na": molar_mass},
        fixed_molar_flow_mol_s_by_species={},
        molar_flow_mol_s_at_pressure=lambda _pressure: 0.25,
        pressure_bracket_Pa=(0.0, 201.0),
        evaluate_flux=evaluate,
        fixed_pressure_Pa=200.0,
        fixed_pressure_molar_flow_mol_s=0.25,
    )

    assert solved.converged
    assert solved.partials_Pa["Na"] == pytest.approx(0.1923077, abs=5.0e-8)
    assert solved.flux.species_kg_hr["Na"] / 3600.0 == pytest.approx(
        9.6153846e-6,
        abs=5.0e-13,
    )


@pytest.mark.parametrize("beta", [1.0e-8, 1.0e-6, 1.0e-4])
def test_large_pipe_conductance_approaches_zero_partial_and_free_source(beta):
    equilibrium = 2.0
    molar_mass = 0.023
    coefficient = 1.0e10

    solved = _single_species_solver(
        beta=beta,
        equilibrium=equilibrium,
        pipe_K=coefficient,
        molar_mass=molar_mass,
    )
    assert solved.converged
    assert solved.partials_Pa["Na"] < 1.0e-6 * equilibrium
    free_source_kg_s = beta * equilibrium
    assert solved.flux.total_kg_hr / 3600.0 == pytest.approx(
        free_source_kg_s, rel=1.0e-6
    )


def test_swept_nonzero_downstream_pressure_balances_at_review_value():
    beta = 6.0e-7  # kg/(s Pa)
    equilibrium = 2.0  # Pa
    molar_mass = 0.0229898  # kg/mol
    coefficient = 3.163092e-6  # mol/(s Pa²)
    total_pressure = 1000.0  # Pa; existing swept total
    downstream_pressure = 900.0  # Pa
    removal_per_partial = molar_mass * coefficient * (
        total_pressure - downstream_pressure**2 / total_pressure
    )  # kg/(s Pa)

    solved = _single_species_solver(
        beta=beta,
        equilibrium=equilibrium,
        pipe_K=coefficient,
        molar_mass=molar_mass,
        downstream=downstream_pressure,
        fixed_pressure=total_pressure,
    )
    assert solved.converged
    assert solved.partials_Pa["Na"] == pytest.approx(
        0.0832375, abs=5.0e-8
    )
    assert solved.residual_relative <= 1.0e-9


def test_amplitude_and_empty_hour_are_stable():
    equilibrium = 5.0
    molar_mass = 0.056
    coefficient = 2.0e-6

    roots = [
        _single_species_solver(
            beta=beta,
            equilibrium=equilibrium,
            pipe_K=coefficient,
            molar_mass=molar_mass,
        )
        for beta in (1.0e-9, 1.0e-6, 1.0e-3)
    ]
    assert all(root.converged for root in roots)
    assert all(root.residual_relative <= 1.0e-9 for root in roots)
    assert roots[0].partials_Pa["Na"] < roots[-1].partials_Pa["Na"]

    empty = solve_quasi_steady_headspace_partials_Pa(
        equilibrium_Pa={},
        fixed_partials_Pa={},
        beta_kg_s_Pa_by_species={},
        molar_mass_kg_mol_by_species={},
        fixed_molar_flow_mol_s_by_species={},
        molar_flow_mol_s_at_pressure=lambda _pressure: 0.0,
        pressure_bracket_Pa=(0.0, 1.0),
        evaluate_flux=lambda _partials, _pressure: _flux(0.0),
    )
    assert empty.converged
    assert empty.flux.total_kg_hr == 0.0


def test_scalar_pressure_root_closes_two_species_balance_and_S_is_monotone():
    # With beta=1e-6 kg/(s Pa), M*K*(1 Pa)=beta, and P_eq=(2,4) Pa,
    # p_i(P)=P_eq,i/(1+P/(1 Pa)). Thus S(P)=6 Pa/(1+P/(1 Pa));
    # S=P gives P=2 Pa, p_Na=2/3 Pa, and p_K=4/3 Pa.
    beta = 1.0e-6
    molar_mass = 0.02
    pipe_K = beta / molar_mass
    equilibrium = {"Na": 2.0, "K": 4.0}

    def evaluate(partials, _total_pressure):
        flux = EvaporationFlux(species_kg_hr={
            species: beta * max(0.0, pressure - partials[species]) * 3600.0
            for species, pressure in equilibrium.items()
        })
        flux.update_totals()
        return flux

    def molar_flow(pressure):
        return pipe_K * pressure**2

    solved = solve_quasi_steady_headspace_partials_Pa(
        equilibrium_Pa=equilibrium,
        fixed_partials_Pa={},
        beta_kg_s_Pa_by_species={"Na": beta, "K": beta},
        molar_mass_kg_mol_by_species={"Na": molar_mass, "K": molar_mass},
        fixed_molar_flow_mol_s_by_species={},
        molar_flow_mol_s_at_pressure=molar_flow,
        pressure_bracket_Pa=(0.0, 6.0),
        evaluate_flux=evaluate,
    )
    assert solved.converged
    assert solved.residual_relative <= 1.0e-9
    assert solved.total_pressure_Pa == pytest.approx(2.0, abs=1.0e-9)
    assert solved.partials_Pa["Na"] == pytest.approx(
        2.0 / 3.0, abs=1.0e-9
    )
    assert solved.partials_Pa["K"] == pytest.approx(
        4.0 / 3.0, abs=1.0e-9
    )

    sums = [
        sum(_headspace_partials_at_pressure_Pa(
            pressure,
            equilibrium_Pa=equilibrium,
            beta_kg_s_Pa_by_species={"Na": beta, "K": beta},
            fixed_partials_Pa={},
            fixed_molar_flow_mol_s_by_species={},
            molar_mass_kg_mol_by_species={"Na": molar_mass, "K": molar_mass},
            molar_flow_mol_s=molar_flow(pressure),
        ).values())
        for pressure in (0.5, 1.0, 2.0, 4.0, 6.0)
    ]
    assert all(left > right for left, right in zip(sums, sums[1:]))


def test_fixed_flow_species_and_frozen_oxygen_each_have_residuals():
    # Hand balance at P=2 Pa: the pipe carries 2e-4 mol/s, giving Na=0.633333
    # Pa and K=1.266667 Pa. Fixed Ar contributes 0.05 Pa and frozen O2 keeps
    # its declared 0.05 Pa; the four partials sum to 2 Pa.
    beta = 1.0e-6
    equilibrium = {"Na": 1.9, "K": 3.8}
    molar_mass = {"Na": 0.02, "K": 0.02, "Ar": 0.04, "O2": 0.032}
    fixed_ar_flow = 5.0e-6

    def evaluate(partials, _total_pressure):
        flux = EvaporationFlux(
            species_kg_hr={
                **{
                    species: beta * (pressure - partials[species]) * 3600.0
                    for species, pressure in equilibrium.items()
                },
                "Ar": fixed_ar_flow * molar_mass["Ar"] * 3600.0,
            }
        )
        flux.update_totals()
        return flux

    solved = solve_quasi_steady_headspace_partials_Pa(
        equilibrium_Pa=equilibrium,
        fixed_partials_Pa={"O2": 0.05},
        beta_kg_s_Pa_by_species={"Na": beta, "K": beta},
        molar_mass_kg_mol_by_species=molar_mass,
        fixed_molar_flow_mol_s_by_species={"Ar": fixed_ar_flow},
        molar_flow_mol_s_at_pressure=lambda pressure: 5.0e-5 * pressure**2,
        pressure_bracket_Pa=(0.0, 6.0),
        evaluate_flux=evaluate,
    )

    assert solved.converged
    assert solved.total_pressure_Pa == pytest.approx(2.0, abs=1.0e-9)
    assert solved.partials_Pa["Na"] == pytest.approx(0.6333333333, abs=1.0e-9)
    assert solved.partials_Pa["K"] == pytest.approx(1.2666666667, abs=1.0e-9)
    assert solved.partials_Pa["Ar"] == pytest.approx(0.05, abs=1.0e-9)
    assert solved.partials_Pa["O2"] == pytest.approx(0.05, abs=1.0e-12)
    assert set(solved.residual_relative_by_species) == {"Ar", "K", "Na", "O2"}
    assert all(
        residual <= 1.0e-9
        for residual in solved.residual_relative_by_species.values()
    )


def test_bounded_nonconvergence_returns_best_iterate_and_residual():
    solved = solve_quasi_steady_headspace_partials_Pa(
        equilibrium_Pa={"Na": 2.0},
        fixed_partials_Pa={},
        beta_kg_s_Pa_by_species={"Na": 1.0e-6},
        molar_mass_kg_mol_by_species={"Na": 0.0229898},
        fixed_molar_flow_mol_s_by_species={},
        molar_flow_mol_s_at_pressure=lambda _pressure: 1.0,
        pressure_bracket_Pa=(0.0, 3.0),
        evaluate_flux=lambda _partials, _pressure: _flux(1.0),
        fixed_pressure_Pa=1.0,
        fixed_pressure_molar_flow_mol_s=1.0,
        max_iterations=1,
    )
    assert not solved.converged
    assert math.isfinite(solved.partials_Pa["Na"])
    assert solved.residual_relative > 0.0
    assert solved.flux.species_kg_hr["Na"] > 0.0

import math

import pytest

from simulator.overhead import (
    OverheadGasModel,
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


def test_near_equilibrium_closure_stops_at_binary64_partial_resolution():
    equilibrium = 8.7805834e-9
    beta = 4.20421149e-7
    pressure = 5.71281515e-8
    molar_flow = 2.86761256e-20
    calls = 0

    def evaluate(partials, _total_pressure):
        nonlocal calls
        calls += 1
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
    assert calls < 10


def test_unbracketed_residual_does_not_repeat_an_unchanged_candidate():
    calls = 0

    def evaluate(_partials, _total_pressure):
        nonlocal calls
        calls += 1
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
    assert calls <= 4


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

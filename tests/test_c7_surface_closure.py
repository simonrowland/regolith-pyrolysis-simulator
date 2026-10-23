"""C7 signed finite-source closure, independent limits and live projection."""
from copy import deepcopy
import math

import pytest

from simulator.chemistry.langmuir_knudsen import (
    OxygenClosureNonconvergence, _SurfaceOxygenEvaluation,
    _solve_surface_oxygen_balance,
)
from simulator.evaporation import EvaporationFluxRefusal
from simulator.transport_regime import series_evaporation_flux
from tests.test_c7_closure_routing import simulation
from tests.magemin_test_support import close_test_owned_magemin_backend

pytestmark = [pytest.mark.serial, pytest.mark.xdist_group('serial')]


def evaluation(x, *, source=5.0, ferric=-2.0, other=-0.5, storage=1.0):
    return _SurfaceOxygenEvaluation(x, 0.0, 2.0 * x * 100000.0, 0.0,
                                    {'reaction': source}, ferric, other, storage)


def test_signed_ferric_storage_and_consuming_source():
    solved = _solve_surface_oxygen_balance(evaluation)
    # 2*x_Pa = 5 + 0.5*(-2) - 0.5 - 1 = 2.5 mol/(m² s).
    assert solved.reaction_pO2_bar == pytest.approx(1.25e-5, rel=1e-11)
    assert abs(solved.residual) < 1e-10


def test_proved_source_free_zero_is_exact():
    zero = evaluation(0.0, source=0.0, ferric=0.0, other=0.0, storage=0.0)
    assert _solve_surface_oxygen_balance(lambda _: pytest.fail('zero enters log'),
                                        source_free_zero=zero) is zero


def test_positive_source_expands_below_original_bracket():
    solved = _solve_surface_oxygen_balance(
        lambda x: evaluation(x, source=2e-45, ferric=0.0, other=0.0, storage=0.0))
    assert solved.reaction_pO2_bar == pytest.approx(1e-50, rel=1e-10, abs=0.0)


def test_failed_bracket_is_typed_and_never_an_endpoint():
    with pytest.raises(OxygenClosureNonconvergence, match='no interior'):
        _solve_surface_oxygen_balance(lambda x: evaluation(x, source=-1.0,
            ferric=0.0, other=0.0, storage=0.0))


def test_coordinate_convergence_across_a_jump_is_not_flux_convergence():
    def jump(x):
        return _SurfaceOxygenEvaluation(x, 0.0, 1.0 if x >= 1e-6 else 0.0,
                                        0.0, {'source': 0.5}, 0.0, 0.0, 0.0)
    with pytest.raises(OxygenClosureNonconvergence, match='without closing'):
        _solve_surface_oxygen_balance(jump)


def test_runtime_source_free_zero():
    sim = simulation('mars_basalt', 'C2A')
    try:
        sim.melt.temperature_C = 20.0
        sim._reset_redox_source_diagnostics_for_hour()
        sim._establish_melt_redox_gate_authority_for_current_hour()
        sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()
        solved = sim._solve_evaporative_oxygen_boundary(sim._get_equilibrium())
        assert solved.reaction_pO2_bar == solved.gas_face_pO2_bar == 0.0
        assert solved.residual == 0.0
    finally:
        close_test_owned_magemin_backend(sim)


def test_hot_empty_parent_inventory_proves_zero_without_pressure_evaluation():
    from simulator.accounting import AtomLedger
    from simulator.melt_backend.base import EquilibriumResult
    sim = simulation('mars_basalt')
    try:
        sim.melt.temperature_C = 1600.0
        sim._reset_redox_source_diagnostics_for_hour()
        sim._establish_melt_redox_gate_authority_for_current_hour()
        # An exhausted current-hour inventory retains that hour's admitted
        # gate measurement. No carrier or Fe parcel remains to fund a source.
        sim.atom_ledger = AtomLedger(registry=sim.species_formula_registry)
        sim._chem_kernel = sim._build_chemistry_kernel()
        sim._project_cleaned_melt_from_atom_ledger()
        sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()
        assert sim.vapour_rail_catalog.build_request(sim.atom_ledger.mol_by_account()) == frozenset()
        result = EquilibriumResult(temperature_C=1600.0, liquid_fraction=1.0)
        solved = sim._solve_evaporative_oxygen_boundary(result)
        assert solved.reaction_pO2_bar == solved.residual == 0.0
    finally:
        close_test_owned_magemin_backend(sim)


@pytest.mark.parametrize('eq,bulk', ((0.1, 0.0), (0.0, 0.1)))
def test_signed_series_recovers_hk_limit(eq, bulk):
    from simulator.physical_constants import GAS_CONSTANT
    mass, temperature = 0.031998, 1800.0
    result = series_evaporation_flux('O2', eq, bulk, temperature, mass, 1.0,
        gas_resistance_enabled=False, signed=True,
        stir_diagnostic=dict(axial_stir_applied=0.0, radial_stir_applied=0.0,
            axial_stir_clamped=False, radial_stir_clamped=False,
            frozen_skull_stir_clamped=False, frozen_skull_stir_ceiling=1.0))
    assert result.flux_kg_s_m2 == pytest.approx(
        (eq - bulk) * math.sqrt(mass / (2 * math.pi * GAS_CONSTANT * temperature)))
    assert result.r_gas == result.r_melt == 0.0


def test_projection_is_read_only_and_commits_the_same_signed_amounts():
    sim = simulation()
    try:
        sim.melt.temperature_C = 1600.0
        sim._reset_redox_source_diagnostics_for_hour()
        sim._establish_melt_redox_gate_authority_for_current_hour()
        sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()
        equilibrium = sim._get_equilibrium()
        before = (sim.atom_ledger.mol_by_account(), len(sim.atom_ledger.transitions),
                  deepcopy(sim.melt.oxygen_reservoir), deepcopy(sim.train))
        solved = sim._solve_evaporative_oxygen_boundary(equilibrium)
        assert solved.reaction_pO2_bar > solved.gas_face_pO2_bar >= sim._vapor_pressure_transport_pO2_bar()
        assert (sim.atom_ledger.mol_by_account(), len(sim.atom_ledger.transitions),
                sim.melt.oxygen_reservoir, sim.train) == before
        flux, predicted, trial, _, _ = solved.payload
        assert trial.atom_ledger is not sim.atom_ledger
        assert trial.cost_ledger is not sim.cost_ledger
        assert trial._chem_kernel is not sim._chem_kernel
        start = len(sim.atom_ledger.transitions)
        stored = sim.atom_ledger.mol_by_account('reservoir.fo2_buffer').get('O2', 0.0)
        sim._apply_evaporation_carrier_batch(flux)
        actual = sim._evaporation_signed_amounts(start, stored)
        sim._verify_evaporation_projection(predicted, actual)
        # Net source equals actual gas accumulation; finite stored oxygen and
        # ferric uptake are individually included, not inferred from fO2.
        amounts, _ = actual
        net = math.fsum(value for key, value in amounts.items() if key.startswith('source:'))
        net += amounts['ferric_oxygen'] + amounts['other_oxygen'] - amounts['storage']
        gas_delta = sim.atom_ledger.mol_by_account('process.overhead_gas').get('O2', 0.0) - before[0].get('process.overhead_gas', {}).get('O2', 0.0)
        assert net == pytest.approx(gas_delta, rel=1e-12, abs=0.0)
        changed = deepcopy(actual)
        changed[0]['storage'] += 1.0
        with pytest.raises(EvaporationFluxRefusal, match='oxygen_projection_commit_mismatch'):
            sim._verify_evaporation_projection(predicted, changed)
    finally:
        close_test_owned_magemin_backend(sim)

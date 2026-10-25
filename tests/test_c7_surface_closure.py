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
                                    {'reaction': source}, ferric, other, storage,
                                    source + 0.5 * ferric + other - storage)


def test_signed_ferric_storage_and_consuming_source():
    solved = _solve_surface_oxygen_balance(evaluation)
    # 2*x_Pa = 5 + 0.5*(-2) - 0.5 - 1 = 2.5 mol/(m² s).
    assert solved.reaction_pO2_bar == pytest.approx(1.25e-5, rel=1e-11)
    assert abs(solved.residual) < 1e-10


def test_proved_source_free_zero_is_exact():
    zero = evaluation(0.0, source=0.0, ferric=0.0, other=0.0, storage=0.0)
    assert _solve_surface_oxygen_balance(lambda _: pytest.fail('zero enters log'),
                                        source_free_zero=zero) is zero


@pytest.mark.parametrize('bulk', (1.0365655169846737e-27, 3.73282716125e-21))
def test_source_free_positive_bulk_is_exact(bulk):
    def evaluate(excess):
        return _SurfaceOxygenEvaluation(
            bulk + excess, bulk, 0.1 * excess, 0.0, {}, 0.0, 0.0, 0.0, 0.0,
            bulk_pO2_bar=bulk,
        )
    solved = _solve_surface_oxygen_balance(evaluate, bulk_pO2_bar=bulk)
    assert solved.reaction_pO2_bar == solved.gas_face_pO2_bar == bulk
    assert solved.oxygen_flux_mol_m2_s == solved.residual == 0.0


def test_positive_source_expands_below_original_bracket():
    solved = _solve_surface_oxygen_balance(
        lambda x: evaluation(x, source=2e-45, ferric=0.0, other=0.0, storage=0.0))
    assert solved.reaction_pO2_bar == pytest.approx(1e-50, rel=1e-10, abs=0.0)


def test_failed_bracket_is_typed_and_never_an_endpoint():
    with pytest.raises(OxygenClosureNonconvergence, match='no interior'):
        _solve_surface_oxygen_balance(lambda x: evaluation(x, source=-1.0,
            ferric=0.0, other=0.0, storage=0.0))


def test_vacuum_consuming_source_can_close_at_physical_zero():
    def consuming(x):
        return evaluation(x, source=-x ** 0.25, ferric=0.0, other=0.0, storage=0.0)

    solved = _solve_surface_oxygen_balance(consuming)
    assert solved.reaction_pO2_bar == solved.gas_face_pO2_bar == 0.0
    assert not any(solved.balance_terms)


def test_unavailable_zero_endpoint_remains_a_typed_refusal():
    def consuming(x):
        if x == 0.0:
            raise ValueError('positive-pressure chemical-potential boundary required')
        return evaluation(x, source=-x ** 0.25, ferric=0.0, other=0.0, storage=0.0)

    with pytest.raises(OxygenClosureNonconvergence, match='zero endpoint unavailable'):
        _solve_surface_oxygen_balance(consuming)


def test_runtime_vacuum_consuming_carriers_prove_zero_source():
    sim = simulation('s_type_asteroid_silicate')
    try:
        sim.melt.temperature_C = 1055.0
        sim._frozen_evaporation_carriers = frozenset({'CrO2', 'CrO3'})
        sim._reaction_pO2_bar = 0.0
        assert sim._evaporation_carrier_zero_reason() == 'frozen_consuming_carriers_at_zero_oxygen'
        # Positive oxygen has a physical consuming flux; the zero proof must
        # never suppress it away from the boundary.
        sim._reaction_pO2_bar = 1e-12
        assert sim._evaporation_carrier_zero_reason() is None
    finally:
        close_test_owned_magemin_backend(sim)


def test_coordinate_convergence_across_a_jump_is_not_flux_convergence():
    def jump(x):
        return _SurfaceOxygenEvaluation(x, 0.0, 1.0 if x >= 1e-6 else 0.0,
                                        0.0, {'source': 0.5}, 0.0, 0.0, 0.0, 0.5)
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


def test_runtime_cold_mars_source_free_positive_bulk():
    from simulator.config import load_config_bundle
    from simulator.state import CampaignPhase
    from tests.chemistry.conftest import _build_sim
    bundle = load_config_bundle()
    sim = _build_sim('mars_basalt', bundle.vapor_pressures, bundle.feedstocks,
                     bundle.setpoints, additives_kg={'C': 60.0})
    try:
        sim.start_campaign(CampaignPhase.C0)
        sim.step()
        closure = sim._last_surface_oxygen_diagnostic
        assert closure['bulk_pO2_bar'] > 0.0
        assert closure['reaction_pO2_bar'] == closure['gas_face_pO2_bar'] == closure['bulk_pO2_bar']
        assert closure['oxygen_flux_mol_m2_s'] == closure['relative_residual'] == 0.0
        assert not any(closure['source_terms_mol_m2_s'].values())
        assert closure['ferric_rate_mol_m2_s'] == closure['other_oxygen_mol_m2_s'] == closure['storage_mol_m2_s'] == 0.0
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
        # Re-enter the real legacy/backend + kernel refresh path at that zero.
        sim._reaction_pO2_bar = solved.reaction_pO2_bar
        assert sim._get_equilibrium().vapor_pressures_Pa == {}
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
        before = (sim.atom_ledger.mol_by_account(), len(sim.atom_ledger.transitions),
                  deepcopy(sim.melt.oxygen_reservoir), deepcopy(sim.train))
        solved = sim._solve_evaporative_oxygen_boundary()
        assert solved.reaction_pO2_bar > solved.gas_face_pO2_bar >= sim._vapor_pressure_transport_pO2_bar()
        assert (sim.atom_ledger.mol_by_account(), len(sim.atom_ledger.transitions),
                sim.melt.oxygen_reservoir, sim.train) == before
        flux, predicted, trial, _, _ = solved.payload
        assert trial.atom_ledger is not sim.atom_ledger
        assert trial.cost_ledger is not sim.cost_ledger
        assert trial._chem_kernel is not sim._chem_kernel
        sim._reaction_pO2_bar = solved.reaction_pO2_bar
        sim._get_equilibrium()
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


def test_pressure_projections_share_committed_history_and_copy_mutable_frontier():
    class CommittedSnapshot:
        def __deepcopy__(self, _memo):
            raise AssertionError('pressure trials must not recursively copy committed history')

    sim = simulation()
    try:
        committed = CommittedSnapshot()
        sim.record.snapshots.append(committed)
        sim.condensation_model.operating_history.append({'nested': {'wall_temperature_C': 25.0}})
        before = sim.atom_ledger.mol_by_account()
        trial = sim._detached_evaporation_projection()
        nested = trial._detached_evaporation_projection()
        for projection in (trial, nested):
            assert projection.record.snapshots[0] is committed
            assert projection.record.snapshots is not sim.record.snapshots
            assert projection.atom_ledger.mol_by_account() == before
            assert projection.atom_ledger is not sim.atom_ledger
        trial.record.snapshots.append(committed)
        trial.condensation_model.operating_history[-1]['nested']['wall_temperature_C'] = 99.0
        assert len(sim.record.snapshots) == len(nested.record.snapshots) == 1
        assert sim.condensation_model.operating_history[-1]['nested']['wall_temperature_C'] == 25.0
        assert nested.condensation_model.operating_history[-1]['nested']['wall_temperature_C'] == 25.0
    finally:
        close_test_owned_magemin_backend(sim)


def test_pressure_projection_shares_read_only_inputs_but_rollback_detaches_them():
    class ReadOnlyInputs(dict):
        def __deepcopy__(self, _memo):
            raise AssertionError('pressure trials must not copy read-only input tables')

    sim = simulation()
    names = ('feedstocks', 'vapor_pressure_catalog_data', 'vapor_pressures')
    try:
        for name in names:
            setattr(sim, name, ReadOnlyInputs(getattr(sim, name)))
        trial = sim._detached_evaporation_projection()
        for name in names:
            assert getattr(trial, name) is getattr(sim, name)
        assert trial.melt is not sim.melt
        assert trial.atom_ledger is not sim.atom_ledger
        # Whole-hour rollback keeps its original mutable-input contract.
        for name in names:
            setattr(sim, name, {'sample': {'value': 1000.0}})
        state, _, _ = sim._snapshot_terminal_refusal_hour_state()
        for name in names:
            getattr(sim, name)['sample']['value'] = 2000.0
            assert state[name]['sample']['value'] == 1000.0
    finally:
        close_test_owned_magemin_backend(sim)


@pytest.mark.parametrize('finite_capacity', (False, True))
def test_real_hour_closes_current_sources_and_preserves_oxygen_atoms(monkeypatch, finite_capacity):
    sim = simulation()
    try:
        if finite_capacity:
            from simulator.thermal_train import FiniteCapacity
            _, cold_train = sim._cold_train_capacity_policy()
            monkeypatch.setattr(sim, '_cold_train_capacity_policy',
                                lambda: (FiniteCapacity(1.0), cold_train))
        sim.melt.temperature_C = 1600.0
        sim.campaign_mgr.overrides['C2A'] = {'ramp_rate': 0.0, 'min_hold_hr': 24.0, 'max_hours': 24.0}
        def oxygen_atoms():
            return math.fsum(sim.atom_ledger.atom_moles_by_account(account).get('O', 0.0)
                             for account in sim.atom_ledger.mol_by_account())
        before = oxygen_atoms()
        sim.step()
        assert sim.melt.hour == 1
        diagnostic = sim._last_surface_oxygen_diagnostic
        assert diagnostic['reaction_pO2_bar'] > 0.0
        assert diagnostic['relative_residual'] < math.sqrt(math.ulp(1.0))
        assert oxygen_atoms() == pytest.approx(before, rel=0.0, abs=8.0 * math.ulp(before))
    finally:
        close_test_owned_magemin_backend(sim)


@pytest.mark.parametrize('campaign', ('C6', 'C7_CA_ALUMINOTHERMIC'))
def test_non_evaporating_step_uses_published_intrinsic_oxygen(campaign):
    from simulator.state import CampaignPhase
    from tests.test_c7_ca_aluminothermic_campaign import _c7_patch
    sim = simulation('targeted_super_kreep_ore' if campaign.startswith('C7') else 'lunar_mare_low_ti')
    try:
        if campaign.startswith('C7'):
            sim.setpoints['campaigns']['C7'].update(_c7_patch()['campaigns']['C7'])
        sim.start_campaign(CampaignPhase[campaign])
        sim.melt.temperature_C = 1400.0 if campaign == 'C6' else 1600.0
        # A direct query after changing campaign must not consume the previous
        # interval's accepted coordinate or source-free carrier admission.
        sim._reaction_pO2_bar = 0.0 if campaign == 'C6' else 1e-12
        sim._frozen_evaporation_carriers = frozenset()
        sim._last_surface_oxygen_diagnostic = {'completed_interval': True}
        sim._get_equilibrium()
        query = sim._last_vapor_pressure_diagnostic
        assert query['pO2_bar'] == 10.0 ** query['intrinsic_fO2_publication']['published_intrinsic_fO2_log']
        assert sim._reaction_pO2_bar is None
        assert sim._frozen_evaporation_carriers is None
        assert sim._last_surface_oxygen_diagnostic == {'completed_interval': True}
        sim.step()
        diagnostic = sim._last_vapor_pressure_diagnostic
        published = diagnostic['intrinsic_fO2_publication']['published_intrinsic_fO2_log']
        assert diagnostic['pO2_bar'] == 10.0 ** published > 0.0
        assert math.isfinite(diagnostic['pO2_bar'])
        assert any('no gas-side closure; published intrinsic' in str(note)
                   for note in diagnostic['kernel_vapor_pressure_warnings'])
        assert sim.melt.hour == 1
    finally:
        close_test_owned_magemin_backend(sim)


def test_fixed_admission_preserves_subfloor_carrier_and_oxygen_legs():
    from simulator.state import EvaporationFlux
    sim = simulation()
    try:
        tiny = EvaporationFlux(species_kg_hr={'Na': 2e-14, 'K': 2e-14})
        assert sim._apply_analytic_evaporation_depletion(tiny).species_kg_hr == {}
        sim._frozen_evaporation_carriers = frozenset({'Na'})
        continued = sim._apply_analytic_evaporation_depletion(tiny)
        assert set(continued.species_kg_hr) == {'Na'}
        assert 0 < continued.species_kg_hr['Na'] <= 2e-14
        before = {element: math.fsum(sim.atom_ledger.atom_moles_by_account(account).get(element, 0.0)
                  for account in sim.atom_ledger.mol_by_account()) for element in ('Na', 'O')}
        _, transition = sim._credit_evaporation_transition(
            'Na', continued.species_kg_hr['Na'], continued.species_kg_hr['Na'],
            sim.vapor_pressures['metals']['Na'], return_transition=True)
        assert transition is not None
        # The actual balanced transition, including its smaller oxygen leg,
        # must reach the ledger; a carrier-only continuation is insufficient.
        receipt, _ = sim._evaporation_signed_amounts(len(sim.atom_ledger.transitions)-1,
            sim.atom_ledger.mol_by_account('reservoir.fo2_buffer').get('O2', 0.0))
        assert receipt['carrier:Na'] > 0.0
        assert receipt['source:Na'] > 0.0
        after = {element: math.fsum(sim.atom_ledger.atom_moles_by_account(account).get(element, 0.0)
                 for account in sim.atom_ledger.mol_by_account()) for element in before}
        for element in before:
            assert abs(after[element] - before[element]) <= math.fsum(
                (math.ulp(before[element]), math.ulp(after[element])))
    finally:
        close_test_owned_magemin_backend(sim)


@pytest.mark.parametrize('disabled_p4', (False, True))
def test_cold_admission_jump_accepts_zero_and_flags_positive_side_limit(disabled_p4, monkeypatch):
    sim = simulation('lunar_mare_low_ti', 'C0')
    try:
        # Hour three of the existing C0 ramp formerly jumped across the P4
        # numerical cutoff inside the log solve.
        sim.melt.temperature_C = 170.0
        if disabled_p4:
            monkeypatch.setitem(sim.vapor_pressures['oxide_vapors']['P4'],
                                'evaporation_alpha', {'value': 0.0})
        sim._reset_redox_source_diagnostics_for_hour()
        sim._establish_melt_redox_gate_authority_for_current_hour()
        sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()
        solved = sim._solve_evaporative_oxygen_boundary()
        projected = solved.payload[2]
        assert projected._frozen_evaporation_carriers == frozenset()
        assert solved.reaction_pO2_bar == 0.0
        assert not any(solved.balance_terms)
        admitted = {item['species'] for item in projected._carrier_admission_flips
                    if item['direction'] == 'admitted'}
        assert ('P4' in admitted) is not disabled_p4
        assert ('P2' in admitted) is disabled_p4
        # Si3's seed pressure is positive but its HK rate underflows. Its
        # stronger inverse power still dominates Si2 in the exact limit.
        assert 'Si3' in admitted and 'Si2' not in admitted
        assert any('carrier admission flips at the solved surface oxygen; frozen-set solution used'
                   in notice for notice in projected._carrier_admission_notices)
    finally:
        close_test_owned_magemin_backend(sim)


def test_zero_limit_keeps_dominant_parent_channel_and_ordinary_stock_gates():
    sim = simulation()
    try:
        parent = sim.atom_ledger.kg_by_account('process.cleaned_melt')['P2O5']
        stoich = sim._evaporation_stoich('P4', sim.vapor_pressures['oxide_vapors']['P4'])
        rates = sim._analytic_evaporation_depletion_rates(
            {'P4': 1e-280, 'P2': 1e-140, 'PO2': 1e-40}, dt_hr=1.0,
            phase_scalar=1.0, cleaned_melt_kg={'P2O5': parent}, available_o2_kg=0.0,
            zero_oxygen_exponents={'P4': -5.0, 'P2': -2.5, 'PO2': -0.25})
        assert set(rates) == {'P4'}
        # Conservation bounds the unique leading channel by its complete
        # parent inventory in this one-hour interval.
        assert rates['P4'] * stoich['oxide_per_product_kg'] == pytest.approx(parent)
        assert sim._analytic_evaporation_depletion_rates(
            {'P4': 1e-280}, dt_hr=1.0, phase_scalar=1.0,
            cleaned_melt_kg={'P2O5': 1e-14}, available_o2_kg=0.0,
            zero_oxygen_exponents={'P4': -5.0}) == {}
    finally:
        close_test_owned_magemin_backend(sim)


def test_bulk_film_retains_species_without_an_active_source(monkeypatch):
    from simulator.chemistry.kernel import ChemistryIntent
    sim = simulation()
    try:
        sim.melt.temperature_C = 875.0
        sim._reaction_pO2_bar = 1e-9
        sim._melt_headspace_composition_mbar = {'Na': 0.01}
        eq = sim._get_equilibrium()
        sim._frozen_evaporation_carriers = frozenset({'K'})
        eq.vapor_pressures_Pa = {'K': eq.vapor_pressures_Pa['K']}
        seen = []
        dispatch = sim._dispatch_only
        def observe(intent, **kwargs):
            if intent is ChemistryIntent.EVAPORATION_FLUX:
                seen.append(kwargs['control_inputs'])
            return dispatch(intent, **kwargs)
        monkeypatch.setattr(sim, '_dispatch_only', observe)
        sim._calculate_evaporation(eq)
        # 0.01 mbar * 100 Pa/mbar = 1 Pa, even with no current Na source.
        assert seen[0]['overhead_partials_Pa']['Na'] == 1.0
        assert seen[0]['overhead_pressure_pa'] >= 1.0
    finally:
        close_test_owned_magemin_backend(sim)


@pytest.mark.parametrize('species', ('CrO2', 'CrO3', 'SiO2_gas'))
def test_pressure_owner_zero_oxygen_limit(species, monkeypatch):
    from simulator.config import load_config_bundle
    from simulator.vapour_rail import catalog
    evaluator = catalog.compile_vapour_rail_catalog(load_config_bundle().vapor_pressures.catalog_payload).evaluator_for_hot_train(
        species, process_phase='hot_train')
    anchor = evaluator.evaluate(1343.15, source_activity=0.1, pO2_bar=1.0).pressure_pa
    monkeypatch.setattr(catalog, 'o2_potential_from_pO2_bar',
                        lambda **kw: pytest.fail('zero oxygen entered logarithmic adapter'))
    pressure = evaluator.evaluate(1343.15, source_activity=0.1, pO2_bar=0.0).pressure_pa
    assert pressure == (0.0 if species != 'SiO2_gas' else anchor)


def test_consuming_pressure_underflow_is_zero():
    from dataclasses import replace
    from simulator.config import load_config_bundle
    from simulator.vapour_rail.catalog import compile_vapour_rail_catalog
    evaluator = compile_vapour_rail_catalog(load_config_bundle().vapor_pressures.catalog_payload).evaluator_for_hot_train(
        'CrO3', process_phase='hot_train')
    # A steep, consuming mass-action law can be below binary64 pressure.
    evaluator = replace(evaluator, pO2_exponent=2.0, reaction_terms=())
    assert evaluator.evaluate(1343.15, source_activity=0.1, pO2_bar=1e-300).pressure_pa == 0.0


def test_mixed_asteroid_zero_keeps_neutral_pressure():
    from simulator.melt_backend.base import EquilibriumResult
    sim = simulation('s_type_asteroid_silicate')
    try:
        sim.melt.temperature_C = 1070.0
        sim._reset_redox_source_diagnostics_for_hour()
        sim._frozen_evaporation_carriers = frozenset({'CrO2', 'CrO3', 'SiO2_gas'})
        sim._reaction_pO2_bar = 0.0
        assert sim._evaporation_carrier_zero_reason() == 'frozen_consuming_carriers_at_zero_oxygen'
        result = EquilibriumResult(temperature_C=1070.0, liquid_fraction=1.0)
        sim._refresh_vapor_pressures_from_kernel(result, include_diagnostic_shadows=False)
        assert result.vapor_pressures_Pa.get('CrO2', 0.0) == 0.0
        assert result.vapor_pressures_Pa.get('CrO3', 0.0) == 0.0
        assert result.vapor_pressures_Pa['SiO2_gas'] > 0.0
        legacy = sim._internal_analytical_equilibrium()
        assert legacy.vapor_pressures_Pa == result.vapor_pressures_Pa
        flux = sim._calculate_evaporation(result)
        assert flux.species_kg_hr['SiO2_gas'] > 0.0
        solved = sim._solve_evaporative_oxygen_boundary(result)
        assert solved.reaction_pO2_bar == solved.residual == 0.0
        proposed, amounts, trial, _, _ = solved.payload
        assert proposed.species_kg_hr.get('CrO2', 0.0) == 0.0
        assert proposed.species_kg_hr.get('CrO3', 0.0) == 0.0
        assert proposed.species_kg_hr['SiO2_gas'] > 0.0
        assert amounts[0]['carrier:SiO2_gas'] > 0.0
        assert any('oxygen-consuming carriers stop at zero oxygen' in notice
                   for notice in trial._carrier_admission_notices)
    finally:
        close_test_owned_magemin_backend(sim)


def test_internal_buffer_coproduct_is_not_overhead_gas_demand():
    # Lunar smoke hour 29: store the coproduct without subtracting two gross
    # floats to recover the gas demand. The overhead ledger delta owns R.
    stored = 0.00045222020932088954
    point = _SurfaceOxygenEvaluation(
        7.706491106080192e-26, 7.706491106080192e-26, 0.0, 0.0,
        {'Fe_Na_K_Mn': stored - 5.7520419261263434e-18}, 0.0, 0.0, stored,
        overhead_oxygen_mol_m2_s=0.0)
    assert point.residual == 0.0
    assert point.storage_mol_m2_s == stored


def test_transport_bounded_batch_retains_internal_buffer_coproduct():
    from simulator.state import EvaporationFlux
    sim = simulation('s_type_asteroid_silicate')
    try:
        sim.melt.temperature_C = 1070.0
        sim._reset_redox_source_diagnostics_for_hour()
        sim._establish_melt_redox_gate_authority_for_current_hour()
        sim.atom_ledger.load_external_mol('process.overhead_gas', {'O2': 1.0},
            material_origin='feedstock', source='test finite headspace')
        sim._frozen_evaporation_carriers = frozenset({'Na', 'CrO2', 'CrO3'})
        flux = EvaporationFlux(species_kg_hr={
            'Na': 0.001, 'CrO2': 5.808349183114205e-10,
            'CrO3': 3.3342076872338656e-10})
        flux.update_totals()
        full = sim._detached_evaporation_projection()
        limited = sim._detached_evaporation_projection()
        full._apply_evaporation_carrier_batch(deepcopy(flux))
        start = len(limited.atom_ledger.transitions)
        before = limited.atom_ledger.mol_by_account('reservoir.fo2_buffer').get('O2', 0.0)
        # Lunar inward film supply at the recorded area and one-hour dt.
        supply = 4.993391080925191e-25 * 0.41006003303200633 * 3600.0
        limited._apply_evaporation_carrier_batch(deepcopy(flux), oxygen_uptake_capacity_mol=supply)
        amounts, _ = limited._evaporation_signed_amounts(start, before)
        assert amounts['gas:O2'] == pytest.approx(-supply, rel=1e-12, abs=0.0)
        assert limited.atom_ledger.mol_by_account('reservoir.fo2_buffer') == full.atom_ledger.mol_by_account('reservoir.fo2_buffer')
        assert limited._oxygen_uptake_transport_limited
    finally:
        close_test_owned_magemin_backend(sim)


def test_lunar_shaped_debit_resettles_at_inward_endpoint():
    from simulator.state import EvaporationFlux
    from tests.test_c7_pressure_excess import oxygen_transport

    bulk = 7.706491106080192e-26
    area_dt = 0.41006003303200633 * 3600.0
    stored = 0.00045222020932088954
    debit = 5.7520419261263434e-18 * area_dt
    sim = simulation('s_type_asteroid_silicate')
    try:
        sim.melt.temperature_C = 1145.0
        sim._reset_redox_source_diagnostics_for_hour()
        sim._establish_melt_redox_gate_authority_for_current_hour()
        sim.atom_ledger.load_external_mol('process.overhead_gas', {'O2': 1.0},
            material_origin='feedstock', source='test unconstrained debit inventory')
        sim._frozen_evaporation_carriers = frozenset({'Na', 'CrO2', 'CrO3'})
        flux = EvaporationFlux(species_kg_hr={
            'Na': 0.001, 'CrO2': 5.808349183114205e-10,
            'CrO3': 3.3342076872338656e-10})
        flux.update_totals()
        full = sim._detached_evaporation_projection()
        start = len(full.atom_ledger.transitions)
        full._apply_evaporation_carrier_batch(deepcopy(flux))
        amounts, _ = full._evaporation_signed_amounts(start, 0.0)
        # Scale actual balanced carrier legs to the captured lunar debit and
        # buffer coproduct, rather than copying a residual or stoichiometry law.
        for species in ('CrO2', 'CrO3'):
            flux.species_kg_hr[species] *= debit / -amounts['gas:O2']
        flux.species_kg_hr['Na'] *= stored * area_dt / amounts['storage']
        flux.update_totals()

        def point(delta, *, bounded=False):
            j = oxygen_transport(bulk, delta, film=True, temperature_K=1418.15, area_m2=0.41006003303200633).flux_kg_s_m2 / 0.031998
            trial = sim._detached_evaporation_projection()
            start = len(trial.atom_ledger.transitions)
            trial._apply_evaporation_carrier_batch(deepcopy(flux),
                oxygen_uptake_capacity_mol=(max(0.0, -j) * area_dt if bounded else None))
            amounts, _ = trial._evaporation_signed_amounts(start, 0.0)
            return _SurfaceOxygenEvaluation(
                bulk + delta, bulk, j, 0.0,
                {k: v / area_dt for k, v in amounts.items() if k.startswith('source:')},
                2.0 * amounts['ferric_oxygen'] / area_dt,
                amounts['other_oxygen'] / area_dt, amounts['storage'] / area_dt,
                amounts['gas:O2'] / area_dt,
                oxygen_uptake_transport_limited=trial._oxygen_uptake_transport_limited)

        solved = _solve_surface_oxygen_balance(point, bulk_pO2_bar=bulk,
            settle_inward_endpoint=lambda delta: point(delta, bounded=True))
        assert solved.reaction_pO2_bar == 0.0
        assert solved.oxygen_uptake_transport_limited
        assert solved.storage_mol_m2_s == pytest.approx(stored, rel=1e-12, abs=0.0)
        assert solved.overhead_oxygen_mol_m2_s == pytest.approx(solved.oxygen_flux_mol_m2_s, rel=1e-12, abs=0.0)
    finally:
        close_test_owned_magemin_backend(sim)


def test_finite_consuming_forcing_at_positive_bulk_still_refuses():
    bulk = 7.706491106080192e-26
    def point(delta):
        return _SurfaceOxygenEvaluation(bulk + delta, bulk, delta, 0.0,
            {'imposed_sink': -1.0}, 0.0, 0.0, 0.0, -1.0)
    with pytest.raises(OxygenClosureNonconvergence, match='no interior'):
        _solve_surface_oxygen_balance(point, bulk_pO2_bar=bulk,
                                     settle_inward_endpoint=point)


@pytest.mark.parametrize('species,oxygen', (('CrO2', False), ('SiO', 0.0)))
def test_zero_limit_does_not_bypass_other_pressure_refusals(species, oxygen):
    from simulator.config import load_config_bundle
    from simulator.vapour_rail.catalog import compile_vapour_rail_catalog, CatalogCompileError
    evaluator = compile_vapour_rail_catalog(load_config_bundle().vapor_pressures.catalog_payload).evaluator_for_hot_train(
        species, process_phase='hot_train')
    with pytest.raises(CatalogCompileError):
        evaluator.evaluate(1343.15, source_activity=0.1, pO2_bar=oxygen)


def test_captured_asteroid_mixed_seeds_close_at_zero():
    from tests.test_c7_pressure_excess import oxygen_transport
    sim = simulation('s_type_asteroid_silicate')
    try:
        seeds = {'CrO2': 5.808349183114205e-10,
                 'CrO3': 3.3342076872338656e-10,
                 'SiO2_gas': 5.385943982436044e-12}
        evaluators = {s: sim.vapour_rail_catalog.evaluator_for_hot_train(s, process_phase='hot_train') for s in seeds}
        assert {s: e.pO2_exponent for s, e in evaluators.items()} == {'CrO2': 0.25, 'CrO3': 0.75, 'SiO2_gas': 0.0}
        anchor = {s: e.evaluate(1343.15, source_activity=0.1, pO2_bar=1.0).pressure_pa for s, e in evaluators.items()}
        area_dt = 0.41006003303200633 * 3600.0
        def point(delta):
            # HK flux is linear in channel pressure at fixed T/activity.
            # Normalize by a finite owner evaluation: the zero limit is
            # independent of that anchor; neutral pressure has ratio one.
            rates = {s: seed * (evaluators[s].evaluate(1343.15, source_activity=0.1, pO2_bar=delta).pressure_pa / anchor[s]) for s, seed in seeds.items()}
            gas = sim._project_evaporation_overhead_source_mol_hr(rates, {}).get('O2', 0.0) / area_dt
            j = oxygen_transport(0.0, delta, temperature_K=1343.15, area_m2=0.41006003303200633).flux_kg_s_m2 / 0.031998
            sources = {s: sim._project_evaporation_overhead_source_mol_hr({s: rate}, {}).get('O2', 0.0) / area_dt for s, rate in rates.items()}
            return _SurfaceOxygenEvaluation(delta, 0.0, j, 0.0, sources, 0.0, 0.0, 0.0, gas, payload=rates)
        solved = _solve_surface_oxygen_balance(point, bracket_log10_pO2_bar=(-12.0, 0.0))
        assert solved.reaction_pO2_bar == solved.residual == 0.0
        assert solved.payload == {'CrO2': 0.0, 'CrO3': 0.0, 'SiO2_gas': seeds['SiO2_gas']}
    finally:
        close_test_owned_magemin_backend(sim)


def test_precomputed_zero_uses_the_same_overhead_residual():
    # A nonzero overhead forcing cannot be hidden by zero audit terms.
    forced = _SurfaceOxygenEvaluation(0.0, 0.0, 0.0, 0.0, {}, 0.0, 0.0, 0.0, -1.0)
    with pytest.raises(ValueError):
        _solve_surface_oxygen_balance(lambda _: pytest.fail('precomputed point re-evaluated'),
                                     source_free_zero=forced)
    # Internal coproduct retention cancels even when its audit terms are large.
    retained = _SurfaceOxygenEvaluation(0.0, 0.0, 0.0, 0.0,
        {'internal_coproduct': 0.1}, 0.0, 0.0, 0.1, 0.0)
    assert _solve_surface_oxygen_balance(lambda _: pytest.fail('precomputed point re-evaluated'),
                                         source_free_zero=retained) is retained

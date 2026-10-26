"""Hot-hour receipts for retained oxygen and deferred pressure trials."""
import hashlib
import json

import pytest

from tests.test_c7_closure_routing import simulation
from tests.test_c7_oxygen_pins import hex_tree
from tests.magemin_test_support import close_test_owned_magemin_backend

pytestmark = [pytest.mark.serial, pytest.mark.xdist_group('serial')]


@pytest.mark.parametrize('frontier', (
    'snapshot', 'ledger', 'history', 'cost', 'notice', 'observer', 'liquidus', 'sweep',
))
def test_pressure_evaluation_has_no_write_frontier(monkeypatch, frontier):
    from simulator.accounting.ledger import AtomLedger
    from simulator.condensation import CondensationModel
    from simulator.core import PyrolysisSimulator
    from simulator.chemistry import langmuir_knudsen

    sim = simulation()
    targets = {
        'snapshot': (PyrolysisSimulator, '_snapshot_terminal_refusal_hour_state'),
        'ledger': (AtomLedger, 'apply'),
        'history': (CondensationModel, 'configure_operating_conditions'),
        'cost': (PyrolysisSimulator, '_observe_cost_transition_best_effort'),
        'notice': (PyrolysisSimulator, '_record_degraded_path_engagement'),
        'observer': (PyrolysisSimulator, '_commit_proposal'),
        'liquidus': (PyrolysisSimulator, '_melt_redox_liquidus_gate_record'),
        'sweep': (PyrolysisSimulator, '_continuous_sweep_plan'),
    }
    solve = langmuir_knudsen._solve_surface_oxygen_balance

    def checked_solve(evaluate, **kwargs):
        def guarded(call, excess, **options):
            with monkeypatch.context() as guard:
                owner, name = targets[frontier]
                guard.setattr(owner, name, lambda *a, **k: pytest.fail(
                    f'pressure evaluation crossed {frontier} write frontier'))
                return call(excess, **options)
        endpoint = kwargs.get('settle_inward_endpoint')
        if endpoint is not None:
            kwargs['settle_inward_endpoint'] = lambda excess: guarded(endpoint, excess)
        return solve(lambda excess: guarded(evaluate, excess), **kwargs)

    monkeypatch.setattr(langmuir_knudsen, '_solve_surface_oxygen_balance', checked_solve)
    try:
        sim.melt.temperature_C = 1600.0
        sim._reset_redox_source_diagnostics_for_hour()
        sim._establish_melt_redox_gate_authority_for_current_hour()
        sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()
        sim._solve_evaporative_oxygen_boundary()
    finally:
        close_test_owned_magemin_backend(sim)


def test_pressure_evaluation_order_and_failure_leave_no_state(monkeypatch):
    from copy import deepcopy
    from simulator.chemistry import langmuir_knudsen

    sim = simulation()
    solve = langmuir_knudsen._solve_surface_oxygen_balance

    def receipt():
        return deepcopy((sim.atom_ledger.mol_by_account(), sim.atom_ledger.transitions,
                         sim.cost_ledger.__dict__, sim.melt, sim.train,
                         sim.condensation_model.operating_history,
                         sim.condensation_model.cold_spot_history,
                         getattr(sim, '_degraded_path_engagement', {})))

    def checked_solve(evaluate, **kwargs):
        before = receipt()
        first = evaluate(1e-7)
        evaluate(1e-5)
        with pytest.raises((ValueError, RuntimeError)):
            evaluate(float('nan'))
        repeated = evaluate(1e-7)
        assert first.balance_terms == repeated.balance_terms
        assert first.payload[1] == repeated.payload[1]
        assert receipt() == before
        return solve(evaluate, **kwargs)

    monkeypatch.setattr(langmuir_knudsen, '_solve_surface_oxygen_balance', checked_solve)
    try:
        sim.melt.temperature_C = 1600.0
        sim._reset_redox_source_diagnostics_for_hour()
        sim._establish_melt_redox_gate_authority_for_current_hour()
        sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()
        sim._solve_evaporative_oxygen_boundary()
    finally:
        close_test_owned_magemin_backend(sim)


def test_nonzero_cro2_condensation_returns_gas_oxygen():
    from simulator.state import EvaporationFlux

    sim = simulation('s_type_asteroid_silicate')
    try:
        sim.melt.temperature_C = 1600.0
        sim._reset_redox_source_diagnostics_for_hour()
        sim._establish_melt_redox_gate_authority_for_current_hour()
        sim.atom_ledger.load_external_mol('process.overhead_gas', {'O2': 1.0},
            material_origin='feedstock', source='CrO2 condensation pin')
        sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()
        sim._reaction_pO2_bar = 0.013
        sim._frozen_evaporation_carriers = frozenset({'CrO2'})
        flux = EvaporationFlux(species_kg_hr={'CrO2': 0.001})
        flux.update_totals()
        start = len(sim.atom_ledger.transitions)
        sim._apply_evaporation_carrier_batch(flux)
        returns = [sim._transition_species_mol(transition, side='credits',
                    account='process.overhead_gas', species='O2')
                   for transition in sim.atom_ledger.transitions[start:]
                   if not transition.name.startswith('evaporate_')]
        assert returns and sum(returns) > 0.0
        assert [value.hex() for value in returns] == ['0x1.860dbad355075p-9']
    finally:
        close_test_owned_magemin_backend(sim)


def test_rh03_hour9_seed_and_solved_sio_operands(monkeypatch):
    from simulator.core import PyrolysisSimulator
    from simulator.run_executor import RunExecutor
    from tests.test_condensation_extrapolation_honesty import _rh03_run

    calculate = PyrolysisSimulator._calculate_evaporation
    solve = PyrolysisSimulator._solve_evaporative_oxygen_boundary
    seeds = []
    accepted = []

    def observe_source(owner, equilibrium, **kwargs):
        if owner.melt.hour == 8 and getattr(owner, '_frozen_evaporation_carriers', None) is None:
            seeds.append({
                'sio2_kg': owner.atom_ledger.kg_by_account('process.cleaned_melt')['SiO2'],
                'intrinsic_bar': owner._last_vapor_pressure_diagnostic['source_reaction_fO2_bar'],
                'source_pa': equilibrium.vapor_pressures_Pa['SiO'],
                'bulk_pa': owner._evaporation_bulk_partials_pa()['SiO'],
            })
        return calculate(owner, equilibrium, **kwargs)

    def observe_solve(owner, *args, **kwargs):
        result = solve(owner, *args, **kwargs)
        if owner.melt.hour == 8:
            accepted.append((result.reaction_pO2_bar, result.payload[2]._frozen_evaporation_carriers))
        return result

    monkeypatch.setattr(PyrolysisSimulator, '_calculate_evaporation', observe_source)
    monkeypatch.setattr(PyrolysisSimulator, '_solve_evaporative_oxygen_boundary', observe_solve)
    execution = RunExecutor().execute(_rh03_run(9)._session_config())
    assert execution.status == 'ok', execution.error_message
    assert hex_tree(seeds[0]) == hex_tree({
        'sio2_kg': 453.4187361911669,
        'intrinsic_bar': 0.013,
        'source_pa': 51.367338836827116,
        'bulk_pa': 786.6334028886312,
    })
    assert accepted and all(oxygen == 0.0 and 'SiO' not in carriers
                            for oxygen, carriers in accepted)


def test_trial_ledger_still_refuses_unbalanced_transitions():
    from simulator.accounting.exceptions import UnbalancedTransitionError
    from simulator.accounting.ledger import snapshot_atom_ledger
    from tests.accounting.test_ledger_api import _api

    original = _api().ledger
    trial = snapshot_atom_ledger(original, _pressure_trial=True)
    before = trial.mol_by_account()
    with pytest.raises(UnbalancedTransitionError):
        trial.move('invalid trial', 'process.cleaned_melt', 'process.overhead_gas',
                   {'SiO2': 0.1}, credit_species_kg={'SiO2': 0.2})
    assert trial.mol_by_account() == original.mol_by_account() == before


def test_pressure_trials_keep_checks_without_materializing_diagnostics(monkeypatch):
    from simulator.accounting.ledger import LedgerTransition
    from simulator.vapour_rail import catalog

    sim = simulation()
    try:
        sim.melt.temperature_C = 1600.0
        sim._reset_redox_source_diagnostics_for_hour()
        sim._establish_melt_redox_gate_authority_for_current_hour()
        sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()
        journal_count = len(sim.atom_ledger._gross_account_flow_events)
        compile_calls = []
        conservation_calls = []
        compile_catalog = catalog.compiled_catalog_for
        validate = LedgerTransition.validate_conservation

        def compile_once(*args, **kwargs):
            compile_calls.append(1)
            return compile_catalog(*args, **kwargs)

        def check(transition, *args, **kwargs):
            conservation_calls.append(1)
            return validate(transition, *args, **kwargs)

        from simulator.core import PyrolysisSimulator
        gate_record = PyrolysisSimulator._melt_redox_liquidus_gate_record
        sweep_plan = PyrolysisSimulator._continuous_sweep_plan

        def parent_gate_only(owner, *args, **kwargs):
            assert not getattr(owner, '_pressure_trial', False), 'trial re-entered liquidus finder'
            return gate_record(owner, *args, **kwargs)

        def parent_sweep_only(owner, *args, **kwargs):
            assert not getattr(owner, '_pressure_trial', False), 'trial re-entered sweep balance'
            return sweep_plan(owner, *args, **kwargs)

        monkeypatch.setattr(PyrolysisSimulator, '_melt_redox_liquidus_gate_record', parent_gate_only)
        monkeypatch.setattr(PyrolysisSimulator, '_continuous_sweep_plan', parent_sweep_only)
        monkeypatch.setattr(catalog, 'compiled_catalog_for', compile_once)
        monkeypatch.setattr(LedgerTransition, 'validate_conservation', check)
        monkeypatch.setattr('simulator.diagnostics.wall_sticking_alpha_provenance_notice',
                            lambda *a, **k: pytest.fail('trial materialized authority diagnostics'))
        solved = sim._solve_evaporative_oxygen_boundary()
        trial = solved.payload[2]
        assert len(compile_calls) == 1
        assert conservation_calls
        assert len(trial.atom_ledger._gross_account_flow_events) == journal_count
        assert len(sim.atom_ledger._gross_account_flow_events) == journal_count
        assert trial.atom_ledger._gross_account_flow_events is not sim.atom_ledger._gross_account_flow_events
        assert trial.atom_ledger._gross_account_flow_events[0] is sim.atom_ledger._gross_account_flow_events[0]
        assert solved.source_terms_mol_m2_s
    finally:
        close_test_owned_magemin_backend(sim)


def test_accepted_hot_hour_hex():
    sim = simulation()
    try:
        sim.melt.temperature_C = 1600.0
        sim.campaign_mgr.overrides['C2A'] = {
            'ramp_rate': 0.0, 'min_hold_hr': 24.0, 'max_hours': 24.0,
        }
        snapshot = sim.step()
        receipt = hex_tree({
            'flux': snapshot.evap_flux,
            'oxygen': sim._last_surface_oxygen_diagnostic,
            'ledger': sim.atom_ledger.mol_by_account(),
            'condensation': sim.condensation_model.last_condensation_authority_by_species,
        })
        # Digest the complete float.hex trees, including exact-key authority
        # and zero-rate records, rather than storing another 930 kB fixture.
        assert {key: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
                for key, value in receipt.items()} == {
            'flux': '983829bc3acc9417f7a01919c909e57c9d43b96175507991c278f21ec19ca4f0',
            'oxygen': 'c499e99c3c55d8f5b80b81ef43a3d4c66a406a30f6af88ed5da581531a9ac621',
            'ledger': '2f921a5f0d0de652266991e06452f535ee310dee71c9aad6bd50700a37cec38f',
            'condensation': '818048509486826afc3e358592ab5220b196ff0da455d7ecefb80dd702f24673',
        }
    finally:
        close_test_owned_magemin_backend(sim)


def test_pressure_trial_freezes_liquidus_and_sweep_boundary(monkeypatch):
    sim = simulation()
    try:
        sim.melt.temperature_C = 1600.0
        sim._reset_redox_source_diagnostics_for_hour()
        authority = sim._establish_melt_redox_gate_authority_for_current_hour()
        sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()
        bulk = sim._vapor_pressure_transport_pO2_bar()
        fraction = sim._melt_redox_liquid_fraction_factor(1873.15, gate_authority=authority)
        trial = sim._detached_evaporation_projection()
        # Redox changes during a batch must not invalidate the hour's frozen
        # phase measurement or trigger another physical sweep ODE.
        trial.melt.oxygen_reservoir.melt_intrinsic_fO2_log += 2.0
        trial._melt_redox_gate_authority_tick_hour = None
        trial.melt.sweep_mol_per_s = 1.0
        monkeypatch.setattr(trial, '_melt_redox_liquidus_gate_record',
                            lambda **kw: pytest.fail('pressure trial repeated liquidus lookup'))
        monkeypatch.setattr(trial, '_sweep_boundary_pO2_bar',
                            lambda: pytest.fail('pressure trial repeated sweep balance'))
        assert trial._melt_redox_liquid_fraction_factor(1873.15) == fraction
        assert trial._headspace_transport_pO2_bar_from_ledger(0.0, head_o2_mol=0.0) == bulk
    finally:
        close_test_owned_magemin_backend(sim)


def test_pressure_trial_and_hour_rollback_defer_cold_spot_history(monkeypatch):
    from types import MappingProxyType
    import simulator.core as core

    sim = simulation()
    try:
        row = MappingProxyType({'cold_spot_pin': {'temperature_C': 25.0}})
        sim.condensation_model.cold_spot_history.append(row)
        copy_proxy = core._deepcopy_refusal_mappingproxy

        def copy_frontier_only(value, memo):
            assert 'cold_spot_pin' not in value, 'snapshot recursively copied committed cold-spot history'
            return copy_proxy(value, memo)

        monkeypatch.setattr(core, '_deepcopy_refusal_mappingproxy', copy_frontier_only)
        trial = sim._detached_evaporation_projection()
        assert trial.condensation_model.cold_spot_history is not sim.condensation_model.cold_spot_history
        assert trial.condensation_model.cold_spot_history[0] is row
        trial.condensation_model.cold_spot_history.append({'temperature_C': 99.0})
        assert len(sim.condensation_model.cold_spot_history) == 1
        state, _, _ = sim._snapshot_terminal_refusal_hour_state(defer_committed_history=True)
        history = state['_condensation_model'].cold_spot_history
        assert isinstance(history, core._RefusalSnapshotHistoryPrefix)
        monkeypatch.setattr(core, '_deepcopy_refusal_mappingproxy', copy_proxy)
        core._materialize_refusal_snapshot_history(state)
        assert state['_condensation_model'].cold_spot_history == [row]
        assert state['_condensation_model'].cold_spot_history[0] is not row
        history.release()
    finally:
        close_test_owned_magemin_backend(sim)

"""Accepted hot-hour receipts captured at b6d038243 before trial refactoring."""
import hashlib
import json

import pytest

from tests.test_c7_closure_routing import simulation
from tests.test_c7_oxygen_pins import hex_tree
from tests.magemin_test_support import close_test_owned_magemin_backend

pytestmark = [pytest.mark.serial, pytest.mark.xdist_group('serial')]


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
            'flux': 'bcd5d7fba1fd528578f866313c6160ab9bcb979c9694b6ca560fcef1580f5171',
            'oxygen': '800d16a2841c263be5f3b91476d70da2f36aa9a3c3077bc6601da697e91b27ce',
            'ledger': '4e3a0b5184683392bb660b5a6383dc24b6fd8c80d06595e62bfb19612d245262',
            'condensation': 'c448718e84ef4f561393d6c998e0c47dda6779efca7daedb93de2116fd20b36d',
        }
    finally:
        close_test_owned_magemin_backend(sim)

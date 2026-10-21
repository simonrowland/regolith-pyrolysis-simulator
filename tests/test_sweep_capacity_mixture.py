"""The capacity owner and sole bleed writer consume the same staged mixture."""

import pytest

from simulator.overhead import continuous_sweep_balance
from tests.test_mars_process_pressure import mars_sim


@pytest.mark.parametrize('sweep', [False, True])
def test_bleed_capacity_uses_full_staged_mixture(monkeypatch, sweep):
    sim = mars_sim(sweep=0.0)
    sim.atom_ledger.load_external_mol(
        'process.overhead_gas', {'Na': 0.01},
        source='trace evolved vapour', material_origin='feedstock',
    )
    plan = None
    if sweep:
        plan = continuous_sweep_balance(
            {'Na': 0.01}, {}, carrier='Ar', sweep_mol_per_s=1.0 / 3600.0,
            dt_s=3600.0, pressure_bar=sim.melt.p_total_mbar / 1000.0,
            volume_m3=sim._headspace_volume_m3(), temperature_K=sim._headspace_temperature_K(),
        )
        sim.atom_ledger.load_external_mol(
            'reservoir.reagent.sweep_gas', plan['inlet_species_mol'],
            source='continuous carrier delivery', material_origin='reagent',
        )
    expected = sim._overhead_holdup_species_kg()
    for species, mass in sim.atom_ledger.project_account_kg('reservoir.reagent.sweep_gas').items():
        expected[species] = expected.get(species, 0.0) + mass
    observed = []
    conductance = sim._headspace_bleed_conductance_kg_s

    def capture(**kwargs):
        observed.append(dict(kwargs['species_kg_for_M_avg']))
        return conductance(**kwargs)

    monkeypatch.setattr(sim, '_headspace_bleed_conductance_kg_s', capture)
    result = sim._dispatch_overhead_bleed(
        sweep_outflow_species_mol=plan['outflow_species_mol'] if plan else None,
        sweep_external_o2_outflow_mol=plan['external_o2_outflow_mol'] if plan else None,
    )
    assert result.status == 'ok'
    assert observed == [expected]
    assert ('Ar' in observed[0]) is sweep

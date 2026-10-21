"""Pin the mixture passed to the existing line-capacity owner before d-215."""

from simulator.overhead import continuous_sweep_balance
from tests.test_mars_process_pressure import mars_sim


def test_sweep_capacity_mixture_input_pin(monkeypatch):
    sim = mars_sim(sweep=0.0)
    sim.atom_ledger.load_external_mol(
        'process.overhead_gas', {'Na': 0.01},
        source='trace evolved vapour', material_origin='feedstock',
    )
    plan = continuous_sweep_balance(
        {'Na': 0.01}, {}, carrier='Ar', sweep_mol_per_s=1.0 / 3600.0,
        dt_s=3600.0, pressure_bar=sim.melt.p_total_mbar / 1000.0,
        volume_m3=sim._headspace_volume_m3(), temperature_K=sim._headspace_temperature_K(),
    )
    sim.atom_ledger.load_external_mol(
        'reservoir.reagent.sweep_gas', plan['inlet_species_mol'],
        source='continuous carrier delivery', material_origin='reagent',
    )
    before = sim._overhead_holdup_species_kg()
    observed = []
    conductance = sim._headspace_bleed_conductance_kg_s

    def capture(**kwargs):
        observed.append(dict(kwargs['species_kg_for_M_avg']))
        return conductance(**kwargs)

    monkeypatch.setattr(sim, '_headspace_bleed_conductance_kg_s', capture)
    result = sim._dispatch_overhead_bleed(
        sweep_outflow_species_mol=plan['outflow_species_mol'],
        sweep_external_o2_outflow_mol=plan['external_o2_outflow_mol'],
    )
    assert result.status == 'ok'
    # Current-input pin: the staged Ar is absent from the capacity mixture.
    assert observed == [before]
    assert 'Ar' not in observed[0]

"""C6 migration pins and the b-1012 upstream/downstream separation contract."""

import json
from pathlib import Path

import pytest

from tests.test_mars_process_pressure import mars_sim


def frozen_mars(monkeypatch, flow):
    sim = mars_sim(sweep=flow)
    sim.melt.temperature_C = 1600.0
    sim.atom_ledger.load_external_mol(
        'process.overhead_gas', {'Ar': 0.001},
        material_origin='reagent', source='frozen C6 initial carrier',
    )
    monkeypatch.setattr(sim, '_update_temperature', lambda: None)
    upstream = []
    owner = sim.overhead_model.backpressure_partials

    def observe(*args, **kwargs):
        partials = owner(*args, **kwargs)
        upstream.append(dict(partials))
        return partials

    monkeypatch.setattr(sim.overhead_model, 'backpressure_partials', observe)
    return sim, upstream


def pressure_receipt(sim, upstream):
    initial = {
        'volume_m3': sim._headspace_volume_m3(),
        'gas_atoms_mol': sim.atom_ledger.atom_moles_by_account('process.overhead_gas'),
    }
    start = len(sim.atom_ledger.transitions)
    snapshot = sim.step()
    gas_deltas = []
    for transition in sim.atom_ledger.transitions[start:]:
        debits = [lot.species_moles_for(sim.atom_ledger.registry) for lot in transition.debits
                  if str(lot.account) == 'process.overhead_gas']
        credits = [lot.species_moles_for(sim.atom_ledger.registry) for lot in transition.credits
                   if str(lot.account) == 'process.overhead_gas']
        if debits or credits:
            gas_deltas.append({'name': transition.name, 'debits': debits, 'credits': credits})
    return {
        'initial': initial,
        'final_gas_atoms_mol': sim.atom_ledger.atom_moles_by_account('process.overhead_gas'),
        'source_kg_hr': dict(snapshot.evap_flux.species_kg_hr),
        'gas_deltas_mol': gas_deltas,
        'upstream_mbar': dict(sim._melt_headspace_composition_mbar),
        'source_plane_mbar': upstream[-1],
        'postcapture_mbar': dict(sim.overhead.composition),
    }


def hex_leaves(value):
    if isinstance(value, float):
        return value.hex()
    if isinstance(value, dict):
        return {key: hex_leaves(item) for key, item in value.items()}
    if isinstance(value, list):
        return [hex_leaves(item) for item in value]
    return value


@pytest.mark.parametrize('flow', [0.01, 0.1])
def test_frozen_mars_plane_pins(monkeypatch, flow):
    sim, upstream = frozen_mars(monkeypatch, flow)
    actual = hex_leaves(pressure_receipt(sim, upstream))
    pins = json.loads((Path(__file__).parent / 'fixtures' / 'pressure_planes_pins.json').read_text())
    assert actual == pins[str(flow)]


@pytest.mark.parametrize('flow', [0.01, 0.1])
def test_b1012_postcapture_must_not_replace_upstream_pressure(monkeypatch, flow):
    sim, upstream = frozen_mars(monkeypatch, flow)
    sim.step()
    # Use the existing CSTR projection owner, not a second mixture formula.
    assert sim._melt_headspace_composition_mbar == upstream[-1]
    assert sim._melt_headspace_composition_mbar['SiO'] > sim.overhead.composition['SiO']

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
    expected = pins[str(flow)]
    # Only the consumed plane changes in C6's value step. Keep the original
    # fixture intact so every old -> new pressure leaf remains auditable.
    expected['upstream_mbar'] = expected['source_plane_mbar']
    assert actual == expected


@pytest.mark.parametrize('flow', [0.01, 0.1])
def test_b1012_postcapture_must_not_replace_upstream_pressure(monkeypatch, flow):
    from simulator.physical_constants import PA_PER_MBAR

    sim, upstream = frozen_mars(monkeypatch, flow)
    snapshot = sim.step()
    # Use the existing CSTR projection owner, not a second mixture formula.
    assert sim._melt_headspace_composition_mbar == upstream[-1]
    assert sim._melt_headspace_composition_mbar['SiO'] > sim.overhead.composition['SiO']
    assert snapshot.melt_headspace_composition_mbar == upstream[-1]
    assert sim._evaporation_bulk_partial_pressure_pa('SiO') == (
        upstream[-1]['SiO'] * PA_PER_MBAR
    )


def test_named_views_preserve_lumped_volume_and_zero_storage_stages(monkeypatch):
    from simulator.physical_constants import PA_PER_MBAR
    from simulator.state_types import StateTag

    sim, upstream = frozen_mars(monkeypatch, 0.01)
    receipt = pressure_receipt(sim, upstream)
    before = sim.atom_ledger.mol_by_account()
    volumes = sim._gas_control_volumes()
    planes = {plane.plane_id: plane for plane in sim._gas_stream_planes()}
    assert sum(volume.volume_m3 for volume in volumes) == receipt['initial']['volume_m3']
    assert len(volumes) == 1  # Duct interiors are already in the lumped volume.
    assert volumes[0].volume_id == 'headspace_and_duct'
    assert volumes[0].account_id == 'process.overhead_gas'
    assert volumes[0].temperature_K == sim._headspace_temperature_K()
    assert dict(volumes[0].species_mol) == sim._overhead_holdup_mol()
    assert sum(plane.volume_m3 for plane in planes.values()) == 0.0
    for stage in sim.train.stages:
        assert planes[f'stage:{stage.stage_number}'].partial_pressure_Pa.tag is StateTag.UNKNOWN
    for plane, field in [('upstream_headspace', 'upstream_mbar'),
                         ('postcapture_outlet', 'postcapture_mbar')]:
        assert dict(planes[plane].partial_pressure_Pa.value) == {
            species: partial * PA_PER_MBAR for species, partial in receipt[field].items()
        }
        with pytest.raises(TypeError):
            planes[plane].partial_pressure_Pa.value['SiO'] = 0.0
    with pytest.raises(TypeError):
        volumes[0].species_mol['Ar'] = 0.0
    assert sim.atom_ledger.mol_by_account() == before


@pytest.mark.parametrize('flow', [0.01, 0.1])
def test_native_fe_and_oxygen_reach_second_tick_evaporation(monkeypatch, flow):
    from simulator.chemistry.kernel import ChemistryIntent
    from simulator.physical_constants import PA_PER_MBAR

    sim, _ = frozen_mars(monkeypatch, flow)
    start = len(sim.atom_ledger.transitions)
    sim.step()
    sources = {'Ar': flow * 3600.0}
    for transition in sim.atom_ledger.transitions[start:]:
        if transition.name == 'overhead_bleed':
            continue
        for lot in transition.credits:
            if str(lot.account) == 'process.overhead_gas':
                for species, mol in lot.species_moles_for(sim.atom_ledger.registry).items():
                    sources[species] = sources.get(species, 0.0) + mol
    # Independent steady, well-mixed balance: g_i = y_i * F_out and
    # sum(y_i)=1 give p_i=P*g_i/sum(g). One-hour source moles have a
    # dimensionless ratio; P supplies mbar. No gas source gives no partial.
    expected = {species: sim.melt.p_total_mbar * mol / sum(sources.values())
                for species, mol in sources.items()}
    assert sources['Fe'] > 1.0
    assert sources['O2'] > 100.0
    assert sim._melt_headspace_composition_mbar.get('Fe', 0.0) == pytest.approx(expected['Fe'])
    assert sim._melt_headspace_composition_mbar['O2'] == pytest.approx(expected['O2'])

    consumed_fe_Pa = []
    dispatch = sim._dispatch_only

    def observe(intent, **kwargs):
        if intent is ChemistryIntent.EVAPORATION_FLUX:
            consumed_fe_Pa.append(kwargs['control_inputs']['overhead_partials_Pa']['Fe'])
        return dispatch(intent, **kwargs)

    monkeypatch.setattr(sim, '_dispatch_only', observe)
    snapshot = sim.step()
    assert snapshot.hour == 2
    assert consumed_fe_Pa
    assert consumed_fe_Pa == pytest.approx([expected['Fe'] * PA_PER_MBAR] * len(consumed_fe_Pa))

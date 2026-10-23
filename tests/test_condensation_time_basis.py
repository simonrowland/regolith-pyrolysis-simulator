"""b-1032: steady stage capture must compare mass rates on one time basis."""

import pytest
import yaml

from simulator import condensation as c
from simulator.overhead import OverheadGasModel
from simulator.state import CondensationTrain, EvaporationFlux, MeltState


def _sio_case():
    # Investigation's constructed h120-scale case, not the unrecovered h120 state.
    with open('data/setpoints.yaml') as stream:
        geometry = yaml.safe_load(stream)['condenser_geometry']
    overhead = OverheadGasModel(condenser_geometry_config=geometry)
    feed_kg_hr = 12.70
    rates = {
        'SiO': feed_kg_hr,
        # SiO2 -> SiO + 0.5 O2; mass-rate ratio uses molar masses.
        'O2': 0.5 * feed_kg_hr * c.MOLAR_MASS['O2'] / c.MOLAR_MASS['SiO'],
    }
    flux = EvaporationFlux(species_kg_hr=rates, total_kg_hr=sum(rates.values()))
    melt = MeltState(temperature_C=1750, p_total_mbar=10)
    transport = overhead.estimate_transport_state(flux, melt)
    partials = overhead.species_partial_pressures(flux, transport['vapor_pressure_mbar'])
    model = c.CondensationModel(CondensationTrain.create_default())
    model.configure_operating_conditions(
        overhead_pressure_mbar=transport['pressure_mbar'],
        species_partial_pressures_mbar=partials,
        gas_temperature_C=melt.temperature_C,
        stage_area_m2_by_stage=transport['stage_area_m2_by_stage'],
        stir_factor=6, radial_stir_factor=1,
    )
    model.wall_species_partial_pressures_pa = c._flowing_species_partial_pressures_pa(
        rates, transport['pressure_mbar'] * 100,
        reported_partial_pressures_mbar=partials,
    )
    model._enforce_knudsen_regime()
    stage = next(stage for stage in model.train.stages if stage.stage_number == 3)
    record = c._stage_alpha_record(stage, 'SiO', model.materials)
    return model, dict(
        stage=stage, species='SiO', T_cond_C=1050, residence_s=4,
        available_kg=feed_kg_hr, alpha_s_value=record['alpha_s'], alpha_record=record,
    )


def test_sio_h120_scale_numeric_pin():
    model, inputs = _sio_case()
    # Independent b-1032 investigation: 0.0667345651876 kg/hour / 12.70 kg/hour.
    assert model._condensation_efficiency(**inputs) == pytest.approx(
        0.00525469017225, rel=2e-9,
    )


@pytest.mark.parametrize('residence_s', [2.0, 4.0, 8.0])
def test_steady_stage_eta_uses_matching_time_bases(monkeypatch, residence_s):
    from simulator.accounting.formulas import resolve_species_formula

    model, inputs = _sio_case()
    # Controlled steady molar flux: 1 mol/(m² s) on 1 m², with 2 mol/s
    # inlet. Counting molecules gives eta=1/2, independent of time unit.
    molar_mass = resolve_species_formula('SiO').molar_mass_kg_per_mol()
    model.stage_area_m2_by_stage = {'3': 1.0}
    monkeypatch.setattr(c, '_series_resistance_deposition_flux_mol_m2_s',
                        lambda *args, **kwargs: 1.0)
    feed_kg_s = 2.0 * molar_mass
    inputs.update(available_kg=feed_kg_s * 3600.0, residence_s=residence_s)
    eta_hourly = model._condensation_efficiency(**inputs)
    # Same physical case as a parcel over this residence; seconds cancel.
    captured_kg = molar_mass * residence_s
    passing_kg = feed_kg_s * residence_s
    eta_seconds = captured_kg / passing_kg
    assert eta_hourly == pytest.approx(eta_seconds)
    assert eta_hourly == pytest.approx(0.5)


@pytest.mark.parametrize('species', ['SiO', 'Fe'])
@pytest.mark.parametrize('flux, expected', [(0.0, 0.0), (1.0, 0.5), (10.0, 1.0)])
def test_steady_stage_supply_limit_and_reversible_path(monkeypatch, species, flux, expected):
    from simulator.accounting.formulas import resolve_species_formula

    model, inputs = _sio_case()
    model.wall_species_partial_pressures_pa[species] = 1000.0
    molar_mass = resolve_species_formula(species).molar_mass_kg_per_mol()
    model.stage_area_m2_by_stage = {'3': 1.0}
    monkeypatch.setattr(c, '_series_resistance_deposition_flux_mol_m2_s',
                        lambda *args, **kwargs: flux)
    inputs.update(species=species, T_cond_C=1400.0 if species == 'Fe' else 1050.0,
                  available_kg=2.0 * molar_mass * 3600.0)
    assert model._condensation_efficiency(**inputs) == pytest.approx(expected)

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
    # Historical pin from b-1032 investigation, before correcting the time basis.
    assert model._condensation_efficiency(**inputs) == pytest.approx(
        5.8385446358e-6, rel=2e-9,
    )

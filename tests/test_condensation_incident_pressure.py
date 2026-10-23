"""t-1478 r2: historical pins before correcting incident-pressure roles."""

import pytest

from simulator import condensation
from simulator.state import CondensationTrain


def _stage_eta(species, pressure_pa):
    model = condensation.CondensationModel(CondensationTrain.create_default())
    model.configure_operating_conditions(
        overhead_pressure_mbar=1.0,
        species_partial_pressures_mbar={species: pressure_pa / 100.0},
        stage_area_m2_by_stage={"1": 1.0},
    )
    stage = next(stage for stage in model.train.stages if stage.stage_number == 1)
    return model._condensation_efficiency(
        stage=stage, species=species, T_cond_C=1400.0,
        residence_s=1.0, available_kg=1e-9, alpha_s_value=1.0,
    )


@pytest.mark.parametrize("species, expected_hex", [
    ("SiO2_gas", "0x1.35854501495b9p-5"),
    ("Fe", "0x1.0000000000000p+0"),
])
def test_historical_stage_ignores_incident_pressure(species, expected_hex):
    assert _stage_eta(species, 0.0).hex() == expected_hex
    assert _stage_eta(species, 1.0).hex() == expected_hex


def test_historical_generic_crystal_is_unqualified():
    from simulator.battery.enums import Authority, Phase
    from tests.test_sio2_wall_psat import _static_prediction

    notices = {}
    pressure = condensation._antoine_psat_pa("SiO2_gas", 1400, antoine_extrapolations=notices)
    prediction = _static_prediction(1400, Phase.CR)
    assert float(prediction.value) == pressure
    assert prediction.authority is Authority.CERTIFIED
    assert notices == {}
    assert prediction.notices == ()

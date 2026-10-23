"""t-1478 r2: pressure roles; historical numeric pins are in 209161fe9."""

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
    # _route populates this flow map before invoking the stage owner.
    model.wall_species_partial_pressures_pa = {species: pressure_pa}
    stage = next(stage for stage in model.train.stages if stage.stage_number == 1)
    return model._condensation_efficiency(
        stage=stage, species=species, T_cond_C=1400.0,
        residence_s=1.0, available_kg=1e-9, alpha_s_value=1.0,
    )


@pytest.mark.parametrize("species", ["SiO2_gas", "Fe"])
def test_stage_capture_is_monotonic_in_actual_incident_pressure(species):
    # Sample the production saturation owner; expected zero follows the
    # reversible-condensation law, not a duplicate pressure/flux formula.
    temperatures = [1373.15 + 300 * (i + 0.5) / condensation.HKL_BAND_SAMPLES
                    for i in range(condensation.HKL_BAND_SAMPLES)]
    saturation = [condensation._antoine_psat_pa(species, t) for t in temperatures]
    pressures = [0.0, min(saturation) / 2, min(saturation) * 1.1, max(saturation) * 10]
    efficiencies = [_stage_eta(species, p) for p in pressures]
    assert efficiencies[:2] == [0.0, 0.0]
    assert 0.0 < efficiencies[2] <= efficiencies[3] <= 1.0


def test_generic_crystal_flags_tridymite_without_changing_reference_value():
    from simulator.battery.enums import Authority, Phase
    from tests.test_sio2_wall_psat import _static_prediction

    notices = {}
    pressure = condensation._antoine_psat_pa("SiO2_gas", 1400, antoine_extrapolations=notices)
    prediction = _static_prediction(1400, Phase.CR)
    assert float(prediction.value) == pressure
    assert prediction.authority is Authority.EXTRAPOLATED
    assert "stable tridymite field: high-cristobalite reference used" in prediction.notices[0].reason
    assert "<=0.006 dex" in prediction.notices[0].reason
    assert any(n["reason"] == prediction.notices[0].reason for n in notices.values())


@pytest.mark.parametrize("temperature, flagged", [
    (1139.999, False), (1140, True), (1200, True), (1743, True), (1743.001, False),
])
def test_tridymite_field_and_explicit_cristobalite(temperature, flagged):
    from simulator.battery.enums import Authority, Phase, Polymorph
    from tests.test_sio2_wall_psat import _static_prediction

    generic = _static_prediction(temperature, Phase.CR)
    explicit = _static_prediction(temperature, Phase.CR, Polymorph.CRISTOBALITE_HIGH)
    assert generic.value == explicit.value
    assert bool(generic.notices) is flagged
    assert generic.authority is (Authority.EXTRAPOLATED if flagged else Authority.CERTIFIED)
    assert explicit.authority is Authority.CERTIFIED
    assert explicit.notices == ()

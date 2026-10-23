"""t-1478 part 2: historical SiO2 wall input/disposition pins."""

import pytest

from simulator import condensation


@pytest.mark.parametrize("temperature", [250, 328, 500, 900, 1400, 1800, 328.453])
def test_sio2_wall_pressure_before_intake(temperature):
    with pytest.raises(condensation.WallSaturationPressureRefusal) as caught:
        condensation._antoine_psat_pa("SiO2_gas", temperature)
    assert caught.value.reason == (
        "no extrapolation available: wall fit absent or outside its finite "
        "positive-denominator branch; valid_range_K=[1400.0, 2273.15]"
    )
    assert caught.value.valid_range_K == [1400.0, 2273.15]


@pytest.mark.parametrize("temperature", [250, 328, 500, 900, 1400, 1800])
def test_sio2_wall_driving_pressure_before_intake(temperature):
    diagnostic = {}
    assert condensation._wall_deposition_driving_pressure_pa(
        "SiO2_gas", 100.0, temperature, diagnostic_out=diagnostic,
    ) == 0.0
    assert diagnostic == {
        "wall_saturation_pressure_pa": None,
        "wall_saturation_pressure_refused": True,
        "wall_saturation_pressure_refusal_reason": "missing_reactivity_class",
        "wall_saturation_pressure_refusal_type": "MissingReactivityClassRefusal",
    }

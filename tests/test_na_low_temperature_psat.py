"""t-1478 production-output pins; captured at green 7dd268c6f."""

import pytest

from simulator import condensation


@pytest.mark.parametrize("species, temperature, pressure_hex, refusal, authority", [
    ('Na', 250, None, 'wall_saturation_pressure_refused: species=Na temperature_K=250.000 reason=no extrapolation available: wall fit absent or outside its finite positive-denominator branch; valid_range_K=[924, 1118]', 'unavailable'),
    ('Na', 300, None, 'wall_saturation_pressure_refused: species=Na temperature_K=300.000 reason=no extrapolation available: wall fit absent or outside its finite positive-denominator branch; valid_range_K=[924, 1118]', 'unavailable'),
    ('Na', 328, None, 'wall_saturation_pressure_refused: species=Na temperature_K=328.000 reason=no extrapolation available: wall fit absent or outside its finite positive-denominator branch; valid_range_K=[924, 1118]', 'unavailable'),
    ('Na', 371, None, 'wall_saturation_pressure_refused: species=Na temperature_K=371.000 reason=no extrapolation available: wall fit absent or outside its finite positive-denominator branch; valid_range_K=[924, 1118]', 'unavailable'),
    ('Na', 500, '0x1.4757259d99799p-50', None, 'extrapolated'),
    ('Na', 700, '0x1.c9c95010cc073p+2', None, 'extrapolated'),
    ('Na', 900, '0x1.e259ea2620988p+11', None, 'extrapolated'),
    ('K', 250, '0x1.7dc9b633cd045p-26', None, 'extrapolated'),
    ('K', 300, '0x1.44addc4e0b66ap-17', None, 'extrapolated'),
    ('K', 328, '0x1.1ef86e44e9eaap-13', None, 'extrapolated'),
    ('K', 371, '0x1.f8d0d13b98306p-9', None, 'extrapolated'),
    ('K', 500, '0x1.9b74cda9c4db1p+1', None, 'extrapolated'),
    ('K', 700, '0x1.dc398b061300dp+9', None, None),
    ('K', 900, '0x1.777ea4a15c5ebp+14', None, None),
])
def test_wall_pressure_pin(species, temperature, pressure_hex, refusal, authority):
    diagnostic = {}
    condensation._wall_deposition_driving_pressure_pa(
        species, 100.0, temperature, diagnostic_out=diagnostic,
    )
    pressure = diagnostic["wall_saturation_pressure_pa"]
    assert (pressure.hex() if pressure is not None else None) == pressure_hex
    assert diagnostic.get("wall_saturation_pressure_refusal_reason") == refusal
    assert diagnostic.get("wall_saturation_pressure_notice", {}).get("authority_level") == authority


def test_t1464_warm_bin_na_missing_input():
    # t-1464 r3 withholds the source debit on this exact typed refusal.
    # Its warm bin is 55.303 C (328.453 K); there is no numeric pump flow
    # or capture fraction when the pressure is unavailable.
    with pytest.raises(condensation.WallSaturationPressureRefusal) as refused:
        condensation._antoine_psat_pa("Na", 328.453)
    assert "outside its finite positive-denominator branch" in str(refused.value)

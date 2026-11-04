"""Classical nucleation rate. Expected comparisons are the published rows.

Water at 293.15 K crosses 1 cm^-3 s^-1 near S = 3.3. Sodium at 523 K
crosses it near S = 28. Those bands are the comparison. The test does
not paste the Becker-Doring expression. Fink coefficients are the
published correlation, checked against the data file the owner reads.
"""

from __future__ import annotations

import math

import pytest

from simulator.nucleation import (
    FOG_RATE_THRESHOLD_M3_S,
    NOTICE_FOG,
    NOTICE_UNEVALUABLE,
    NucleationInputError,
    NucleationRate,
    fog_capture_code,
    nucleation_liquid_data,
    nucleation_rate,
)
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET

_P_NA_PA = 5.89
_WATER_K = 293.15


def _ratio_at_threshold(species: str, temperature_K: float) -> float:
    """S where the production rate crosses the fog threshold, from above."""

    probe = nucleation_rate(species, temperature_K, 1.0)
    assert probe.status == "evaluated"
    saturation = probe.saturation_pressure_Pa
    assert saturation is not None and saturation > 0.0

    def rate_at(ratio: float) -> float:
        result = nucleation_rate(species, temperature_K, ratio * saturation)
        assert result.status == "evaluated"
        assert result.rate_m3_s is not None
        return result.rate_m3_s

    low = 1.0000001
    high = 1000.0
    assert rate_at(low) < FOG_RATE_THRESHOLD_M3_S
    assert rate_at(high) > FOG_RATE_THRESHOLD_M3_S
    for _step in range(60):
        mid = math.sqrt(low * high)
        if rate_at(mid) > FOG_RATE_THRESHOLD_M3_S:
            high = mid
        else:
            low = mid
    return math.sqrt(low * high)


def test_fink_coefficients_are_the_published_correlation() -> None:
    row = nucleation_liquid_data()["species"]["Na"]
    assert row["reference_temperature_K"] == 2503.7
    assert row["melting_temperature_K"] == 370.98
    assert row["saturation_owner"] == "browning_potter"
    assert row["density"] == {
        "a_kg_m3": 219.0,
        "b_kg_m3": 275.32,
        "c_kg_m3": 511.58,
        "tau_exponent": 0.5,
    }
    assert row["surface_tension"]["A_mN_per_m"] == 240.5
    assert row["surface_tension"]["exponent"] == 1.126


def test_water_293k_critical_supersaturation_is_about_3_3() -> None:
    ratio = _ratio_at_threshold("H2O", _WATER_K)
    assert ratio == pytest.approx(3.3, abs=0.15)
    result = nucleation_rate("H2O", _WATER_K, 1.0)
    assert result.saturation_authority == "cited_point"
    assert result.saturation_pressure_Pa == pytest.approx(2338.8)


def test_water_off_the_sourced_temperature_is_unevaluable() -> None:
    result = nucleation_rate("H2O", 300.0, 2338.8)
    assert result.status == "unevaluable"
    assert result.rate_m3_s is None
    assert fog_capture_code(result) == NOTICE_UNEVALUABLE


def test_sodium_523k_critical_supersaturation_is_about_28() -> None:
    ratio = _ratio_at_threshold("Na", 523.0)
    assert ratio == pytest.approx(28.0, abs=1.5)


def test_sodium_lunar_partial_fog_boundary() -> None:
    cold = nucleation_rate("Na", 514.0, _P_NA_PA)
    crossing = nucleation_rate("Na", 515.0, _P_NA_PA)
    hot = nucleation_rate("Na", 523.0, _P_NA_PA)
    bulk_250c = nucleation_rate("Na", 250.0 + CELSIUS_TO_KELVIN_OFFSET, _P_NA_PA)

    assert cold.status == "evaluated"
    assert cold.rate_m3_s is not None and cold.rate_m3_s > FOG_RATE_THRESHOLD_M3_S
    assert fog_capture_code(cold) == NOTICE_FOG

    assert crossing.rate_m3_s is not None
    assert crossing.rate_m3_s <= FOG_RATE_THRESHOLD_M3_S
    assert crossing.rate_m3_s > 1.0e5
    assert fog_capture_code(crossing) is None

    assert hot.rate_m3_s is not None and hot.rate_m3_s <= FOG_RATE_THRESHOLD_M3_S
    assert fog_capture_code(hot) is None

    assert bulk_250c.rate_m3_s == pytest.approx(187.0, rel=0.05)
    assert fog_capture_code(bulk_250c) is None


def test_sodium_250c_handbook_lower_saturation_pressure_crosses() -> None:
    """A 25% lower P_sat at 250 C and 5.89 Pa crosses 1e6. The nominal rate does not.

    The 1.25e6 comparison is the t1605 review's evaluation of this owner
    at scale 0.75, not a second copy of the Becker-Doring expression.
    """

    temperature_K = 250.0 + CELSIUS_TO_KELVIN_OFFSET
    nominal = nucleation_rate("Na", temperature_K, _P_NA_PA)
    same = nucleation_rate("Na", temperature_K, _P_NA_PA, saturation_pressure_scale=1.0)
    lowered = nucleation_rate(
        "Na",
        temperature_K,
        _P_NA_PA,
        saturation_pressure_scale=0.75,
    )

    assert same.rate_m3_s == nominal.rate_m3_s
    assert same.saturation_pressure_Pa == nominal.saturation_pressure_Pa
    assert nominal.saturation_pressure_Pa is not None
    assert lowered.saturation_pressure_Pa == pytest.approx(nominal.saturation_pressure_Pa * 0.75)
    assert lowered.rate_m3_s == pytest.approx(1.25e6, rel=0.05)
    assert fog_capture_code(nominal) is None
    assert fog_capture_code(lowered) == NOTICE_FOG

    with pytest.raises(NucleationInputError):
        nucleation_rate("Na", temperature_K, _P_NA_PA, saturation_pressure_scale=0.0)
    with pytest.raises(NucleationInputError):
        nucleation_rate("Na", temperature_K, _P_NA_PA, saturation_pressure_scale=float("nan"))


def test_threshold_is_strictly_greater_than_1e6() -> None:
    evaluated = NucleationRate(
        status="evaluated",
        species="Na",
        temperature_K=515.0,
        partial_pressure_Pa=_P_NA_PA,
        rate_m3_s=FOG_RATE_THRESHOLD_M3_S,
        saturation_pressure_Pa=1.0,
        saturation_ratio=1.0,
        saturation_authority="extrapolated",
        critical_cluster_molecules=30.0,
        reason=None,
        property_notice=None,
        saturation_notice=None,
    )
    assert fog_capture_code(evaluated) is None
    just_over = NucleationRate(
        status=evaluated.status,
        species=evaluated.species,
        temperature_K=evaluated.temperature_K,
        partial_pressure_Pa=evaluated.partial_pressure_Pa,
        rate_m3_s=math.nextafter(FOG_RATE_THRESHOLD_M3_S, math.inf),
        saturation_pressure_Pa=evaluated.saturation_pressure_Pa,
        saturation_ratio=evaluated.saturation_ratio,
        saturation_authority=evaluated.saturation_authority,
        critical_cluster_molecules=evaluated.critical_cluster_molecules,
        reason=None,
        property_notice=None,
        saturation_notice=None,
    )
    assert fog_capture_code(just_over) == NOTICE_FOG


def test_potassium_has_no_sourced_liquid_properties() -> None:
    result = nucleation_rate("K", 313.15, _P_NA_PA)
    assert result.status == "unevaluable"
    assert result.rate_m3_s is None
    assert result.reason
    assert fog_capture_code(result) == NOTICE_UNEVALUABLE


def test_undersaturated_and_empty_vapour_are_zero() -> None:
    undersaturated = nucleation_rate("Na", 523.15, 1.0e-6)
    empty = nucleation_rate("Na", 523.15, 0.0)
    assert undersaturated.status == "evaluated"
    assert undersaturated.rate_m3_s == 0.0
    assert undersaturated.saturation_ratio is not None
    assert undersaturated.saturation_ratio < 1.0
    assert empty.status == "evaluated"
    assert empty.rate_m3_s == 0.0
    assert fog_capture_code(empty) is None


def test_sodium_below_melting_point_is_evaluated_and_flagged() -> None:
    result = nucleation_rate("Na", 313.15, _P_NA_PA)
    assert result.status == "evaluated"
    assert result.rate_m3_s is not None and result.rate_m3_s > FOG_RATE_THRESHOLD_M3_S
    assert result.property_notice
    assert result.critical_cluster_molecules is not None
    assert result.critical_cluster_molecules < 10.0
    assert result.saturation_authority == "extrapolated"


def test_invalid_temperature_or_pressure_raises() -> None:
    with pytest.raises(NucleationInputError):
        nucleation_rate("Na", -1.0, 1.0)
    with pytest.raises(NucleationInputError):
        nucleation_rate("Na", float("nan"), 1.0)
    with pytest.raises(NucleationInputError):
        nucleation_rate("Na", 523.0, -1.0)
    with pytest.raises(NucleationInputError):
        nucleation_rate("", 523.0, 1.0)


def test_sodium_above_the_fink_reference_temperature_is_unevaluable() -> None:
    result = nucleation_rate("Na", 2503.7, 1.0e6)
    assert result.status == "unevaluable"
    assert result.rate_m3_s is None

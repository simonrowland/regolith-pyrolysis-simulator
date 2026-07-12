import math

import pytest

from simulator.material_densities import (
    _fe_si_fractional_contraction,
    _ideal_alloy_density_kg_m3,
    alloy_density_kg_m3,
    alloy_density_uncertainty_relative_fraction,
    buoyancy_verdict,
    liquid_metal_density_kg_m3,
    liquid_metal_density_provenance,
    resolve_melt_density_kg_m3,
)


@pytest.mark.parametrize(
    ("species", "temperature_K", "expected_kg_m3"),
    [
        ("Al", 933.473, 2377.23),
        ("Si", 1687.0, 2550.0),
        ("Ti", 1941.0, 4222.1),
        ("Cr", 2180.0, 6097.1),
        ("Mn", 1519.0, 5757.33),
        ("Fe", 1810.94, 7034.96),
        ("Ni", 1728.3, 7861.0),
    ],
)
def test_liquid_metal_correlations_match_cited_reference_points(
    species, temperature_K, expected_kg_m3
):
    assert liquid_metal_density_kg_m3(species, temperature_K) == pytest.approx(
        expected_kg_m3, rel=0.0, abs=1e-10
    )


def test_ideal_alloy_density_mixes_molar_volumes_not_densities():
    T = 1820.0
    composition = {"Fe": 3.0, "Si": 1.0}
    rho_fe = liquid_metal_density_kg_m3("Fe", T)
    rho_si = liquid_metal_density_kg_m3("Si", T)
    expected = (0.75 * 55.845 + 0.25 * 28.085) / (
        0.75 * 55.845 / rho_fe + 0.25 * 28.085 / rho_si
    )
    assert _ideal_alloy_density_kg_m3(composition, T) == pytest.approx(expected)


def test_fe_25atpct_si_excess_volume_matches_measured_density_anchor():
    assert _fe_si_fractional_contraction(0.25) == pytest.approx(
        0.016577241181315427
    )
    assert alloy_density_kg_m3({"Fe": 3.0, "Si": 1.0}, 1820.0) == pytest.approx(
        5780.645, rel=1e-9
    )


def test_equiatomic_fe_si_excess_volume_matches_mizuno_anchor():
    assert _fe_si_fractional_contraction(0.5) == pytest.approx(
        0.09847612429515318
    )
    assert alloy_density_kg_m3({"Fe": 1.0, "Si": 1.0}, 1820.0) == pytest.approx(
        4952.829, rel=1e-9
    )


@pytest.mark.parametrize(
    ("x_si", "expected_kg_m3"),
    [
        (0.05, 6676.407),
        (0.10, 6470.075),
        (0.15, 6222.541),
        (0.20, 6220.700),
        (0.25, 5780.645),
        (0.34, 5573.343),
        (0.38, 5456.484),
        (0.40, 5541.545),
        (0.45, 5153.425),
        (0.50, 4952.829),
        (0.55, 4656.292),
        (0.60, 4445.923),
        (0.70, 3820.700),
        (0.75, 3450.594),
        (0.80, 3427.518),
        (0.90, 2902.980),
    ],
)
def test_fe_si_piecewise_fit_reproduces_mizuno_table1_composition_curve(
    x_si, expected_kg_m3
):
    assert alloy_density_kg_m3({"Fe": 1.0 - x_si, "Si": x_si}, 1820.0) == (
        pytest.approx(expected_kg_m3, abs=1e-3)
    )


@pytest.mark.parametrize(
    ("species", "temperature_K", "expected_kg_m3"),
    [
        ("Si", 1873.15, 2500.8564),
        ("Ti", 2300.0, 4080.2232),
        ("Cr", 2300.0, 6018.668),
    ],
)
def test_liquid_metal_correlations_match_independent_off_tm_source_points(
    species, temperature_K, expected_kg_m3
):
    assert liquid_metal_density_kg_m3(species, temperature_K) == pytest.approx(
        expected_kg_m3, rel=0.0, abs=1e-7
    )


def test_buoyancy_classification_includes_near_neutral_si_hazard():
    assert buoyancy_verdict(7000.0, 2700.0)["verdict"] == "sink"
    assert buoyancy_verdict(2300.0, 2700.0)["verdict"] == "float"
    si_density = liquid_metal_density_kg_m3("Si", 1873.15)
    si_uncertainty = alloy_density_uncertainty_relative_fraction({"Si": 1.0})
    verdict = buoyancy_verdict(
        si_density,
        2638.918,
        alloy_uncertainty_relative_fraction=si_uncertainty,
    )
    assert verdict["verdict"] == "BUOYANCY-AMBIGUOUS"
    assert math.isclose(verdict["delta_rho_kg_m3"], si_density - 2638.918)
    assert verdict["alloy_density_uncertainty_kg_m3"] == pytest.approx(
        0.022 * si_density
    )
    assert verdict["ambiguity_threshold_kg_m3"] == pytest.approx(
        math.hypot(0.05 * 2638.918, 0.022 * si_density)
    )


def test_melt_density_uses_engine_value_or_labeled_fallback():
    assert resolve_melt_density_kg_m3(2638.918) == (2638.918, "engine_liquid_eos")
    density, tier = resolve_melt_density_kg_m3(None)
    assert density == 2700.0
    assert tier == "fallback_basaltic_melt_constant_engine_density_unavailable"


def test_density_provenance_labels_out_of_range_extrapolation():
    assert liquid_metal_density_provenance("Fe", 1873.15)[
        "status"
    ] == "within_valid_range"
    al = liquid_metal_density_provenance("Al", 1873.15)
    assert al["status"] == "extrapolated_above_valid_range"
    assert al["valid_range_K"] == [933.0, 1190.0]
    assert "Assael" in al["source"]

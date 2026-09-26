"""Grounded SiO wall equilibrium checks for d-025.

The two pressure points are independent of the simulator's stage goldens:
100 Pa SiO is below the disproportionation equilibrium at 1500 C and above
it at 1100 C. The wall flux and b-282 fouling diagnostic must agree.
"""

from __future__ import annotations

import copy
import math
from pathlib import Path

import pytest
import yaml

from simulator.accounting.queries import wall_deposit_candidate_for_surface_kg
from simulator.condensation import (
    CELSIUS_TO_KELVIN_OFFSET,
    CondensationModel,
    MATERIALS_DATA,
    _series_resistance_deposition_flux_mol_m2_s,
    _wall_alpha_record,
    _wall_deposition_driving_pressure_pa,
    cold_spot_diagnostic,
)
from simulator.reference_data.janaf import load_table_document
from simulator.state import CondensationTrain, PipeSegment


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
R_J_MOL_K = 8.314462618


def _sio_source_row() -> dict:
    payload = yaml.safe_load((DATA_DIR / "vapor_pressures.yaml").read_text())
    return payload["families"]["oxide_vapors_sio_family"][
        "physical_properties"
    ]["species"]["SiO"]


def _o039_formation_gibbs_fit_kj_mol(
    temperature_K: float,
    *,
    phase: str,
) -> float:
    """Fit raw O-039 formation-Gibbs rows for the selected phase leg.

    JANAF prints ΔfG° directly in kJ/mol. The runtime Ellingham dH/dS
    segments are linear fits to these same rows, so fitting the raw rows here
    recreates dG(T) without importing the runtime segment table.
    """
    table = load_table_document(
        DATA_DIR
        / "literature"
        / "compilations"
        / "janaf"
        / "tables"
        / "O-039.yaml"
    )["table"]
    bounds = {"solid": (1100.0, 1600.0), "liquid": (1700.0, 2500.0)}
    low_K, high_K = bounds[phase]
    rows = [
        (
            float(point["temperature"]["value"]),
            float(point["formation_gibbs_energy"]["value"]),
        )
        for point in table["values"]
        if point["formation_gibbs_energy"]["value"] is not None
        and low_K <= float(point["temperature"]["value"]) <= high_K
    ]
    assert len(rows) >= 2
    n = float(len(rows))
    sum_t = sum(row[0] for row in rows)
    sum_g = sum(row[1] for row in rows)
    sum_tt = sum(row[0] * row[0] for row in rows)
    sum_tg = sum(row[0] * row[1] for row in rows)
    slope = (n * sum_tg - sum_t * sum_g) / (n * sum_tt - sum_t * sum_t)
    intercept = (sum_g - slope * sum_t) / n
    return intercept + slope * float(temperature_K)


def _sio_expected_p_eq_pa(
    temperature_K: float,
    phase: str,
    source_row: dict,
) -> float:
    reaction = source_row["reaction"]
    coefficients = source_row["pressure_models"][0][
        "reference_pressure_model"
    ]["coefficients"]
    p_std_pa = float(reaction["standard_pressure_Pa"])
    pO2_reference_bar = float(reaction["pO2_reference_bar"])
    p_ref_pa = 10.0 ** (
        float(coefficients["A"])
        - float(coefficients["B"])
        / (float(temperature_K) + float(coefficients["C"]))
    )
    k_b = (p_ref_pa / p_std_pa) * math.sqrt(pO2_reference_bar)
    dG_b_J = -R_J_MOL_K * temperature_K * math.log(k_b)
    dG_a_J = 1000.0 * _o039_formation_gibbs_fit_kj_mol(
        temperature_K,
        phase=phase,
    )
    dG_disproportionation_J = -2.0 * dG_b_J - dG_a_J
    return p_std_pa * math.exp(
        dG_disproportionation_J / (2.0 * R_J_MOL_K * temperature_K)
    )


def _sio_expected_phase_legs(
    wall_temperature_C: float,
) -> tuple[float, float | None]:
    source_row = _sio_source_row()
    temperature_K = wall_temperature_C + 273.15
    # The production bridge uses the liquid SiO2 leg below the Si melt
    # transition and above it; the solid leg remains a comparison diagnostic.
    primary_p_eq = _sio_expected_p_eq_pa(temperature_K, "liquid", source_row)
    comparison_p_eq = (
        _sio_expected_p_eq_pa(temperature_K, "solid", source_row)
        if temperature_K < 1696.0
        else None
    )
    return primary_p_eq, comparison_p_eq


def _sio_segment(
    wall_temperature_C: float,
    *,
    liner_material: str = "fe_condenser_liner",
) -> PipeSegment:
    return PipeSegment(
        name=f"sio_probe_{int(wall_temperature_C)}",
        upstream_stage="stage_2",
        downstream_stage="stage_3",
        wall_temperature_C=wall_temperature_C,
        length_m=1.0,
        inner_diameter_m=0.12,
        liner_material=liner_material,
    )


@pytest.mark.parametrize("wall_material_class", [None, "silica", "alumina"])
@pytest.mark.parametrize(
    ("wall_temperature_C", "target_primary_p_eq", "target_comparison_p_eq"),
    [
        (1100.0, 6.067326, 4.764698),
        (1253.0, 99.844511, 89.962814),
        (1500.0, 3081.130, None),
        (1745.0, 38313.434, None),
    ],
)
def test_sio_equilibrium_controls_flux_on_any_liner(
    wall_material_class: str | None,
    wall_temperature_C: float,
    target_primary_p_eq: float,
    target_comparison_p_eq: float | None,
) -> None:
    temperature_K = wall_temperature_C + CELSIUS_TO_KELVIN_OFFSET
    expected_primary_p_eq, expected_comparison_p_eq = _sio_expected_phase_legs(
        wall_temperature_C
    )
    assert expected_primary_p_eq == pytest.approx(
        target_primary_p_eq, rel=2e-6
    )
    if target_comparison_p_eq is None:
        assert expected_comparison_p_eq is None
    else:
        assert expected_comparison_p_eq == pytest.approx(
            target_comparison_p_eq, rel=2e-6
        )

    expected_drive_pa = max(0.0, 100.0 - expected_primary_p_eq)
    expect_flux = expected_drive_pa > 0.0
    driving_notice: dict[str, object] = {}
    driving_pa = _wall_deposition_driving_pressure_pa(
        "SiO",
        100.0,
        temperature_K,
        wall_material_class=wall_material_class,
        diagnostic_out=driving_notice,
    )
    # Raw-row regression coefficients retain more digits than the rounded
    # runtime dH/dS constants; use an absolute floor for the small residual
    # driving pressure rather than hiding that source-to-runtime rounding.
    assert driving_pa == pytest.approx(
        expected_drive_pa,
        rel=2e-9,
        abs=1e-5,
    )
    assert driving_notice["wall_saturation_pressure_pa"] == pytest.approx(
        expected_primary_p_eq, rel=1e-7
    )
    notice = driving_notice["wall_saturation_pressure_notice"]
    assert notice["p_eq_comparison_pa"] == (
        pytest.approx(expected_comparison_p_eq, rel=1e-7)
        if expected_comparison_p_eq is not None
        else None
    )
    source_row = _sio_source_row()
    expected_valid_range_K = source_row["pressure_models"][0]["valid_domain"][
        "temperature_K"
    ]
    assert notice["valid_range_K"] == expected_valid_range_K
    assert notice["certified_band_K"] == [1696.0, 2273.15]
    assert notice["antoine_standard_state_extrapolated"] is (
        wall_temperature_C + CELSIUS_TO_KELVIN_OFFSET
        < expected_valid_range_K[0]
        or wall_temperature_C + CELSIUS_TO_KELVIN_OFFSET
        > expected_valid_range_K[1]
    )

    flux_notice: dict[str, object] = {}
    flux = _series_resistance_deposition_flux_mol_m2_s(
        "SiO",
        100.0,
        temperature_K,
        0.04,
        pipe_diameter_m=0.12,
        regime_factor=1.0,
        T_gas_K=temperature_K,
        overhead_pressure_pa=1000.0,
        wall_material_class=wall_material_class,
        diagnostic_out=flux_notice,
    )
    assert (flux > 0.0) is expect_flux
    assert flux_notice["driving_pressure_pa"] == pytest.approx(driving_pa)
    assert flux_notice["reason"] == (
        "reactive_uptake"
        if expect_flux
        else "reactive_equilibrium_undersaturated"
    )


def test_sio_certified_band_excludes_2400_K() -> None:
    notice: dict[str, object] = {}
    _wall_deposition_driving_pressure_pa(
        "SiO",
        100.0,
        2400.0,
        diagnostic_out=notice,
    )
    certified_band = notice["wall_saturation_pressure_notice"]["certified_band_K"]
    assert certified_band == [1696.0, 2273.15]
    assert not certified_band[0] <= 2400.0 <= certified_band[1]


@pytest.mark.parametrize(
    ("wall_temperature_C", "has_violation"),
    [(1500.0, False), (1100.0, True)],
)
def test_sio_b282_fouling_diagnostic_agrees_with_flux(
    wall_temperature_C: float,
    has_violation: bool,
) -> None:
    segment = _sio_segment(wall_temperature_C)
    diagnostic = cold_spot_diagnostic(
        [segment],
        {"SiO": 1.0},
        species_partial_pressures_pa={"SiO": 100.0},
    )
    assert diagnostic["has_upstream_hot_wall_violation"] is has_violation

    alpha_record = _wall_alpha_record(
        "SiO",
        segment=segment,
        T_K=wall_temperature_C + CELSIUS_TO_KELVIN_OFFSET,
    )
    assert alpha_record["authority_level"] == "bridge"
    assert alpha_record["output_status"] == "status_bearing"
    assert alpha_record["reactive_uptake_reason"] == (
        "evaporation_alpha_proxy_not_reactive_uptake"
    )

    fused_segment = _sio_segment(
        wall_temperature_C,
        liner_material="fused_silica_baffles",
    )
    fused_alpha = _wall_alpha_record(
        "SiO",
        segment=fused_segment,
        T_K=wall_temperature_C + CELSIUS_TO_KELVIN_OFFSET,
    )
    assert fused_alpha["alpha_s"] > 0.0
    assert fused_alpha["source_class"] != (
        "fail_closed_no_direct_sticking_coefficient"
    )
    assert fused_alpha["authority_level"] == "bridge"


def test_cold_spot_uses_runtime_liner_material_class() -> None:
    materials = copy.deepcopy(MATERIALS_DATA)
    materials["liner_materials"]["fused_silica_baffles"][
        "wall_material_class"
    ] = None
    diagnostic = cold_spot_diagnostic(
        [_sio_segment(1100.0, liner_material="fused_silica_baffles")],
        {"Na": 1.0},
        species_partial_pressures_pa={"Na": 100.0},
        materials=materials,
    )
    assert diagnostic["has_upstream_hot_wall_violation"] is False


def test_sio_wall_capture_does_not_restore_the_t_cond_gate() -> None:
    """T_cond routes the candidate; p_eq(T_wall) controls capture."""

    segment = _sio_segment(1100.0)
    model = CondensationModel(CondensationTrain.create_default())
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        species_partial_pressures_mbar={"SiO": 1.0},
        pipe_diameter_m=0.12,
        gas_temperature_C=1300.0,
        campaign_name="C2A",
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in model.train.stages
        },
    )
    model.pipe_segments = [segment]
    model.lab_geometry = object()

    candidate = wall_deposit_candidate_for_surface_kg(
        model,
        species="SiO",
        rate_kg_hr=1.0,
        T_cond_C=1050.0,
        melt_temperature_C=1300.0,
        wall_temperature_C=1100.0,
        surface_area_m2=segment.surface_area_m2,
        segment=segment,
    )
    assert isinstance(candidate, float)
    assert candidate > 0.0

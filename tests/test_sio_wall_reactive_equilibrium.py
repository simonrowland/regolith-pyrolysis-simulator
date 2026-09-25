"""Grounded SiO wall equilibrium checks for d-025.

The two pressure points are independent of the simulator's stage goldens:
100 Pa SiO is below the disproportionation equilibrium at 1500 C and above
it at 1100 C. The wall flux and b-282 fouling diagnostic must agree.
"""

from __future__ import annotations

import pytest

from simulator.accounting.queries import wall_deposit_candidate_for_surface_kg
from simulator.condensation import (
    CELSIUS_TO_KELVIN_OFFSET,
    CondensationModel,
    _series_resistance_deposition_flux_mol_m2_s,
    _wall_alpha_record,
    _wall_deposition_driving_pressure_pa,
    cold_spot_diagnostic,
)
from simulator.state import CondensationTrain, PipeSegment


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
def test_sio_equilibrium_controls_flux_on_any_liner(
    wall_material_class: str | None,
) -> None:
    for wall_temperature_C, expected_drive_pa, expect_flux in (
        (1500.0, 0.0, False),
        (1100.0, pytest.approx(94.0, abs=0.2), True),
    ):
        temperature_K = wall_temperature_C + CELSIUS_TO_KELVIN_OFFSET
        driving_notice: dict[str, object] = {}
        driving_pa = _wall_deposition_driving_pressure_pa(
            "SiO",
            100.0,
            temperature_K,
            wall_material_class=wall_material_class,
            diagnostic_out=driving_notice,
        )
        assert driving_pa == expected_drive_pa

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

from __future__ import annotations

import math

import pytest

from simulator import condensation
from simulator.state import (
    CondensationStage,
    CondensationTrain,
    EvaporationFlux,
    MeltState,
    PipeSegment,
)


def test_scalar_alpha_s_range_extrapolation_is_honest_without_value_change():
    out_context: dict[str, object] = {}
    out_value = condensation.alpha_s("Fe", 1600.0, out_context)
    out_eval = out_context["alpha_s_evaluation"]

    assert out_value.hex() == float(0.02).hex()
    assert out_eval["alpha_s_form"] == "scalar"
    assert out_eval["alpha_s_extrapolated"] is True
    assert out_eval["alpha_s_valid_range_K"] == [1700.0, 1800.0]
    assert out_eval["alpha_s_temperature_range_K"] == [1700.0, 1800.0]
    assert out_eval["alpha_s_temperature_below_valid_range"] is True
    assert "Fe alpha_s scalar coefficient extrapolated" in out_eval[
        "alpha_s_extrapolation_warning"
    ]
    assert "[1700, 1800]" in out_eval["alpha_s_extrapolation_warning"]

    in_context: dict[str, object] = {}
    in_value = condensation.alpha_s("Fe", 1750.0, in_context)
    in_eval = in_context["alpha_s_evaluation"]

    assert in_value.hex() == float(0.02).hex()
    assert in_eval["alpha_s_extrapolated"] is False
    assert in_eval["alpha_s_valid_range_K"] == [1700.0, 1800.0]
    assert "alpha_s_extrapolation_warning" not in in_eval


def test_scalar_alpha_s_range_travels_through_stage_provenance():
    stage = CondensationStage(
        stage_number=1,
        label="Fe below source range",
        temp_range_C=(1326.85, 1326.85),
        target_species=["Fe"],
    )

    record = condensation._stage_alpha_record(stage, "Fe")

    assert record["alpha_s"].hex() == float(0.02).hex()
    assert record["temperature_range_K"] == [1700, 1800]
    assert record["alpha_s_valid_range_K"] == [1700.0, 1800.0]
    assert record["alpha_s_extrapolated"] is True
    assert "Fe alpha_s scalar coefficient extrapolated" in record[
        "alpha_s_extrapolation_warning"
    ]


def test_wall_antoine_applied_path_reports_extrapolation_without_value_change():
    T_wall_K = 1173.15
    local_pressure_pa = 100.0
    baseline_pressure = condensation._wall_deposition_driving_pressure_pa(
        "SiO",
        local_pressure_pa,
        T_wall_K,
        reactive_product_backstop=False,
    )
    antoine_extrapolations: dict[str, dict[str, object]] = {}
    antoine_warnings: list[str] = []

    instrumented_pressure = condensation._wall_deposition_driving_pressure_pa(
        "SiO",
        local_pressure_pa,
        T_wall_K,
        reactive_product_backstop=False,
        antoine_extrapolations=antoine_extrapolations,
        antoine_extrapolation_warnings=antoine_warnings,
    )

    assert instrumented_pressure.hex() == baseline_pressure.hex()
    assert antoine_extrapolations["SiO"]["temperature_K"] == pytest.approx(
        T_wall_K
    )
    assert tuple(antoine_extrapolations["SiO"]["valid_range_K"]) == (
        1400.0,
        2200.0,
    )
    assert any(
        "SiO metal Antoine fit extrapolated beyond valid_range_K" in warning
        and "1173.15 K" in warning
        for warning in antoine_warnings
    )

    in_range_extrapolations: dict[str, dict[str, object]] = {}
    in_range_warnings: list[str] = []
    in_range_pressure = condensation._wall_deposition_driving_pressure_pa(
        "SiO",
        local_pressure_pa,
        1500.0,
        reactive_product_backstop=False,
        antoine_extrapolations=in_range_extrapolations,
        antoine_extrapolation_warnings=in_range_warnings,
    )

    assert in_range_pressure >= 0.0
    assert in_range_extrapolations == {}
    assert in_range_warnings == []


def test_antoine_extrapolation_records_count_same_species_stage_and_wall():
    records: dict[str, dict[str, object]] = {}
    warnings: list[str] = []
    data = {"valid_range_K": (1400.0, 2200.0)}

    condensation._record_antoine_extrapolation(
        "SiO",
        1173.15,
        data,
        antoine_extrapolations=records,
        antoine_extrapolation_warnings=warnings,
    )
    condensation._record_antoine_extrapolation(
        "SiO",
        2300.0,
        data,
        antoine_extrapolations=records,
        antoine_extrapolation_warnings=warnings,
    )
    condensation._record_antoine_extrapolation(
        "SiO",
        2300.0,
        data,
        antoine_extrapolations=records,
        antoine_extrapolation_warnings=warnings,
    )
    condensation._record_antoine_extrapolation(
        "Ca",
        2300.0,
        data,
        antoine_extrapolations=records,
        antoine_extrapolation_warnings=warnings,
    )

    assert len(records) == 3
    assert "SiO" in records
    assert "Ca" in records
    assert {record["temperature_K"] for record in records.values()} == {
        1173.15,
        2300.0,
    }
    assert len(warnings) == 3


def test_wall_deposition_flux_telemetry_keeps_applied_flux_identical():
    T_wall_K = 1173.15
    kwargs = dict(
        species="SiO",
        P_local_pa=100.0,
        T_surface_K=T_wall_K,
        alpha_s=0.02,
        pipe_diameter_m=0.05,
        T_gas_K=1700.0,
        overhead_pressure_pa=100.0,
        reactive_product_backstop=False,
    )
    baseline_flux = condensation._series_resistance_deposition_flux_mol_m2_s(
        **kwargs
    )
    antoine_extrapolations: dict[str, dict[str, object]] = {}
    antoine_warnings: list[str] = []

    instrumented_flux = condensation._series_resistance_deposition_flux_mol_m2_s(
        **kwargs,
        antoine_extrapolations=antoine_extrapolations,
        antoine_extrapolation_warnings=antoine_warnings,
    )

    assert instrumented_flux.hex() == baseline_flux.hex()
    assert "SiO" in antoine_extrapolations
    assert antoine_warnings


def test_sio_high_side_arrhenius_warning_surfaces_without_value_change():
    T_hot_K = 1900.0
    context: dict[str, object] = {}
    value = condensation.alpha_s("SiO", T_hot_K, context)
    expected = 0.52 * math.exp(-3685.0 / T_hot_K)
    evaluation = context["alpha_s_evaluation"]

    assert value.hex() == expected.hex()
    assert evaluation["alpha_s_extrapolated"] is True
    assert evaluation["alpha_s_temperature_above_valid_range"] is True
    assert evaluation["alpha_s_valid_range_K"] == [1000.0, 1800.0]
    assert "SiO alpha_s arrhenius coefficient extrapolated" in evaluation[
        "alpha_s_extrapolation_warning"
    ]
    assert "[1000, 1800]" in evaluation["alpha_s_extrapolation_warning"]

    in_context: dict[str, object] = {}
    in_value = condensation.alpha_s("SiO", 1500.0, in_context)
    in_expected = 0.52 * math.exp(-3685.0 / 1500.0)
    in_evaluation = in_context["alpha_s_evaluation"]

    assert in_value.hex() == in_expected.hex()
    assert in_evaluation["alpha_s_extrapolated"] is False
    assert "alpha_s_extrapolation_warning" not in in_evaluation


def test_sio_high_side_arrhenius_warning_reaches_alpha_provenance_report():
    segment = PipeSegment(
        name="hot_sio_wall",
        upstream_stage="stage_2",
        downstream_stage="stage_3",
        wall_temperature_C=1626.85,
        length_m=1.0,
        inner_diameter_m=0.05,
    )
    record = condensation._wall_alpha_record("SiO", segment=segment)

    assert record["alpha_s"].hex() == (
        0.52 * math.exp(-3685.0 / 1900.0)
    ).hex()
    assert record["alpha_s_extrapolated"] is True
    assert "alpha_s_extrapolation_warning" in record

    model = condensation.CondensationModel(CondensationTrain.create_default())
    model.configure_operating_conditions(
        wall_temperature_C=1626.85,
        pipe_segment_temperatures_C={
            segment.name: 1626.85 for segment in model.pipe_segments
        },
    )
    route = model.route(
        EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0),
        MeltState(temperature_C=1700.0),
    )
    warnings = route.sticking_alpha_provenance_notice.get(
        "alpha_s_extrapolation_warnings",
        [],
    )

    assert any(
        "SiO alpha_s arrhenius coefficient extrapolated" in warning
        and "[1000, 1800]" in warning
        for warning in warnings
    )


def test_cold_spot_stage_parser_rejects_numeric_lab_surface_suffix():
    segments = [
        PipeSegment(
            name="lab_surface",
            upstream_stage="lab_surface_1",
            downstream_stage="lab_surface_2",
            wall_temperature_C=500.0,
            length_m=1.0,
            inner_diameter_m=0.12,
        ),
        PipeSegment(
            name="production_stage",
            upstream_stage="stage_1",
            downstream_stage="stage_2",
            wall_temperature_C=500.0,
            length_m=1.0,
            inner_diameter_m=0.12,
        ),
    ]

    diagnostic = condensation.cold_spot_diagnostic(
        segments,
        {"SiO": 1.0},
        upstream_hot_wall_min_C=None,
    )

    assert {row["segment"] for row in diagnostic["findings"]} == {
        "production_stage"
    }


def test_free_molecular_hkl_incidence_uses_gas_temperature(monkeypatch):
    monkeypatch.setattr(
        condensation,
        "_wall_deposition_driving_pressure_pa",
        lambda *args, **kwargs: 1.0,
    )
    kwargs = {
        "species": "SiO",
        "P_local_pa": 1.0,
        "T_surface_K": 900.0,
        "alpha_s": 0.5,
        "regime_factor": 1.0,
    }

    cold_gas_flux = condensation._series_resistance_deposition_flux_mol_m2_s(
        **kwargs, T_gas_K=400.0
    )
    hot_gas_flux = condensation._series_resistance_deposition_flux_mol_m2_s(
        **kwargs, T_gas_K=1600.0
    )

    # J_inc is proportional to T_gas^-1/2 at fixed pressure and molecular mass.
    assert cold_gas_flux / hot_gas_flux == pytest.approx(2.0)


@pytest.mark.parametrize(
    ("source_class", "expected_status", "expected_output_status"),
    [
        ("internal-analytical", "UNCERTIFIED", "status_bearing"),
        ("cited_hkl_accommodation", "sourced", "sourced_with_surface_proxy"),
    ],
)
def test_cold_wall_source_class_controls_certification(
    monkeypatch, source_class, expected_status, expected_output_status
):
    monkeypatch.setattr(
        condensation,
        "_cold_wall_condensation_spec",
        lambda species: {
            "value": 1.0,
            "source": "test cold-wall source",
            "source_url": "https://example.invalid/source",
            "source_class": source_class,
            "status": "CITED",
            "output_status": "sourced_with_surface_proxy",
            "uncertainty_envelope": [0.5, 1.0],
            "uncertainty_flag": "test",
        },
    )
    record = condensation._cold_wall_condensation_record_payload("SiO", {})

    assert record["status"] == expected_status
    assert record["output_status"] == expected_output_status
    if source_class == "internal-analytical":
        assert "cannot certify" in record["certification_status_reason"]

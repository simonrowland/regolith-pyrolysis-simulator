from __future__ import annotations

import math

import pytest

from simulator.equipment import EquipmentDesigner
from simulator.lab_geometry import parse_lab_geometry


def _designer(**geometry):
    return EquipmentDesigner({"condenser_geometry": geometry})


def test_batch_headspace_counts_freeboard_once_and_includes_pipe_volume():
    design = EquipmentDesigner().design_for_batch(1000.0, {})

    melt_volume_m3 = 1000.0 / EquipmentDesigner.MELT_DENSITY_KG_M3
    freeboard_m3 = design.crucible.volume_m3 - melt_volume_m3
    pipe_volume_m3 = (
        math.pi * (design.pipe.diameter_m / 2.0) ** 2 * design.pipe.length_m
    )

    assert design.headspace_volume_m3 == pytest.approx(
        freeboard_m3 + pipe_volume_m3
    )


def test_lab_headspace_includes_each_declared_pipe_segment_volume():
    geometry = parse_lab_geometry(
        {
            "id": "two-segment-lab",
            "scale": "gram_lab",
            "equipment_sizing": "lab_fixed_geometry",
            "sample": {"mass_g": 10.0},
            "surfaces": [
                {
                    "id": "wide",
                    "role": "chamber_wall",
                    "area_m2": math.pi * 0.04 * 0.20,
                    "temperature_C": 500.0,
                    "view_factor_from_melt": 0.5,
                    "line_of_sight_to_melt": True,
                    "source_class": "measured",
                    "equivalent_diameter_m": 0.04,
                    "extraction_note": "measured wide segment",
                },
                {
                    "id": "narrow",
                    "role": "condenser",
                    "area_m2": math.pi * 0.01 * 0.30,
                    "temperature_C": 100.0,
                    "view_factor_from_melt": 0.2,
                    "line_of_sight_to_melt": False,
                    "source_class": "measured",
                    "equivalent_diameter_m": 0.01,
                    "extraction_note": "measured narrow segment",
                },
            ],
        }
    )
    assert geometry is not None

    design = EquipmentDesigner().design_for_batch(0.01, {}, lab_geometry=geometry)
    segment_volume_m3 = (
        math.pi * (0.04 / 2.0) ** 2 * 0.20
        + math.pi * (0.01 / 2.0) ** 2 * 0.30
    )
    melt_volume_m3 = 0.01 / EquipmentDesigner.MELT_DENSITY_KG_M3
    freeboard_m3 = design.crucible.volume_m3 - melt_volume_m3

    assert design.headspace_volume_m3 == pytest.approx(
        freeboard_m3 + segment_volume_m3
    )


@pytest.mark.parametrize("pressure_mbar", [-10.0, 0.0, float("nan"), float("inf")])
def test_collection_pipe_rejects_nonpositive_or_nonfinite_pressure(pressure_mbar):
    with pytest.raises(ValueError, match="pressure_mbar"):
        EquipmentDesigner().size_collection_pipe(0.01, pressure_mbar=pressure_mbar)


@pytest.mark.parametrize("throat", ["bad", -1.0, 0.0, float("nan")])
def test_invalid_configured_throat_fails_closed(throat):
    with pytest.raises(ValueError, match="initial_throat_area_m2"):
        _designer(initial_throat_area_m2=throat).size_collection_pipe(0.01)


def test_invalid_condenser_geometry_block_fails_closed():
    with pytest.raises(ValueError, match="condenser_geometry must be a mapping"):
        EquipmentDesigner({"condenser_geometry": "bad"}).size_collection_pipe(0.01)


@pytest.mark.parametrize("ratios", ["bad", {"stage": 0.5}, {"stage": "bad"}])
def test_invalid_or_constricting_stage_ratios_fail_closed(ratios):
    with pytest.raises(ValueError, match="stage_area_ratios"):
        _designer(stage_area_ratios=ratios).size_collection_pipe(0.01)


def test_configured_condenser_catalog_populates_plant_design():
    throat_area_m2 = 0.02
    design = _designer(
        initial_throat_area_m2=throat_area_m2,
        stage_area_ratios={
            "fe_stage1": 2.0,
            "sio_stage3": 3.0,
            "alkali_stage4": 4.0,
            "terminal": 5.0,
        },
    ).design_for_batch(1000.0, {})

    assert [stage.stage_name for stage in design.condensers] == [
        "fe_stage1",
        "sio_stage3",
        "alkali_stage4",
        "terminal",
    ]
    assert [stage.stage_number for stage in design.condensers] == [1, 3, 4, 7]
    assert [stage.surface_area_m2 for stage in design.condensers] == pytest.approx(
        [
            throat_area_m2 * 2.0,
            throat_area_m2 * 3.0,
            throat_area_m2 * 4.0,
            throat_area_m2 * 5.0,
        ]
    )

"""Hot-train applicability for a trace vapour is the onset result.

Designated findings keep their existing shape. Rb and Cs carry
``uncaptured_condensable`` from the same derivation that picked the stage.
"""

from __future__ import annotations

import inspect

from simulator.condensation import (
    CondensationModel,
    cold_spot_diagnostic,
    trace_vapour_condensation_onset,
)
from simulator.state import CondensationTrain, PipeSegment


def _segment(downstream: str, wall_temperature_C: float) -> PipeSegment:
    return PipeSegment(
        name=f"stage_0_to_{downstream}",
        upstream_stage="stage_0",
        downstream_stage=downstream,
        wall_temperature_C=wall_temperature_C,
        length_m=1.0,
        inner_diameter_m=0.12,
    )


def test_cold_spot_reads_applicability_from_the_onset() -> None:
    source = inspect.getsource(cold_spot_diagnostic)
    assert "_trace_onset_for_flow" in source
    assert "uncaptured_condensable" not in source
    assert '"Rb"' not in source and '"Cs"' not in source
    stages = CondensationTrain.create_default().stages
    rubidium = trace_vapour_condensation_onset("Rb", 100.0, stages=stages)
    cesium = trace_vapour_condensation_onset("Cs", 1.0, stages=stages)
    gallium = trace_vapour_condensation_onset("Ga", 100.0, stages=stages)
    assert rubidium.hot_train_applicability == "uncaptured_condensable"
    assert cesium.hot_train_applicability == "uncaptured_condensable"
    assert gallium.hot_train_applicability == "applicable"
    diagnostic = cold_spot_diagnostic(
        [
            _segment("stage_1", 100.0),
            _segment("stage_5", 100.0),
        ],
        {"Rb": 1.0, "Cs": 1.0, "Ga": 1.0, "Fe": 1.0, "Ca": 1.0},
        margin_C=0.0,
        upstream_hot_wall_min_C=None,
        species_partial_pressures_pa={
            "Rb": 100.0,
            "Cs": 1.0,
            "Ga": 100.0,
            "Fe": 100.0,
            "Ca": 100.0,
        },
        stages=stages,
    )
    by_species = {finding["species"]: finding for finding in diagnostic["findings"]}
    assert by_species["Rb"]["hot_train_applicability"] == rubidium.hot_train_applicability
    assert by_species["Rb"]["target_stage_number"] == rubidium.wall_landing_stage_number
    assert by_species["Cs"]["hot_train_applicability"] == cesium.hot_train_applicability
    assert by_species["Ga"]["hot_train_applicability"] == gallium.hot_train_applicability
    assert by_species["Ga"]["target_stage_number"] == gallium.wall_landing_stage_number
    assert "Ca" not in by_species
    iron = by_species["Fe"]
    assert iron["target_stage_number"] == 1
    assert iron["condensation_temperature_C"] == 1250
    assert "hot_train_applicability" not in iron


def test_route_passes_the_train_stages_into_the_cold_spot() -> None:
    route_source = inspect.getsource(CondensationModel._route)
    tick_source = inspect.getsource(CondensationModel._route_tick)
    assert "_route_tick" in route_source
    assert "stages=self.train.stages" in tick_source

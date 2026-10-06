"""Wall candidates for trace vapours come from the one onset derivation.

Carriers with no receiving condensed phase stay a visible gap. Ca, Al,
and Ti keep an empty candidate list.
"""

from __future__ import annotations

import inspect

import pytest

from simulator.coating_lifespan import (
    FoulingTerminalSnapshot,
    thickness_proxy_by_segment_m,
)
from simulator.condensation import (
    CondensationModel,
    _trace_vapour_carrier_sources,
    trace_vapour_condensation_onset,
)
from simulator.condensation_routing import designated_stage_number
from simulator.state import CondensationTrain


def _model_at(pressure_pa: float) -> CondensationModel:
    model = CondensationModel(CondensationTrain.create_default())
    carriers = _trace_vapour_carrier_sources()
    model.wall_species_partial_pressures_pa = {
        species: pressure_pa for species in carriers
    }
    return model


def test_wall_builder_calls_the_onset_function() -> None:
    source = inspect.getsource(
        CondensationModel._mixed_temperature_wall_candidate_segments
    )
    assert "trace_vapour_condensation_onset" in source or "_trace_onset_for_flow" in source
    assert "DESIGNATED_STAGE" not in source


def test_every_onset_species_is_a_wall_candidate_the_coating_sums() -> None:
    model = _model_at(100.0)
    deposits: dict[str, dict[str, float]] = {}
    coated: list[str] = []
    gaps: list[str] = []
    for species in sorted(_trace_vapour_carrier_sources()):
        onset = trace_vapour_condensation_onset(
            species,
            100.0,
            stages=model.train.stages,
        )
        names = [
            segment.name
            for segment in model._mixed_temperature_wall_candidate_segments(species)
        ]
        if onset.wall_landing_stage_number is None:
            assert names == []
            assert onset.status != "ok"
            gaps.append(species)
            continue
        expected = [
            segment.name
            for segment in model.pipe_segments
            if _downstream(segment.downstream_stage) is not None
            and _downstream(segment.downstream_stage) <= onset.wall_landing_stage_number
        ]
        assert names == expected
        assert names
        coated.append(species)
        deposits.setdefault(names[0], {})[species] = 1.0
    assert {"Rb", "Cs", "Pb", "Ga", "SnO", "Li", "GeO", "VO2"} <= set(coated)
    assert "BO2" in gaps
    assert "GeO" not in gaps
    assert "VO2" not in gaps
    areas = {segment: 1.0 for segment in deposits}
    thickness = thickness_proxy_by_segment_m(
        FoulingTerminalSnapshot(wall_deposit_by_segment_species_kg=deposits),
        segment_area_m2=areas,
        rho_deposit_kg_m3=1000.0,
    )
    counted: set[str] = set()
    for segment, species_kg in deposits.items():
        assert thickness[segment] == pytest.approx(len(species_kg) / 1000.0)
        counted.update(species_kg)
    assert counted == set(coated)


def test_declared_species_wall_lists_do_not_move() -> None:
    model = _model_at(100.0)
    model.wall_species_partial_pressures_pa["Ca"] = 100.0
    model.wall_species_partial_pressures_pa["Fe"] = 100.0
    assert model._mixed_temperature_wall_candidate_segments("Ca") == []
    assert model._mixed_temperature_wall_candidate_segments("Al") == []
    assert model._mixed_temperature_wall_candidate_segments("Ti") == []
    iron = [
        segment.name
        for segment in model._mixed_temperature_wall_candidate_segments("Fe")
    ]
    assert iron == ["stage_0_to_stage_1"]
    assert designated_stage_number("Fe") == 1
    assert model._mixed_temperature_wall_candidate_segments("Rb") != []
    bare = CondensationModel(CondensationTrain.create_default())
    assert bare._mixed_temperature_wall_candidate_segments("Rb") == []


def _downstream(stage_name: str) -> int | None:
    token = str(stage_name)
    if not token.startswith("stage_"):
        return None
    suffix = token.removeprefix("stage_")
    return int(suffix) if suffix.isdecimal() else None

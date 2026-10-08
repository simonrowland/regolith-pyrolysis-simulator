"""Wall candidates for trace vapours come from the one onset derivation.

Carriers with no receiving condensed phase stay a visible gap. Ca, Al,
and Ti keep an empty candidate list. A cold trace vapour reaches the
coating ledger from route(), not from a hand-built deposit map.
"""

from __future__ import annotations

import inspect
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

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
from simulator.state import CondensationTrain, EvaporationFlux, MeltState


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


def test_every_ok_onset_is_a_wall_candidate() -> None:
    model = _model_at(100.0)
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
    assert {"Rb", "Cs", "Pb", "Ga", "SnO", "Li", "GeO", "VO2"} <= set(coated)
    assert "BO2" in gaps
    assert "GeO" not in gaps
    assert "VO2" not in gaps


@pytest.mark.parametrize("species", ["Rb", "Pb", "SnO", "GeO"])
def test_cold_trace_vapour_reaches_the_coating_ledger(species) -> None:
    from simulator.yaml_cache import load_cached_safe_yaml

    payload = deepcopy(load_cached_safe_yaml(
        (Path(__file__).resolve().parents[1] / "data" / "vapor_pressures.yaml").read_text()
    ))
    family = next(family for family in payload["families"].values()
                  if species in family["physical_properties"]["species"])
    family["code_metadata"]["request_rule"] = "source_inventory_present"
    family["physical_properties"]["species"][species]["flux_dormant"] = False
    family["fiat_routing"]["compatibility_fields"]["flux_dormant"] = False
    model = CondensationModel(CondensationTrain.create_default(), vapor_pressure_data=payload)
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        species_partial_pressures_mbar={species: 1.0},
        pipe_diameter_m=0.12,
        gas_temperature_C=1700.0,
        wall_temperature_C=50.0,
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in model.train.stages
        },
    )
    flux = EvaporationFlux(species_kg_hr={species: 0.4})
    flux.update_totals()

    result = model.route(flux, MeltState())

    deposited_kg = result.wall_deposit_by_species[species]
    assert deposited_kg > 0.0
    assert (
        result.remaining_by_species[species]
        + result.retained_in_source_by_species.get(species, 0.0)
        + sum(stage.get(species, 0.0) for stage in result.condensed_by_stage_species.values())
        + deposited_kg
    ) == pytest.approx(0.4)
    snapshot = FoulingTerminalSnapshot.from_trace(
        SimpleNamespace(
            wall_deposit_by_segment_species_kg=(
                result.wall_deposit_by_segment_species
            ),
        )
    )
    coated_kg = sum(
        species_kg.get(species, 0.0)
        for species_kg in snapshot.deposit_plain().values()
    )
    assert coated_kg == pytest.approx(deposited_kg)
    areas = {segment: 1.0 for segment in snapshot.deposit_plain()}
    thickness = thickness_proxy_by_segment_m(
        snapshot,
        segment_area_m2=areas,
        rho_deposit_kg_m3=1000.0,
    )
    assert sum(thickness.values()) > 0.0
    if species == "Rb":
        assert result.condensation_refusals_by_species[species]["reason"] == (
            "flagged_uncaptured_condensable"
        )


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

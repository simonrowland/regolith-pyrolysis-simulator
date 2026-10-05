"""Pins of the routing outputs that Build B3 must leave unchanged.

Existing designated species keep their declared stage, their declared
condensation temperature, and the wall segments that stage selects.
Ca, Al, and Ti stay off the wall-candidate list. The coating thickness
proxy still sums whatever species a deposit map already contains.
"""

from __future__ import annotations

import pytest

from simulator.coating_lifespan import (
    FoulingTerminalSnapshot,
    thickness_proxy_by_segment_m,
)
from simulator.condensation import (
    CONDENSATION_TEMPS_C,
    CondensationModel,
    _species_condensation_temperature_C,
    antoine_dew_temperature_diagnostic,
    authoritative_condensation_temperature,
    cold_spot_diagnostic,
)
from simulator.condensation_routing import (
    DESIGNATED_STAGE,
    designated_stage_number,
)
from simulator.state import CondensationTrain, PipeSegment


# Review F routing table. Trace vapours are not in this map.
_DESIGNATED_STAGE = {
    "Fe": "stage_1_fe_condenser",
    "Cr": "stage_2_cr_oxide_harvest",
    "CrO2": "stage_2_cr_oxide_harvest",
    "Cr2O3": "stage_2_cr_oxide_harvest",
    "Mn": "stage_2_cr_oxide_harvest",
    "SiO": "stage_3_sio_zone",
    "SiO2": "stage_3_sio_zone",
    "Na": "stage_4_alkali_mg_cyclone",
    "K": "stage_4_alkali_mg_cyclone",
    "Mg": "stage_4_alkali_mg_cyclone",
}

_CONDENSATION_TEMPS_C = {
    "Fe": 1250,
    "SiO": 1050,
    "CrO2": 1250,
    "Mg": 580,
    "Na": 480,
    "K": 420,
    "Ca": 780,
    "Mn": 1000,
    "Cr": 1280,
    "Al": 1180,
    "Ti": 1500,
}

# Species with a declared temperature and no designated condenser stage.
_NO_WALL_STAGE = ("Ca", "Al", "Ti")


def _default_model() -> CondensationModel:
    return CondensationModel(CondensationTrain.create_default())


def test_designated_stage_is_the_existing_major_map() -> None:
    assert DESIGNATED_STAGE == _DESIGNATED_STAGE
    for species, destination in _DESIGNATED_STAGE.items():
        assert designated_stage_number(species) == {
            "stage_1_fe_condenser": 1,
            "stage_2_cr_oxide_harvest": 2,
            "stage_3_sio_zone": 3,
            "stage_4_alkali_mg_cyclone": 4,
        }[destination]


def test_condensation_temperatures_are_the_declared_table() -> None:
    assert CONDENSATION_TEMPS_C == _CONDENSATION_TEMPS_C
    for species, temperature_C in _CONDENSATION_TEMPS_C.items():
        assert _species_condensation_temperature_C(species) == pytest.approx(
            temperature_C
        )
        resolved = authoritative_condensation_temperature(species)
        assert resolved["temperature_C"] == pytest.approx(temperature_C)


def test_wall_candidates_stop_at_the_designated_stage() -> None:
    model = _default_model()
    by_name = {segment.name: segment for segment in model.pipe_segments}
    assert set(by_name) == {
        "stage_0_to_stage_1",
        "stage_1_to_stage_2",
        "stage_2_to_stage_3",
        "stage_3_to_stage_4",
        "stage_4_to_stage_5",
        "stage_5_to_stage_6",
        "stage_6_to_stage_7",
    }
    for species in _DESIGNATED_STAGE:
        stage_number = designated_stage_number(species)
        assert stage_number is not None
        names = [
            segment.name
            for segment in model._mixed_temperature_wall_candidate_segments(
                species
            )
        ]
        expected = [
            segment.name
            for segment in model.pipe_segments
            if _downstream_number(segment) is not None
            and _downstream_number(segment) <= stage_number
        ]
        assert names == expected
        assert names
    for species in _NO_WALL_STAGE:
        assert (
            model._mixed_temperature_wall_candidate_segments(species) == []
        )


def test_cold_spot_uses_the_declared_temperature_for_designated_species() -> None:
    segment = PipeSegment(
        name="stage_0_to_stage_1",
        upstream_stage="stage_0",
        downstream_stage="stage_1",
        wall_temperature_C=1000.0,
        length_m=1.0,
        inner_diameter_m=0.12,
    )
    diagnostic = cold_spot_diagnostic(
        [segment],
        {"Fe": 1.0},
        margin_C=0.0,
        upstream_hot_wall_min_C=None,
    )
    assert diagnostic["has_cold_spot"] is True
    assert diagnostic["findings"][0]["species"] == "Fe"
    assert diagnostic["findings"][0]["target_stage_number"] == 1
    assert diagnostic["findings"][0]["condensation_temperature_C"] == pytest.approx(
        1250.0
    )
    undesignated = cold_spot_diagnostic(
        [segment],
        {"Ca": 1.0, "Al": 1.0, "Ti": 1.0},
        margin_C=0.0,
        upstream_hot_wall_min_C=None,
    )
    assert undesignated["findings"] == []
    assert undesignated["has_cold_spot"] is False


def test_fe_antoine_dewpoint_at_1_mbar_stays_outside_the_certified_range() -> None:
    """Live Antoine surface at the historical 1 mbar routing pressure.

    100 Pa is below Fe's certified wall curve (1400-1900 K), so the
    diagnostic refuses instead of inventing a temperature. Designated Fe
    keeps the declared 1250 C routing temperature either way.
    """

    diagnostic = antoine_dew_temperature_diagnostic("Fe", 100.0)
    assert diagnostic["status"] == "refused_pressure_outside_certified_range"
    assert diagnostic["routing_authority"] is False
    assert diagnostic["temperature_K"] is None
    assert diagnostic["partial_pressure_Pa"] == pytest.approx(100.0)
    assert diagnostic["valid_range_K"] == [1400.0, 1900.0]


def test_coating_thickness_proxy_sums_the_deposit_map() -> None:
    area_m2 = 2.0
    rho_kg_m3 = 1000.0
    fe_kg = 2.0
    na_kg = 1.0
    snapshot = FoulingTerminalSnapshot(
        wall_deposit_by_segment_species_kg={
            "stage_0_to_stage_1": {"Fe": fe_kg, "Na": na_kg},
        }
    )
    thickness = thickness_proxy_by_segment_m(
        snapshot,
        segment_area_m2={"stage_0_to_stage_1": area_m2},
        rho_deposit_kg_m3=rho_kg_m3,
    )
    assert thickness["stage_0_to_stage_1"] == pytest.approx(
        (fe_kg + na_kg) / (rho_kg_m3 * area_m2)
    )


def _downstream_number(segment: PipeSegment) -> int | None:
    token = str(segment.downstream_stage)
    if not token.startswith("stage_"):
        return None
    suffix = token.removeprefix("stage_")
    return int(suffix) if suffix.isdecimal() else None

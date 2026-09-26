"""M07: hot-wall diagnostic uses local pressure vs wall P_sat.

CONFIRM (VERIFY-codex / VERIFY-grok): millibar Fe at a 1400 C wall must
remain a violation because its source-equation wall P_sat is 1.1375418658 Pa;
a 100 Pa stream exceeds it ~88-fold. Alkali and Mg walls use their own
source-equation P_sat values rather than a uniform temperature floor.

Successful-value controls named by the verifiers: adequately hot or
diluted Fe stays clear; a wall below the SiO engineering landing T
still sets has_cold_spot; the 1300 C Fe dew-point warning remains;
SiO at 1 mbar and 1400 C is undersaturated on the disproportionation
equilibrium (p_eq is above 1 mbar), so the diagnostic stays clear.
"""

from __future__ import annotations

import pytest

from simulator.condensation import (
    CELSIUS_TO_KELVIN_OFFSET,
    CONDENSATION_TEMPS_C,
    CondensationModel,
    _try_antoine_psat_pa,
    _wall_deposition_driving_pressure_pa,
    cold_spot_diagnostic,
)
from simulator.state import CondensationTrain, EvaporationFlux, MeltState, PipeSegment


MBAR_TO_PA = 100.0
MILLIBAR_BAKEOFF_PA = 1.0 * MBAR_TO_PA
DILUTE_QUIESCENT_PA = 0.01
WALL_1400_K = 1400.0 + CELSIUS_TO_KELVIN_OFFSET


def _upstream_segment(downstream_stage: str, wall_temperature_C: float) -> PipeSegment:
    return PipeSegment(
        name=f"upstream_{downstream_stage}",
        upstream_stage="stage_0",
        downstream_stage=downstream_stage,
        wall_temperature_C=wall_temperature_C,
        length_m=1.0,
        inner_diameter_m=0.12,
    )


def _fe_sio_segments(wall_temperature_C: float) -> list[PipeSegment]:
    return [
        _upstream_segment("stage_3", wall_temperature_C),
        _upstream_segment("stage_1", wall_temperature_C),
    ]


def test_millibar_fe_sio_at_1400_c_is_hot_wall_violation():
    """Verifier CONFIRM probe: 1400 C millibar Fe/SiO must not all-clear."""

    assert CONDENSATION_TEMPS_C["Fe"] == 1250
    assert CONDENSATION_TEMPS_C["SiO"] == 1050

    fe_psat, refused = _try_antoine_psat_pa("Fe", WALL_1400_K)
    assert refused is False
    assert fe_psat == pytest.approx(1.1375418658, rel=1e-9, abs=0.0)
    assert MILLIBAR_BAKEOFF_PA > fe_psat

    diagnostic = cold_spot_diagnostic(
        _fe_sio_segments(1400.0),
        {"SiO": 1.0, "Fe": 1.0},
        species_partial_pressures_pa={
            "SiO": MILLIBAR_BAKEOFF_PA,
            "Fe": MILLIBAR_BAKEOFF_PA,
        },
    )

    assert diagnostic["has_upstream_hot_wall_violation"] is True
    assert any(
        finding["species"] == "Fe"
        and "local pressure exceeds wall saturation" in finding["warning"]
        for finding in diagnostic["upstream_hot_wall_findings"]
    )


def test_dilute_or_hot_fe_at_valid_wall_stays_clear():
    """Verifier successful-value: diluted or adequately hot Fe remains clear."""

    dilute = cold_spot_diagnostic(
        _fe_sio_segments(1400.0),
        {"Fe": 1.0},
        species_partial_pressures_pa={"Fe": DILUTE_QUIESCENT_PA},
    )
    hot = cold_spot_diagnostic(
        _fe_sio_segments(1800.0),
        {"Fe": 1.0},
        species_partial_pressures_pa={"Fe": MILLIBAR_BAKEOFF_PA},
    )

    assert dilute["has_upstream_hot_wall_violation"] is False
    assert dilute["has_cold_spot"] is False
    assert hot["has_upstream_hot_wall_violation"] is False
    assert hot["has_cold_spot"] is False


def test_species_change_and_low_wall_uniform_minimum():
    """Per-species dew points admit alkali/Mg and catch supersaturated Fe."""

    for species, wall_temperature_C, downstream_stage in (
        ("Na", 500.0, "stage_4"),
        ("K", 400.0, "stage_4"),
        ("Mg", 600.0, "stage_4"),
    ):
        psat, refused = _try_antoine_psat_pa(
            species,
            wall_temperature_C + CELSIUS_TO_KELVIN_OFFSET,
        )
        assert refused is False
        assert psat > MILLIBAR_BAKEOFF_PA
        diagnostic = cold_spot_diagnostic(
            [_upstream_segment(downstream_stage, wall_temperature_C)],
            {species: 1.0},
            upstream_hot_wall_min_C=1400.0,
            species_partial_pressures_pa={species: MILLIBAR_BAKEOFF_PA},
        )
        assert diagnostic["has_upstream_hot_wall_violation"] is False
        assert diagnostic["upstream_hot_wall_findings"] == []

    na = cold_spot_diagnostic(
        [_upstream_segment("stage_4", 1400.0)],
        {"Na": 1.0},
        species_partial_pressures_pa={"Na": MILLIBAR_BAKEOFF_PA},
    )

    na_psat, refused = _try_antoine_psat_pa("Na", WALL_1400_K)
    assert refused is False
    assert na_psat > MILLIBAR_BAKEOFF_PA
    low_wall = cold_spot_diagnostic(
        _fe_sio_segments(1300.0),
        {"Fe": 1.0, "SiO": 1.0},
        species_partial_pressures_pa={
            "Fe": MILLIBAR_BAKEOFF_PA,
            "SiO": MILLIBAR_BAKEOFF_PA,
        },
    )

    assert na["has_upstream_hot_wall_violation"] is False
    assert na["has_cold_spot"] is False
    assert low_wall["has_upstream_hot_wall_violation"] is True
    assert all(
        "local pressure exceeds wall saturation" in finding["warning"]
        for finding in low_wall["upstream_hot_wall_findings"]
    )

    for wall_temperature_C in (1400.0, 1500.0, 1600.0):
        fe_psat, refused = _try_antoine_psat_pa(
            "Fe",
            wall_temperature_C + CELSIUS_TO_KELVIN_OFFSET,
        )
        assert refused is False
        assert MILLIBAR_BAKEOFF_PA > fe_psat
        diagnostic = cold_spot_diagnostic(
            [_upstream_segment("stage_1", wall_temperature_C)],
            {"Fe": 1.0},
            species_partial_pressures_pa={"Fe": MILLIBAR_BAKEOFF_PA},
        )
        assert diagnostic["has_upstream_hot_wall_violation"] is True
        assert all(
            "local pressure exceeds wall saturation" in finding["warning"]
            for finding in diagnostic["upstream_hot_wall_findings"]
        )


def test_wall_below_sio_landing_temperature_still_has_cold_spot():
    """Verifier successful-value: 1000 C vs SiO 1050 C still flags a cold spot."""

    diagnostic = cold_spot_diagnostic(
        [_upstream_segment("stage_3", 1000.0)],
        {"SiO": 1.0},
        upstream_hot_wall_min_C=None,
        species_partial_pressures_pa={"SiO": MILLIBAR_BAKEOFF_PA},
    )

    assert diagnostic["has_cold_spot"] is True
    assert diagnostic["findings"][0]["species"] == "SiO"
    assert diagnostic["findings"][0]["condensation_temperature_C"] == pytest.approx(
        1050.0
    )


def test_sio_reactive_equilibrium_at_1400_c_stays_undersaturated():
    """1 mbar SiO at 1400 C is below p_eq, so the duct stays clear."""

    sio_psat, refused = _try_antoine_psat_pa("SiO", WALL_1400_K)
    assert refused is False
    assert sio_psat is not None and sio_psat > 0.0

    notice: dict[str, object] = {}
    driving = _wall_deposition_driving_pressure_pa(
        "SiO",
        MILLIBAR_BAKEOFF_PA,
        WALL_1400_K,
        diagnostic_out=notice,
    )
    assert driving == 0.0
    assert notice["wall_saturation_pressure_pa"] > MILLIBAR_BAKEOFF_PA
    assert notice["wall_saturation_pressure_status"] == "reactive_equilibrium"
    assert notice["reason"] == "reactive_equilibrium_undersaturated"

    sio_only = cold_spot_diagnostic(
        [_upstream_segment("stage_3", 1400.0)],
        {"SiO": 1.0},
        species_partial_pressures_pa={"SiO": MILLIBAR_BAKEOFF_PA},
    )
    assert sio_only["has_upstream_hot_wall_violation"] is False


def test_route_millibar_fe_at_1400_c_uses_wall_partial_pressure():
    """Production route() must feed local P into the diagnostic."""

    def _route(wall_C: float, fe_mbar: float):
        model = CondensationModel(
            CondensationTrain.create_default(),
            wall_temperature_C=wall_C,
        )
        model.configure_operating_conditions(
            wall_temperature_C=wall_C,
            overhead_pressure_mbar=10.0,
            species_partial_pressures_mbar={"Fe": fe_mbar},
            pipe_diameter_m=0.12,
            gas_temperature_C=1700.0,
            stage_area_m2_by_stage={
                str(stage.stage_number): 1.0 for stage in model.train.stages
            },
            pipe_segment_temperatures_C={
                segment.name: wall_C for segment in model.pipe_segments
            },
        )
        model.route(
            EvaporationFlux(species_kg_hr={"Fe": 1.0}, total_kg_hr=1.0),
            MeltState(temperature_C=1700.0),
        )
        return model.last_cold_spot_diagnostic

    millibar = _route(1400.0, 1.0)
    dilute = _route(1400.0, DILUTE_QUIESCENT_PA / MBAR_TO_PA)
    hot = _route(1800.0, 1.0)

    assert millibar["has_upstream_hot_wall_violation"] is True
    assert dilute["has_upstream_hot_wall_violation"] is False
    assert hot["has_upstream_hot_wall_violation"] is False


def test_route_copies_wall_antoine_extrapolation_notice():
    model = CondensationModel(
        CondensationTrain.create_default(),
        wall_temperature_C=500.0,
    )
    model.configure_operating_conditions(
        wall_temperature_C=500.0,
        overhead_pressure_mbar=10.0,
        species_partial_pressures_mbar={"Na": 1.0},
        pipe_diameter_m=0.12,
        gas_temperature_C=1700.0,
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in model.train.stages
        },
        pipe_segment_temperatures_C={
            segment.name: 500.0 for segment in model.pipe_segments
        },
    )
    model.route(
        EvaporationFlux(species_kg_hr={"Na": 1.0}, total_kg_hr=1.0),
        MeltState(temperature_C=1700.0),
    )

    extrapolations = model.last_sticking_alpha_provenance_notice[
        "wall_saturation_pressure_extrapolations_by_species"
    ]["Na"]
    assert any(
        record["authority_level"] == "extrapolated"
        and record["temperature_K"] == pytest.approx(500.0 + CELSIUS_TO_KELVIN_OFFSET)
        for record in extrapolations.values()
    )

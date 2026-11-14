"""M07: hot-wall diagnostic uses local pressure vs wall P_sat.

CONFIRM (VERIFY-codex / VERIFY-grok): millibar Fe/SiO at a 1400 C wall
false-cleared both diagnostic components because the check used
CONDENSATION_TEMPS_C landing setpoints (Fe 1250 C, SiO 1050 C) plus a
uniform 1400 C floor — the SiO quiescent 0.1 µbar figure, not bake-off
dew temperature. The current gate uses each segment's arriving partial
pressure against its wall saturation pressure; the uniform floor is not
a hot-wall violation threshold.

Successful-value controls named by the verifiers: adequately hot or
diluted Fe stays clear; a wall below the SiO engineering landing T
still sets has_cold_spot; a 1300 C wall is judged by its local mixture.
SiO uses the wall-saturation law: missing pO2 refuses, and a real Psat
above the local pressure drives nothing.

d-384: the hot-wall invariant is per species. A wall violates iff the
arriving partial exceeds P_sat,species(T_wall). Fe P_sat is the value
``_try_antoine_psat_pa`` returns, the same owner the diagnostic calls.
d-383 replaces the pure-Fe line, so these tests construct partials on
both sides of that owner value and do not pin an Fe dew point or an
Fe saturation number.

A wall below the SiO engineering landing T still sets has_cold_spot.
A declared reactive wall reaction uses the same saturation law for
deposition and hot-wall diagnostics; a refused law is never replaced by
P_sat = 0 (d-402).
The uniform 1400 C floor is not a violation (b-282). A refused wall
P_sat, or a missing local pressure, is a typed refusal of the hot-wall
quantity, not a clear (d-367).
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from engines.builtin.sodium_saturation import (
    sodium_saturation_block,
    sodium_saturation_pressure,
)

from simulator.condensation import (
    CELSIUS_TO_KELVIN_OFFSET,
    CONDENSATION_TEMPS_C,
    CondensationModel,
    WallSaturationPressureRefusal,
    _antoine_psat_pa,
    VAPOR_PRESSURE_DATA,
    _try_antoine_psat_pa,
    _wall_deposition_driving_pressure_pa,
    cold_spot_diagnostic,
)
from simulator.condensation_routing import DESIGNATED_STAGE, designated_stage_number
from simulator.optimize.physics import PhysicsConstraintSet
from simulator.state import (
    MOLAR_MASS,
    CondensationTrain,
    EvaporationFlux,
    MeltState,
    PipeSegment,
)


MBAR_TO_PA = 100.0
MILLIBAR_BAKEOFF_PA = 1.0 * MBAR_TO_PA
DILUTE_QUIESCENT_PA = 0.01
WALL_1400_K = 1400.0 + CELSIUS_TO_KELVIN_OFFSET


@pytest.fixture(scope="module")
def sodium_saturation_500_c():
    block = sodium_saturation_block(VAPOR_PRESSURE_DATA["metals"]["Na"])
    assert block is not None
    return sodium_saturation_pressure(block, 500.0 + CELSIUS_TO_KELVIN_OFFSET)


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
    """Keep the parent value pin and verify the local Fe partial is judged."""

    assert CONDENSATION_TEMPS_C["Fe"] == 1250
    assert CONDENSATION_TEMPS_C["SiO"] == 1050
    fe_psat, refused = _try_antoine_psat_pa("Fe", WALL_1400_K)
    assert refused is False
    assert fe_psat == pytest.approx(1.1375418658, rel=1e-9, abs=0.0)
    assert MILLIBAR_BAKEOFF_PA > fe_psat

    segments = _fe_sio_segments(1400.0)
    diagnostic = cold_spot_diagnostic(
        segments,
        {"SiO": 1.0, "Fe": 1.0},
        species_partial_pressures_pa_by_segment=_pressures_by_segment(
            segments,
            {"SiO": MILLIBAR_BAKEOFF_PA, "Fe": MILLIBAR_BAKEOFF_PA},
        ),
    )

    assert diagnostic["has_upstream_hot_wall_violation"] is True
    assert any(
        finding["species"] == "Fe"
        for finding in diagnostic["upstream_hot_wall_findings"]
    )


def test_dilute_or_hot_fe_at_valid_wall_stays_clear():
    """Retain the parent's diluted and hot Fe successful-value controls."""

    dilute_segments = _fe_sio_segments(1400.0)
    dilute = cold_spot_diagnostic(
        dilute_segments,
        {"Fe": 1.0},
        species_partial_pressures_pa_by_segment=_pressures_by_segment(
            dilute_segments, {"Fe": DILUTE_QUIESCENT_PA}
        ),
    )
    hot_segments = _fe_sio_segments(1800.0)
    hot = cold_spot_diagnostic(
        hot_segments,
        {"Fe": 1.0},
        species_partial_pressures_pa_by_segment=_pressures_by_segment(
            hot_segments, {"Fe": MILLIBAR_BAKEOFF_PA}
        ),
    )

    assert dilute["has_upstream_hot_wall_violation"] is False
    assert dilute["has_cold_spot"] is False
    assert hot["has_upstream_hot_wall_violation"] is False
    assert hot["has_cold_spot"] is False


def _pressures_by_segment(segments, partials):
    """State the arriving mixture at each segment this call judges.

    The hot-wall chokepoint does not broadcast one pressure onto every
    wall. Callers that have not modeled capture pass the same mixture
    explicitly.
    """

    if partials is None:
        return None
    return {segment.name: dict(partials) for segment in segments}


def test_fe_sio_above_owner_psat_at_1400_c_is_hot_wall_violation():
    """1400 C Fe above the production P_sat must not all-clear (d-384).

    SiO has no wall saturation here. That refusal must not turn the Fe
    finding back into a landing-temperature warning.
    """

    assert CONDENSATION_TEMPS_C["Fe"] == 1250
    assert CONDENSATION_TEMPS_C["SiO"] == 1050

    fe_psat, known, _extrapolated = _saturation_oracle("Fe", 1400.0)
    assert known
    partial_pa = fe_psat * 2.0
    assert partial_pa > fe_psat

    segments = _fe_sio_segments(1400.0)
    diagnostic = cold_spot_diagnostic(
        segments,
        {"SiO": 1.0, "Fe": 1.0},
        species_partial_pressures_pa_by_segment=_pressures_by_segment(
            segments,
            {
                "SiO": MILLIBAR_BAKEOFF_PA,
                "Fe": partial_pa,
            },
        ),
    )

    assert diagnostic["has_upstream_hot_wall_violation"] is True
    assert diagnostic["upstream_hot_wall_min_C"] is None
    fe_findings = [
        finding
        for finding in diagnostic["upstream_hot_wall_findings"]
        if finding["species"] == "Fe"
    ]
    assert fe_findings
    assert all("saturation" in finding["warning"] for finding in fe_findings)
    assert all(
        "below" not in finding["warning"]
        for finding in diagnostic["upstream_hot_wall_findings"]
    )
    fe_judgement = _judgement_for(diagnostic, "Fe")
    assert fe_judgement["partial_pressure_pa"] == pytest.approx(partial_pa)
    assert fe_judgement["saturation_pressure_pa"] == pytest.approx(fe_psat)
    assert fe_judgement["status"] == "violation"


@pytest.mark.parametrize("wall_C", (1300.0, 1400.0, 1775.0, 1800.0))
@pytest.mark.parametrize(
    ("factor", "violates"),
    [(0.5, False), (1.0, False), (2.0, True)],
)
def test_fe_hot_wall_violation_iff_partial_exceeds_owner_psat(
    wall_C: float,
    factor: float,
    violates: bool,
):
    """d-384: violation iff arriving partial > P_sat,Fe(T_wall).

    P_sat is the production owner. Half, equal, and double that value
    sit on both sides of the threshold, including the equal boundary
    of the strict comparison. d-383 can replace the Fe line without
    moving these cases.
    """

    fe_psat, known, _extrapolated = _saturation_oracle("Fe", wall_C)
    assert known
    partial_pa = fe_psat * factor
    assert violates is (partial_pa > fe_psat)

    segments = [_upstream_segment("stage_1", wall_C)]
    diagnostic = cold_spot_diagnostic(
        segments,
        {"Fe": 1.0},
        species_partial_pressures_pa_by_segment=_pressures_by_segment(
            segments, {"Fe": partial_pa}
        ),
    )

    judgement = _judgement_for(diagnostic, "Fe")
    assert judgement["partial_pressure_pa"] == pytest.approx(partial_pa)
    assert judgement["saturation_pressure_pa"] == pytest.approx(fe_psat)
    assert judgement["status"] == ("violation" if violates else "clear")
    assert diagnostic["has_upstream_hot_wall_violation"] is violates
    assert diagnostic["upstream_hot_wall_refused"] is False
    # These walls sit above Fe's 1250 C landing setpoint minus margin.
    assert diagnostic["has_cold_spot"] is False


def test_na_500_c_alkali_duty_is_not_a_hot_wall_violation(sodium_saturation_500_c):
    """b-282: Na at 500 C on 1 mbar alkali duty is undersaturated.

    The Browning-Potter owner puts P_sat at this wall above 100 Pa.
    Its own declared band qualifies that comparison as extrapolated;
    the historical uniform 1400 C floor must not decide the verdict.
    """

    na_psat, refused = _try_antoine_psat_pa("Na", 500.0 + CELSIUS_TO_KELVIN_OFFSET)
    assert refused is False
    assert na_psat > MILLIBAR_BAKEOFF_PA

    segments = [_upstream_segment("stage_4", 500.0)]
    diagnostic = cold_spot_diagnostic(
        segments,
        {"Na": 1.0},
        upstream_hot_wall_min_C=1400.0,
        species_partial_pressures_pa_by_segment=_pressures_by_segment(
            segments, {"Na": MILLIBAR_BAKEOFF_PA}
        ),
    )

    assert diagnostic["has_upstream_hot_wall_violation"] is False
    assert diagnostic["upstream_hot_wall_refused"] is False
    assert diagnostic["upstream_hot_wall_findings"] == []
    assert diagnostic["upstream_hot_wall_refusals"] == []
    # The owner's certified band, rather than an obsolete Antoine band,
    # qualifies this best-estimate undersaturated comparison (d-367).
    qualifications = diagnostic["upstream_hot_wall_extrapolations"]
    assert len(qualifications) == 1
    assert qualifications[0]["species"] == "Na"
    assert qualifications[0]["saturation_pressure_pa"] == pytest.approx(na_psat)
    assert qualifications[0]["partial_pressure_pa"] == pytest.approx(
        MILLIBAR_BAKEOFF_PA
    )
    assert qualifications[0]["supersaturated"] is False
    records = qualifications[0]["records"]
    assert records
    assert all(record["authority_level"] == "extrapolated" for record in records)
    assert records[0]["valid_range_K"] == pytest.approx(
        sodium_saturation_500_c.certified_range_K
    )


def test_species_change_and_low_wall_uniform_minimum():
    """Na millibar at 1400 C is undersaturated; 1300 C Fe follows its own P_sat.

    The Fe partial is twice the production P_sat at 1300 C (d-384), so the
    violation is the dew-point criterion on a wall below the old 1400 C floor.
    """

    na_psat, refused = _try_antoine_psat_pa("Na", WALL_1400_K)
    assert refused is False
    assert na_psat > MILLIBAR_BAKEOFF_PA

    na_segments = [_upstream_segment("stage_4", 1400.0)]
    na = cold_spot_diagnostic(
        na_segments,
        {"Na": 1.0},
        species_partial_pressures_pa_by_segment=_pressures_by_segment(
            na_segments, {"Na": MILLIBAR_BAKEOFF_PA}
        ),
    )
    fe_psat, known, _extrapolated = _saturation_oracle("Fe", 1300.0)
    assert known
    fe_partial_pa = fe_psat * 2.0
    low_segments = _fe_sio_segments(1300.0)
    low_wall = cold_spot_diagnostic(
        low_segments,
        {"Fe": 1.0, "SiO": 1.0},
        species_partial_pressures_pa_by_segment=_pressures_by_segment(
            low_segments,
            {
                "Fe": fe_partial_pa,
                "SiO": MILLIBAR_BAKEOFF_PA,
            },
        ),
    )

    assert na["has_upstream_hot_wall_violation"] is False
    assert na["has_cold_spot"] is False
    assert low_wall["has_upstream_hot_wall_violation"] is True
    fe_judgement = _judgement_for(low_wall, "Fe")
    assert fe_judgement["partial_pressure_pa"] == pytest.approx(fe_partial_pa)
    assert fe_judgement["saturation_pressure_pa"] == pytest.approx(fe_psat)
    assert fe_judgement["status"] == "violation"


def test_wall_below_sio_landing_temperature_still_has_cold_spot():
    """Verifier successful-value: 1000 C vs SiO 1050 C still flags a cold spot."""

    segments = [_upstream_segment("stage_3", 1000.0)]
    diagnostic = cold_spot_diagnostic(
        segments,
        {"SiO": 1.0},
        upstream_hot_wall_min_C=None,
        species_partial_pressures_pa_by_segment=_pressures_by_segment(
            segments, {"SiO": MILLIBAR_BAKEOFF_PA}
        ),
    )

    assert diagnostic["has_cold_spot"] is True
    assert diagnostic["findings"][0]["species"] == "SiO"
    assert diagnostic["findings"][0]["condensation_temperature_C"] == pytest.approx(
        1050.0
    )


def test_sio_wall_saturation_uses_the_law_not_the_zero_backstop():
    """A pO2-free route or a supplied pO2 uses the wall-saturation law.

    A free-route saturation is available when the reaction ladder includes
    a pO2-independent path. Neither path may replace its Psat with zero.
    """

    free_psat, refused = _try_antoine_psat_pa("SiO", WALL_1400_K)
    assert refused is False
    assert free_psat is not None and free_psat > 0.0
    free_notice: dict[str, object] = {}
    free_driving = _wall_deposition_driving_pressure_pa(
        "SiO",
        free_psat * 0.5,
        WALL_1400_K,
        diagnostic_out=free_notice,
    )
    assert free_driving == 0.0
    assert free_notice["wall_saturation_pressure_pa"] == pytest.approx(
        free_psat
    )

    po2_pa = 1.0e-4
    psat = _antoine_psat_pa("SiO", WALL_1400_K, wall_pO2_Pa=po2_pa)
    assert psat is not None and psat > 0.0
    notice: dict[str, object] = {}
    driving = _wall_deposition_driving_pressure_pa(
        "SiO",
        psat * 0.5,
        WALL_1400_K,
        wall_pO2_Pa=po2_pa,
        diagnostic_out=notice,
    )
    assert driving == 0.0
    assert notice["wall_saturation_pressure_pa"] == pytest.approx(psat)
    assert notice.get("wall_saturation_pressure_status") != "reactive_product_backstop"

    sio_segments = [_upstream_segment("stage_3", 1400.0)]
    sio_only = cold_spot_diagnostic(
        sio_segments,
        {"SiO": 1.0},
        species_partial_pressures_pa_by_segment=_pressures_by_segment(
            sio_segments, {"SiO": free_psat * 0.5}
        ),
    )
    assert sio_only["has_upstream_hot_wall_violation"] is False
    assert sio_only["upstream_hot_wall_refused"] is False
    assert _judgement_for(sio_only, "SiO")["saturation_pressure_pa"] == pytest.approx(
        free_psat
    )
    supersaturated = cold_spot_diagnostic(
        sio_segments,
        {"SiO": 1.0},
        species_partial_pressures_pa_by_segment=_pressures_by_segment(
            sio_segments,
            {"SiO": 2.0 * psat, "O2": po2_pa},
        ),
    )
    assert supersaturated["has_upstream_hot_wall_violation"] is True
    judgement = _judgement_for(supersaturated, "SiO")
    assert judgement["saturation_pressure_pa"] == pytest.approx(psat)
    assert judgement["status"] == "violation"


def test_sio_reactive_wall_saturation_backstop_unchanged():
    """Retain the parent test id while checking d-402's replacement law."""

    po2_pa = 1.0e-4
    psat = _antoine_psat_pa("SiO", WALL_1400_K, wall_pO2_Pa=po2_pa)
    assert psat is not None and psat > 0.0
    notice: dict[str, object] = {}
    driving = _wall_deposition_driving_pressure_pa(
        "SiO",
        2.0 * psat,
        WALL_1400_K,
        wall_pO2_Pa=po2_pa,
        diagnostic_out=notice,
    )

    assert driving == pytest.approx(psat)
    assert notice["wall_saturation_pressure_pa"] == pytest.approx(psat)
    assert notice.get("wall_saturation_pressure_status") != (
        "reactive_product_backstop"
    )


def test_sio_at_1300_c_refuses_unchecked_bakeoff_instead_of_clearing():
    """Keep the parent test id while checking d-402's predictive law."""

    psat, refused = _try_antoine_psat_pa(
        "SiO", 1300.0 + CELSIUS_TO_KELVIN_OFFSET
    )
    assert refused is False
    assert psat is not None and psat > 0.0
    segments = [_upstream_segment("stage_3", 1300.0)]
    diagnostic = cold_spot_diagnostic(
        segments,
        {"SiO": 1.0},
        species_partial_pressures_pa_by_segment=_pressures_by_segment(
            segments, {"SiO": 2.0 * psat}
        ),
    )

    assert diagnostic["has_cold_spot"] is False
    judgement = _judgement_for(diagnostic, "SiO")
    assert judgement["saturation_pressure_pa"] == pytest.approx(psat)
    assert judgement["status"] == "violation"
    assert diagnostic["has_upstream_hot_wall_violation"] is True


def test_omitted_partial_pressure_refuses_fe_hot_wall_quantity():
    """d-367: omitted pressure on a 1300 C Fe wall is not an all-clear.

    The wall is above Fe's 1250 C landing setpoint, so the cold-spot
    check is quiet. The dew-point check has no local pressure to compare.
    """

    diagnostic = cold_spot_diagnostic(
        [_upstream_segment("stage_1", 1300.0)],
        {"Fe": 1.0},
    )

    assert diagnostic["has_cold_spot"] is False
    assert diagnostic["has_upstream_hot_wall_violation"] is False
    assert diagnostic["upstream_hot_wall_findings"] == []
    assert diagnostic["upstream_hot_wall_refused"] is True
    refusal = diagnostic["upstream_hot_wall_refusals"][0]
    assert refusal["status"] == "refused"
    assert refusal["output_status"] == "refused"
    assert refusal["quantity"] == "upstream_hot_wall"
    assert refusal["species"] == "Fe"
    assert refusal["refusal_type"] == "DepositionInputRefusal"
    assert refusal["parameter"] == "species_partial_pressure_pa"
    assert refusal["reason"] == "missing"
    assert refusal["wall_temperature_C"] == 1300.0


@pytest.mark.parametrize(
    ("partials", "reason"),
    [
        ({"Fe": float("nan")}, "must be finite and positive"),
        ({"Fe": 0.0}, "must be finite and positive"),
        ({"Fe": -5.0}, "must be finite and positive"),
        ({"Fe": "nope"}, "must be finite and positive"),
        ({"Na": MILLIBAR_BAKEOFF_PA}, "missing"),
    ],
)
def test_invalid_partial_pressure_refuses_fe_hot_wall_quantity(partials, reason):
    """A present-but-unusable Fe pressure is the same typed refusal."""

    segments = [_upstream_segment("stage_1", 1300.0)]
    diagnostic = cold_spot_diagnostic(
        segments,
        {"Fe": 1.0},
        species_partial_pressures_pa_by_segment=_pressures_by_segment(
            segments, partials
        ),
    )

    assert diagnostic["has_upstream_hot_wall_violation"] is False
    assert diagnostic["upstream_hot_wall_refused"] is True
    refusal = diagnostic["upstream_hot_wall_refusals"][0]
    assert refusal["refusal_type"] == "DepositionInputRefusal"
    assert refusal["parameter"] == "species_partial_pressure_pa"
    assert refusal["reason"] == reason
    assert refusal["species"] == "Fe"


def test_route_fe_judgement_uses_arriving_partial_against_owner_psat():
    """route() judges Fe with the arriving partial and the production P_sat.

    d-384. Inlets are half and double P_sat,Fe(T_wall). Upstream capture
    can move the partial that arrives, so the verdict is that arriving
    partial against the owner, not a fixed Fe saturation number.
    """

    def _route(wall_C: float, fe_pa: float):
        model = CondensationModel(
            CondensationTrain.create_default(),
            wall_temperature_C=wall_C,
        )
        model.configure_operating_conditions(
            wall_temperature_C=wall_C,
            overhead_pressure_mbar=10.0,
            species_partial_pressures_mbar={"Fe": fe_pa / MBAR_TO_PA},
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

    for wall_C in (1400.0, 1800.0):
        fe_psat, known, _extrapolated = _saturation_oracle("Fe", wall_C)
        assert known
        for factor in (0.5, 2.0):
            diagnostic = _route(wall_C, fe_psat * factor)
            judgements = [
                judgement
                for judgement in diagnostic["upstream_hot_wall_judgements"]
                if judgement["species"] == "Fe"
            ]
            assert judgements, (wall_C, factor)
            saw_violation = False
            for judgement in judgements:
                owner, owner_known, _records = _saturation_oracle(
                    "Fe", judgement["wall_temperature_C"]
                )
                assert owner_known
                partial_pa = judgement["partial_pressure_pa"]
                assert partial_pa is not None and partial_pa > 0.0
                assert judgement["saturation_pressure_pa"] == pytest.approx(owner)
                violated = partial_pa > owner
                assert judgement["status"] == (
                    "violation" if violated else "clear"
                ), (wall_C, factor, partial_pa, owner)
                saw_violation = saw_violation or violated
            assert diagnostic["has_upstream_hot_wall_violation"] is saw_violation


def test_route_millibar_fe_at_1400_c_uses_wall_partial_pressure():
    """Keep the parent route pin for bake-off, dilution, and hot walls."""

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


# Invariant (b-282 / d-367): every upstream hot-wall species judgement is a
# typed refusal or a computed saturation comparison, and that same record
# reaches every coating-gate consumer. An out-of-domain Antoine evaluation
# keeps its best estimate and carries the extrapolation qualification. A
# refusal is never dropped on the way to the gate, and it does not become
# a deposit veto.
# Product aliases SiO2 and Cr2O3 have a designated stage but no landing
# setpoint. cold_spot_diagnostic raises before the hot-wall chokepoint;
# they are not a second judgement path.
_HOT_WALL_SPECIES = tuple(
    species
    for species in DESIGNATED_STAGE
    if species in CONDENSATION_TEMPS_C
)
_HOT_WALL_WALLS_C = (20.0, 500.0, 800.0, 1000.0, 1400.0, 1775.0)
_HOT_WALL_PRESSURE_CASES = ("missing", "nan", "bakeoff_100pa", "extreme_1e12")
_HOT_WALL_CHANNELS = (
    "payload",
    "runtime",
    "merged_report",
    "report_diagnostics",
    "fraction_report",
)


def _hot_wall_partial_pressures(species: str, pressure_case: str):
    if pressure_case == "missing":
        return None
    if pressure_case == "nan":
        return {species: float("nan")}
    if pressure_case == "bakeoff_100pa":
        return {species: MILLIBAR_BAKEOFF_PA}
    if pressure_case == "extreme_1e12":
        return {species: 1.0e12}
    raise AssertionError(pressure_case)


def _hot_wall_diagnostic(species: str, wall_C: float, pressure_case: str):
    stage = designated_stage_number(species)
    assert stage is not None
    segments = [_upstream_segment(f"stage_{stage}", wall_C)]
    return cold_spot_diagnostic(
        segments,
        {species: 1.0},
        species_partial_pressures_pa_by_segment=_pressures_by_segment(
            segments,
            _hot_wall_partial_pressures(species, pressure_case),
        ),
    )


def _saturation_oracle(species: str, wall_C: float):
    notes: dict[str, dict] = {}
    pressure_pa, refused = _try_antoine_psat_pa(
        species,
        wall_C + CELSIUS_TO_KELVIN_OFFSET,
        antoine_extrapolations=notes,
        antoine_extrapolation_warnings=[],
    )
    known = (
        not refused
        and pressure_pa is not None
        and math.isfinite(pressure_pa)
    )
    extrapolated = [
        record
        for record in notes.values()
        if isinstance(record, dict)
        and record.get("status") != "refused"
        and record.get("authority_level") != "unavailable"
        and (
            record.get("authority_level") == "extrapolated"
            or record.get("status") == "extrapolated"
        )
    ]
    return pressure_pa, known, extrapolated


def _qualification_identity(record: dict) -> tuple:
    raw_range = record.get("valid_range_K")
    if isinstance(raw_range, (list, tuple)) and len(raw_range) == 2:
        try:
            norm_range = (
                round(float(raw_range[0]), 6),
                round(float(raw_range[1]), 6),
            )
        except (TypeError, ValueError):
            norm_range = (raw_range[0], raw_range[1])
    else:
        norm_range = raw_range
    return (
        record.get("authority_level"),
        record.get("status"),
        norm_range,
        str(record.get("reason")),
    )


def _assert_published_extrapolation(published: list, oracle: list, where: str):
    assert [
        _qualification_identity(record) for record in published
    ] == [
        _qualification_identity(record) for record in oracle
    ], where


def _judgement_for(diagnostic: dict, species: str) -> dict:
    matches = [
        judgement
        for judgement in diagnostic["upstream_hot_wall_judgements"]
        if judgement["species"] == species
    ]
    assert len(matches) == 1
    return matches[0]


def _assert_diagnostic_invariant(
    diagnostic: dict,
    species: str,
    wall_C: float,
    pressure_case: str,
):
    """One judgement per checked species, qualified exactly as the fit was."""

    where = f"{species} {wall_C} C {pressure_case}"
    judgement = _judgement_for(diagnostic, species)
    assert judgement["wall_temperature_C"] == wall_C
    pressure_refused = pressure_case in {"missing", "nan"}
    if pressure_refused:
        assert diagnostic["has_upstream_hot_wall_violation"] is False, where
        assert diagnostic["upstream_hot_wall_refused"] is True, where
        assert judgement["status"] == "refused", where
        assert judgement["refusal_type"] == "DepositionInputRefusal", where
        assert judgement["extrapolations"] == [], where
        assert diagnostic["upstream_hot_wall_extrapolations"] == [], where
        return

    partial_pa = _hot_wall_partial_pressures(species, pressure_case)[species]
    saturation_pa, known, extrapolated = _saturation_oracle(species, wall_C)
    if not known:
        assert diagnostic["has_upstream_hot_wall_violation"] is False, where
        assert diagnostic["upstream_hot_wall_refused"] is True, where
        assert judgement["status"] == "refused", where
        assert judgement["refusal_type"], where
        assert judgement["saturation_pressure_pa"] is None, where
        assert diagnostic["upstream_hot_wall_findings"] == [], where
        return

    assert diagnostic["upstream_hot_wall_refused"] is False, where
    assert judgement["status"] == (
        "violation" if partial_pa > saturation_pa else "clear"
    ), where
    assert judgement["saturation_pressure_pa"] == pytest.approx(saturation_pa), where
    assert judgement["partial_pressure_pa"] == pytest.approx(partial_pa), where
    assert diagnostic["has_upstream_hot_wall_violation"] is (
        judgement["status"] == "violation"
    ), where
    _assert_published_extrapolation(
        judgement["extrapolations"],
        extrapolated,
        where,
    )
    publications = [
        item
        for item in diagnostic["upstream_hot_wall_extrapolations"]
        if item["species"] == species
    ]
    if extrapolated:
        assert len(publications) == 1, where
        assert publications[0]["supersaturated"] is (
            judgement["status"] == "violation"
        ), where
        assert publications[0]["saturation_pressure_pa"] == pytest.approx(
            saturation_pa
        ), where
        _assert_published_extrapolation(
            publications[0]["records"],
            extrapolated,
            where,
        )
    else:
        assert publications == [], where
        assert judgement["extrapolations"] == [], where


def _clear_campaign_report(*, fraction: bool, diagnostics: dict | None):
    report = {
        "campaigns_to_resinter_total": math.inf,
        "resinter_threshold_kg": None,
        "wall_deposit_kg_per_campaign": 0.0,
        "unqualified_deposition_rate_kg_per_campaign": 0.0,
        "authoritative_for_resinter": True,
        "output_status": "authoritative",
        "status_reason": "",
        "sticking_alpha_authority": {
            "authoritative_for_deposit_mass": True,
        },
    }
    if fraction:
        report["feedstock_charge_mass_kg"] = 1.0
        report["coating_constraint_mode"] = "upstream_deposit_fraction"
        report["coating_constraint_authoritative"] = True
    if diagnostics is not None:
        report["coating_diagnostics"] = diagnostics
    return report


def _coating_margin(channel: str, diagnostic: dict):
    constraints = PhysicsConstraintSet(active_gates=("coating",))
    if channel == "runtime":
        trace = SimpleNamespace(
            snapshots=(),
            wall_deposit_by_segment_species_delta=(),
            condensation_model=SimpleNamespace(
                cold_spot_history=[diagnostic],
            ),
        )
        return constraints.coating(trace)
    if channel == "merged_report":
        trace = SimpleNamespace(
            wall_fouling_report=_clear_campaign_report(
                fraction=False,
                diagnostics=None,
            ),
            condensation_model=SimpleNamespace(
                last_cold_spot_diagnostic=diagnostic,
            ),
        )
        return constraints.coating(trace)
    if channel == "report_diagnostics":
        return constraints.coating_from_fouling_report(
            _clear_campaign_report(fraction=False, diagnostics=diagnostic)
        )
    if channel == "fraction_report":
        return constraints.coating_from_fouling_report(
            _clear_campaign_report(fraction=True, diagnostics=diagnostic)
        )
    raise AssertionError(channel)


def _gate_flags(margin, reason: str, species: str) -> list:
    flags = (margin.status_payload or {}).get("coating_warning_flags") or []
    matched = []
    for flag in flags:
        if flag.get("reason") != reason:
            continue
        finding = flag.get("finding") or {}
        owner = flag.get("species") or finding.get("species")
        if owner == species:
            matched.append(flag)
    return matched


def _assert_gate_invariant(
    margin,
    species: str,
    wall_C: float,
    pressure_case: str,
):
    where = f"{species} {wall_C} C {pressure_case} {margin.status}"
    payload = margin.status_payload or {}
    assert margin.feasible is True, where
    assert payload.get("coating_verdict") == "clear", where
    assert payload.get("coating_violation_reasons") in (None, []), where
    pressure_refused = pressure_case in {"missing", "nan"}
    if pressure_refused:
        flags = _gate_flags(margin, "upstream_hot_wall_refused", species)
        assert len(flags) == 1, where
        assert flags[0]["finding"]["refusal_type"] == "DepositionInputRefusal"
        assert _gate_flags(margin, "upstream_hot_wall_extrapolated", species) == []
        assert _gate_flags(
            margin, "upstream_hot_wall_supersaturation", species
        ) == []
        return

    partial_pa = _hot_wall_partial_pressures(species, pressure_case)[species]
    saturation_pa, known, extrapolated = _saturation_oracle(species, wall_C)
    if not known:
        flags = _gate_flags(margin, "upstream_hot_wall_refused", species)
        assert len(flags) == 1, where
        assert flags[0]["finding"]["refusal_type"]
        assert _gate_flags(
            margin, "upstream_hot_wall_supersaturation", species
        ) == []
        return

    violation_flags = _gate_flags(
        margin, "upstream_hot_wall_supersaturation", species
    )
    if partial_pa > saturation_pa:
        assert len(violation_flags) == 1, where
    else:
        assert violation_flags == [], where
    extrapolation_flags = _gate_flags(
        margin, "upstream_hot_wall_extrapolated", species
    )
    if extrapolated:
        assert len(extrapolation_flags) == 1, where
        assert extrapolation_flags[0]["saturation_pressure_pa"] == pytest.approx(
            saturation_pa
        ), where
        assert extrapolation_flags[0]["partial_pressure_pa"] == pytest.approx(
            partial_pa
        ), where
        _assert_published_extrapolation(
            extrapolation_flags[0]["extrapolations"],
            extrapolated,
            where,
        )
    else:
        assert extrapolation_flags == [], where
    if not extrapolated and partial_pa <= saturation_pa:
        assert _gate_flags(margin, "upstream_hot_wall_refused", species) == []


@pytest.mark.parametrize("species", _HOT_WALL_SPECIES)
@pytest.mark.parametrize("wall_C", _HOT_WALL_WALLS_C)
@pytest.mark.parametrize("pressure_case", _HOT_WALL_PRESSURE_CASES)
@pytest.mark.parametrize("channel", _HOT_WALL_CHANNELS)
def test_hot_wall_judgement_is_qualified_on_every_consumer(
    species: str,
    wall_C: float,
    pressure_case: str,
    channel: str,
):
    diagnostic = _hot_wall_diagnostic(species, wall_C, pressure_case)
    _assert_diagnostic_invariant(diagnostic, species, wall_C, pressure_case)
    if channel == "payload":
        return
    margin = _coating_margin(channel, diagnostic)
    _assert_gate_invariant(margin, species, wall_C, pressure_case)


def _dalton_partials_after_capture_pa(
    inlet_partial_pa: dict[str, float],
    total_pa: float,
    remaining_fraction: dict[str, float],
) -> dict[str, float]:
    """Arriving partials after upstream capture. Independent of production.

    Premise: carrier molar flow persists. On a unit inlet basis,
    y_i = p_i/P, y_c = 1 - sum(y_i), n_i = y_i * (m_i/m_i,0),
    p_i' = n_i / (y_c + sum(n_j)) * P. Mass ratio equals molar ratio.
    """

    mole_fraction = {
        species: partial_pa / total_pa
        for species, partial_pa in inlet_partial_pa.items()
    }
    carrier_fraction = 1.0 - sum(mole_fraction.values())
    basis = {
        species: fraction * remaining_fraction.get(species, 1.0)
        for species, fraction in mole_fraction.items()
    }
    total_basis = carrier_fraction + sum(basis.values())
    return {
        species: amount / total_basis * total_pa
        for species, amount in basis.items()
    }


def test_route_judges_na_on_pressure_after_upstream_fe_capture(sodium_saturation_500_c):
    """Upstream Fe capture must enrich Na across the owner's saturation.

    Start Na at half the 500 C owner pressure. On a five-P_sat total
    basis, Fe occupies four P_sat and carrier half a P_sat. Removing
    more than five eighths of the Fe leaves Na above P_sat at the next wall.
    The independent Dalton projection below uses actual captured mass.
    """

    na_psat = sodium_saturation_500_c.pressure_pa
    inlet_na_pa = na_psat * 0.5
    inlet_fe_pa = na_psat * 4.0
    total_pa = na_psat * 5.0
    assert inlet_na_pa < na_psat
    # Premise: inlet p_Na/p_Fe = n_Na/n_Fe, while each source rate is mass.
    # Algebra: m_Na/m_Fe = (p_Na/p_Fe)*(M_Na/M_Fe). The molar masses share
    # units, so the ratio is kg/kg; with zero capture this reconstructs the
    # inlet basis, and complete Fe capture leaves Na enriched against carrier.
    fe_inlet_kg_hr = 1.0
    na_inlet_kg_hr = fe_inlet_kg_hr * (inlet_na_pa / inlet_fe_pa) * (
        MOLAR_MASS["Na"] / MOLAR_MASS["Fe"]
    )

    model = CondensationModel(CondensationTrain.create_default())
    model.configure_operating_conditions(
        wall_temperature_C=500.0,
        overhead_pressure_mbar=total_pa / MBAR_TO_PA,
        species_partial_pressures_mbar={
            "Na": inlet_na_pa / MBAR_TO_PA,
            "Fe": inlet_fe_pa / MBAR_TO_PA,
        },
        pipe_diameter_m=0.12,
        gas_temperature_C=1700.0,
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in model.train.stages
        },
        pipe_segment_temperatures_C={
            segment.name: 500.0 for segment in model.pipe_segments
        },
    )
    result = model.route(
        EvaporationFlux(
            species_kg_hr={"Na": na_inlet_kg_hr, "Fe": fe_inlet_kg_hr},
            total_kg_hr=na_inlet_kg_hr + fe_inlet_kg_hr,
        ),
        MeltState(temperature_C=1700.0),
    )

    fe_captured_upstream_kg_hr = result.condensed_by_stage_species[
        "stage_0_hot_duct"
    ]["Fe"]
    assert "Na" not in result.condensed_by_stage_species.get("stage_0_hot_duct", {})
    fe_remaining = 1.0 - fe_captured_upstream_kg_hr
    assert 0.0 < fe_remaining < 0.5
    expected = _dalton_partials_after_capture_pa(
        {"Na": inlet_na_pa, "Fe": inlet_fe_pa},
        total_pa,
        {"Na": 1.0, "Fe": fe_remaining},
    )
    assert expected["Na"] > na_psat > inlet_na_pa

    judgements = [
        judgement
        for judgement in model.last_cold_spot_diagnostic["upstream_hot_wall_judgements"]
        if judgement["species"] == "Na" and judgement["segment"] == "stage_0_to_stage_1"
    ]
    assert len(judgements) == 1
    judgement = judgements[0]
    assert judgement["partial_pressure_pa"] == pytest.approx(expected["Na"])
    assert judgement["saturation_pressure_pa"] == pytest.approx(na_psat)
    assert judgement["status"] == "violation"
    stored = model.wall_species_partial_pressures_pa_by_segment[
        "stage_0_to_stage_1"
    ]["Na"]
    assert stored == pytest.approx(expected["Na"])
    fe_judgement = next(
        item
        for item in model.last_cold_spot_diagnostic["upstream_hot_wall_judgements"]
        if item["species"] == "Fe" and item["segment"] == "stage_0_to_stage_1"
    )
    assert fe_judgement["partial_pressure_pa"] == pytest.approx(expected["Fe"])
    assert expected["Fe"] < inlet_fe_pa

    for name, partials in model.wall_species_partial_pressures_pa_by_segment.items():
        if partials.get("Na", 0.0) > 0.0:
            continue
        assert not any(
            item["species"] == "Na" and item["segment"] == name
            for item in model.last_cold_spot_diagnostic["upstream_hot_wall_judgements"]
        )


# Invariant: the partial on a hot-wall judgement is the partial of the
# mixture that arrives at that segment. Inlet pressure is not reused after
# capture. Na at 500 C uses the Browning-Potter production owner; Fe
# uses _saturation_oracle, the same production resolver as the diagnostic.
# The independent Dalton projection preserves the arriving-mixture invariant.
_NO_WALL_SATURATION_SPECIES = frozenset({"CrO2"})
_ARRIVING_CAPTURES = ("none", "partner_90", "self_gone")
_ENRICHED_PARTIAL_PA = 1000.0 * 0.1 / 0.28


def _capture_remaining(species: str, capture: str) -> dict[str, float]:
    partner = "Na" if species == "Fe" else "Fe"
    if capture == "none":
        return {species: 1.0, partner: 1.0}
    if capture == "partner_90":
        return {species: 1.0, partner: 0.1}
    if capture == "self_gone":
        return {species: 0.0, partner: 1.0}
    raise AssertionError(capture)


def _flags_for_segment(margin, reason: str, species: str, segment: str) -> list:
    flags = (margin.status_payload or {}).get("coating_warning_flags") or []
    matched = []
    for flag in flags:
        if flag.get("reason") != reason:
            continue
        finding = flag.get("finding") or {}
        owner = flag.get("species") or finding.get("species")
        segment_name = flag.get("segment") or finding.get("segment")
        if owner == species and segment_name == segment:
            matched.append(flag)
    return matched


@pytest.mark.parametrize("species", _HOT_WALL_SPECIES)
@pytest.mark.parametrize("wall_C", _HOT_WALL_WALLS_C)
@pytest.mark.parametrize("capture", _ARRIVING_CAPTURES)
@pytest.mark.parametrize("channel", _HOT_WALL_CHANNELS)
def test_every_hot_wall_judgement_uses_the_arriving_partial(
    species: str,
    wall_C: float,
    capture: str,
    channel: str,
    sodium_saturation_500_c,
):
    """Enumerates species, wall, capture class, and coating channel.

    Upstream capture of an 800 Pa partner raises a 100 Pa species to
    357.1428571428571 Pa. The downstream judgement has to use that
    pressure. A species with no surviving flow is not judged there.
    """

    partner = "Na" if species == "Fe" else "Fe"
    inlet = {species: 100.0, partner: 800.0}
    remaining = _capture_remaining(species, capture)
    upstream = _dalton_partials_after_capture_pa(
        inlet, 1000.0, {species: 1.0, partner: 1.0}
    )
    downstream = _dalton_partials_after_capture_pa(inlet, 1000.0, remaining)
    if capture == "partner_90":
        assert downstream[species] == pytest.approx(_ENRICHED_PARTIAL_PA)
        assert downstream[species] != pytest.approx(100.0)
    segments = [
        PipeSegment(
            name="wall_inlet",
            upstream_stage="stage_0",
            downstream_stage="stage_1",
            wall_temperature_C=wall_C,
            length_m=1.0,
            inner_diameter_m=0.12,
        ),
        PipeSegment(
            name="wall_after",
            upstream_stage="stage_0",
            downstream_stage="stage_1",
            wall_temperature_C=wall_C,
            length_m=1.0,
            inner_diameter_m=0.12,
        ),
    ]
    local_pa = {
        "wall_inlet": upstream[species],
        "wall_after": downstream[species],
    }
    diagnostic = cold_spot_diagnostic(
        segments,
        {species: 1.0},
        species_partial_pressures_pa_by_segment={
            "wall_inlet": {species: local_pa["wall_inlet"]},
            "wall_after": {species: local_pa["wall_after"]},
        },
        surviving_species_kg_hr_by_segment={
            "wall_inlet": {species: 1.0},
            "wall_after": {species: remaining[species]},
        },
    )
    by_segment = {
        judgement["segment"]: judgement
        for judgement in diagnostic["upstream_hot_wall_judgements"]
        if judgement["species"] == species
    }
    assert "wall_inlet" in by_segment
    if capture == "self_gone":
        assert "wall_after" not in by_segment
        judged_names = ("wall_inlet",)
    else:
        assert "wall_after" in by_segment
        judged_names = ("wall_inlet", "wall_after")

    if species == "Fe":
        # d-384: same owner the diagnostic calls. A hand Antoine number
        # would keep the pure-Fe line d-383 replaces.
        pinned_psat, pinned_known, _extrapolated = _saturation_oracle(
            species, wall_C
        )
    elif species == "Na" and wall_C == 500.0:
        pinned_psat = sodium_saturation_500_c.pressure_pa
        pinned_known = True
    else:
        pinned_psat = None
        pinned_known = False
    for name in judged_names:
        judgement = by_segment[name]
        where = f"{species} {wall_C} {capture} {name}"
        assert judgement["partial_pressure_pa"] == pytest.approx(
            local_pa[name]
        ), where
        if capture == "partner_90" and name == "wall_after":
            assert judgement["partial_pressure_pa"] != pytest.approx(100.0)
        if species in _NO_WALL_SATURATION_SPECIES:
            assert judgement["status"] == "refused", where
            assert judgement["saturation_pressure_pa"] is None, where
            assert diagnostic["has_upstream_hot_wall_violation"] is False, where
            continue
        if not pinned_known:
            if species == "Fe":
                assert judgement["status"] == "refused", where
                assert judgement["saturation_pressure_pa"] is None, where
            continue
        assert judgement["saturation_pressure_pa"] == pytest.approx(
            pinned_psat
        ), where
        expected_status = (
            "violation" if local_pa[name] > pinned_psat else "clear"
        )
        assert judgement["status"] == expected_status, where

    if channel == "payload":
        return
    margin = _coating_margin(channel, diagnostic)
    assert margin.feasible is True
    for name in judged_names:
        judgement = by_segment[name]
        if judgement["status"] == "violation":
            flags = _flags_for_segment(
                margin, "upstream_hot_wall_supersaturation", species, name
            )
            assert len(flags) == 1
            assert flags[0]["finding"]["partial_pressure_pa"] == pytest.approx(
                local_pa[name]
            )
        elif judgement["status"] == "refused":
            flags = _flags_for_segment(
                margin, "upstream_hot_wall_refused", species, name
            )
            assert len(flags) == 1
            assert flags[0]["finding"]["partial_pressure_pa"] == pytest.approx(
                local_pa[name]
            )
        else:
            assert _flags_for_segment(
                margin, "upstream_hot_wall_supersaturation", species, name
            ) == []


# Invariant (b-282 / d-367): no arriving species flow is a silent 0.0.
# The hot-wall chokepoint is cold_spot_diagnostic. Every species on every
# flow source (surviving-flow map, or the inlet rate when that map is
# omitted) is one of:
#   - a computed judgement (any finite flow > 0, then P_sat comparison
#     or a saturation refusal),
#   - a typed refusal (segment missing, species key missing, or the value
#     is not finite and non-negative),
#   - not judged, only when the flow is an explicit finite zero.
# A missing species key is the refusal. It is not the zero. A positive
# flow at or below 1e-15 kg/hr is not the zero either: that flow is still
# judged, and d-384 decides the verdict from the arriving partial and the
# production P_sat.
_POSITIVE_ARRIVING_KG_HR = {
    "positive": 1.0,
    # Reviewer counterexample, below the old mass floor.
    "sub_floor_positive": 1e-16,
    # The old `> 1e-15` comparison skipped this value too.
    "floor_positive": 1e-15,
    # Smallest positive float64. Any hidden floor fails this class.
    "min_positive": math.nextafter(0.0, math.inf),
}
_ARRIVING_FLOW_CASES = (
    "missing_species",
    "missing_segment",
    "explicit_zero",
    "negative",
    "nan",
    "inf",
    "non_numeric",
    "none",
    *_POSITIVE_ARRIVING_KG_HR,
)
# Inlet-only has no map, so it cannot express a missing key or a
# non-numeric value. Those raise at the inlet parser, or they are map
# refusals. Explicit zero and every positive magnitude still apply:
# with no map the inlet rate is the arriving flow.
_INLET_ONLY_FLOW_CASES = (
    "explicit_zero",
    *_POSITIVE_ARRIVING_KG_HR,
)
_FLOW_SOURCE_CASES = (
    # Inlet is 1 kg/hr, so a dust surviving flow is not hidden by it.
    tuple(("surviving_map", case) for case in _ARRIVING_FLOW_CASES)
    # Inlet is the explicit zero. The map still decides: a positive
    # surviving flow is judged, and a missing key is refused.
    + tuple(("inlet_zero_map", case) for case in _ARRIVING_FLOW_CASES)
    + tuple(("inlet_only", case) for case in _INLET_ONLY_FLOW_CASES)
)
_FLOW_REFUSAL_REASON = {
    "missing_species": "missing",
    "missing_segment": "missing",
    "negative": "must be finite and non-negative",
    "nan": "must be finite and non-negative",
    "inf": "must be finite and non-negative",
    "non_numeric": "must be finite and non-negative",
    "none": "must be finite and non-negative",
}


def _surviving_species_flows(species: str, segment_name: str, flow_case: str):
    """One input class of the surviving-flow map. None means the map is omitted."""

    if flow_case == "missing_segment":
        return {}
    if flow_case == "missing_species":
        # The segment is present and the species is not. That is a refusal.
        return {segment_name: {}}
    value = {
        "explicit_zero": 0.0,
        "negative": -1.0,
        "nan": float("nan"),
        "inf": float("inf"),
        "non_numeric": "nope",
        "none": None,
        **_POSITIVE_ARRIVING_KG_HR,
    }[flow_case]
    return {segment_name: {species: value}}


def _arriving_flow_inputs(
    species: str,
    segment_name: str,
    flow_source: str,
    flow_case: str,
):
    """Inlet rate and optional surviving-flow map for one chokepoint branch.

    ``surviving_map`` keeps a 1 kg/hr inlet so a dust surviving flow is
    not hidden by the inlet magnitude. ``inlet_zero_map`` is the other
    inlet extreme: an explicit zero inlet must not hide the map.
    ``inlet_only`` omits the map, and the inlet rate is the arriving flow.
    """

    if flow_source == "inlet_only":
        if flow_case == "explicit_zero":
            return 0.0, None
        return _POSITIVE_ARRIVING_KG_HR[flow_case], None
    surviving = _surviving_species_flows(species, segment_name, flow_case)
    if flow_source == "surviving_map":
        return 1.0, surviving
    if flow_source == "inlet_zero_map":
        return 0.0, surviving
    raise AssertionError(flow_source)


def _assert_arriving_flow_invariant(
    diagnostic: dict,
    species: str,
    flow_source: str,
    flow_case: str,
):
    where = f"{species} 1400 C 100 Pa {flow_source} {flow_case}"
    matches = [
        judgement
        for judgement in diagnostic["upstream_hot_wall_judgements"]
        if judgement["species"] == species
    ]
    if flow_case == "explicit_zero":
        assert matches == [], where
        assert diagnostic["upstream_hot_wall_refused"] is False, where
        assert diagnostic["has_upstream_hot_wall_violation"] is False, where
        assert diagnostic["upstream_hot_wall_findings"] == [], where
        assert diagnostic["upstream_hot_wall_refusals"] == [], where
        return

    assert len(matches) == 1, where
    judgement = matches[0]
    if flow_case in _FLOW_REFUSAL_REASON:
        assert judgement["status"] == "refused", where
        assert judgement["refusal_type"] == "DepositionInputRefusal", where
        assert judgement["parameter"] == "surviving_species_kg_hr", where
        assert judgement["reason"] == _FLOW_REFUSAL_REASON[flow_case], where
        assert judgement["partial_pressure_pa"] is None, where
        assert diagnostic["has_upstream_hot_wall_violation"] is False, where
        assert diagnostic["upstream_hot_wall_refused"] is True, where
        assert diagnostic["upstream_hot_wall_findings"] == [], where
        assert diagnostic["upstream_hot_wall_extrapolations"] == [], where
        return

    assert flow_case in _POSITIVE_ARRIVING_KG_HR, where
    expected_kg_hr = _POSITIVE_ARRIVING_KG_HR[flow_case]
    _assert_diagnostic_invariant(diagnostic, species, 1400.0, "bakeoff_100pa")
    assert judgement["kg_hr"] == pytest.approx(expected_kg_hr), where
    assert judgement["partial_pressure_pa"] == pytest.approx(MILLIBAR_BAKEOFF_PA), where


def _assert_arriving_flow_gate(
    margin,
    species: str,
    flow_source: str,
    flow_case: str,
):
    where = f"{species} 1400 C 100 Pa {flow_source} {flow_case} {margin.status}"
    payload = margin.status_payload or {}
    assert margin.feasible is True, where
    assert payload.get("coating_verdict") == "clear", where
    if flow_case == "explicit_zero":
        assert _gate_flags(margin, "upstream_hot_wall_refused", species) == [], where
        assert _gate_flags(
            margin, "upstream_hot_wall_supersaturation", species
        ) == [], where
        return
    if flow_case in _FLOW_REFUSAL_REASON:
        flags = _gate_flags(margin, "upstream_hot_wall_refused", species)
        assert len(flags) == 1, where
        assert flags[0]["finding"]["parameter"] == "surviving_species_kg_hr", where
        assert flags[0]["finding"]["reason"] == _FLOW_REFUSAL_REASON[flow_case], where
        assert _gate_flags(
            margin, "upstream_hot_wall_supersaturation", species
        ) == [], where
        return
    _assert_gate_invariant(margin, species, 1400.0, "bakeoff_100pa")


@pytest.mark.parametrize("species", _HOT_WALL_SPECIES)
@pytest.mark.parametrize("flow_source,flow_case", _FLOW_SOURCE_CASES)
@pytest.mark.parametrize("channel", _HOT_WALL_CHANNELS)
def test_arriving_species_flow_is_computed_or_refused(
    species: str,
    flow_source: str,
    flow_case: str,
    channel: str,
):
    """Every species, flow source, flow class, and coating channel.

    Fe at inlet 1 kg/hr, surviving 1e-16 kg/hr, a 1400 C wall, and 100 Pa
    is the sub-floor class: still judged. The dew-point criterion, not the
    flow magnitude, sets the verdict (d-384).
    Explicit 0.0 is the only class that skips. Inlet-only repeats that
    rule when no map is supplied. Inlet-zero-map repeats it when the
    inlet is 0.0 and the surviving flow is still positive.
    """

    stage = designated_stage_number(species)
    assert stage is not None
    segment = _upstream_segment(f"stage_{stage}", 1400.0)
    inlet_kg_hr, surviving = _arriving_flow_inputs(
        species,
        segment.name,
        flow_source,
        flow_case,
    )
    diagnostic = cold_spot_diagnostic(
        [segment],
        {species: inlet_kg_hr},
        species_partial_pressures_pa_by_segment={
            segment.name: {species: MILLIBAR_BAKEOFF_PA},
        },
        surviving_species_kg_hr_by_segment=surviving,
    )
    _assert_arriving_flow_invariant(
        diagnostic,
        species,
        flow_source,
        flow_case,
    )
    if channel == "payload":
        return
    margin = _coating_margin(channel, diagnostic)
    _assert_arriving_flow_gate(margin, species, flow_source, flow_case)

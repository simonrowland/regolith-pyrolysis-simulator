"""M07: hot-wall diagnostic uses local pressure vs wall P_sat.

CONFIRM (VERIFY-codex / VERIFY-grok): millibar Fe/SiO at a 1400 C wall
false-cleared both diagnostic components because the check used
CONDENSATION_TEMPS_C landing setpoints (Fe 1250 C, SiO 1050 C) plus a
uniform 1400 C floor — the SiO quiescent 0.1 µbar figure, not bake-off
dew temperature. Fe wall P_sat(1673.15 K) is 1.1375418658 Pa; a 100 Pa
stream exceeds it ~88-fold.

Successful-value controls named by the verifiers: adequately hot or
diluted Fe stays clear; a wall below the SiO engineering landing T
still sets has_cold_spot; a 1300 C millibar Fe wall remains a dew-point
violation; SiO reactive P_sat ~= 0 deposition policy is untouched (M30 / D04).
The uniform 1400 C floor is not a violation (b-282). A refused wall
P_sat, or a missing local pressure, is a typed refusal of the hot-wall
quantity, not a clear (d-367).
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from simulator.condensation import (
    CELSIUS_TO_KELVIN_OFFSET,
    CONDENSATION_TEMPS_C,
    CondensationModel,
    _try_antoine_psat_pa,
    _wall_deposition_driving_pressure_pa,
    cold_spot_diagnostic,
)
from simulator.condensation_routing import DESIGNATED_STAGE, designated_stage_number
from simulator.optimize.physics import PhysicsConstraintSet
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
    assert dilute["upstream_hot_wall_refused"] is False
    assert dilute["has_cold_spot"] is False
    assert hot["has_upstream_hot_wall_violation"] is False
    assert hot["upstream_hot_wall_refused"] is False
    assert hot["has_cold_spot"] is False


def test_na_500_c_alkali_duty_is_not_a_hot_wall_violation():
    """b-282: Na at 500 C on 1 mbar alkali duty is undersaturated.

    CLAUDE.md §4 / docs/concepts.md: Na bake-off T_wall,min is 486 C.
    P_sat(Na, 773.15 K) is about 162 Pa, so 100 Pa does not foul. The
    uniform 1400 C floor is what flags this wall.
    """

    na_psat, refused = _try_antoine_psat_pa("Na", 500.0 + CELSIUS_TO_KELVIN_OFFSET)
    assert refused is False
    assert na_psat > MILLIBAR_BAKEOFF_PA

    diagnostic = cold_spot_diagnostic(
        [_upstream_segment("stage_4", 500.0)],
        {"Na": 1.0},
        upstream_hot_wall_min_C=1400.0,
        species_partial_pressures_pa={"Na": MILLIBAR_BAKEOFF_PA},
    )

    assert diagnostic["has_upstream_hot_wall_violation"] is False
    assert diagnostic["upstream_hot_wall_refused"] is False
    assert diagnostic["upstream_hot_wall_findings"] == []
    assert diagnostic["upstream_hot_wall_refusals"] == []
    # 773.15 K is below the Na pure-component fit certified for 924–1118 K.
    # The undersaturated comparison is the best estimate; the fit is not
    # an unqualified clear (d-367).
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
    assert records[0]["valid_range_K"] == pytest.approx((924.0, 1118.0))


def test_species_change_and_low_wall_uniform_minimum():
    """Na millibar at 1400 C is undersaturated; 1300 C Fe still exceeds P_sat."""

    na_psat, refused = _try_antoine_psat_pa("Na", WALL_1400_K)
    assert refused is False
    assert na_psat > MILLIBAR_BAKEOFF_PA

    na = cold_spot_diagnostic(
        [_upstream_segment("stage_4", 1400.0)],
        {"Na": 1.0},
        species_partial_pressures_pa={"Na": MILLIBAR_BAKEOFF_PA},
    )
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


def test_sio_reactive_wall_saturation_backstop_unchanged():
    """D04 / M30: diagnostic must not retune SiO reactive deposition P_sat."""

    sio_psat, refused = _try_antoine_psat_pa("SiO", WALL_1400_K)
    assert refused is True
    assert sio_psat is None

    notice: dict[str, object] = {}
    driving = _wall_deposition_driving_pressure_pa(
        "SiO",
        MILLIBAR_BAKEOFF_PA,
        WALL_1400_K,
        diagnostic_out=notice,
    )
    assert driving == pytest.approx(MILLIBAR_BAKEOFF_PA)
    assert notice["wall_saturation_pressure_pa"] == 0.0
    assert notice["wall_saturation_pressure_status"] == "reactive_product_backstop"

    sio_only = cold_spot_diagnostic(
        [_upstream_segment("stage_3", 1400.0)],
        {"SiO": 1.0},
        species_partial_pressures_pa={"SiO": MILLIBAR_BAKEOFF_PA},
    )
    assert sio_only["has_upstream_hot_wall_violation"] is False
    assert sio_only["upstream_hot_wall_refused"] is True
    assert sio_only["upstream_hot_wall_refusals"][0]["refusal_type"] == (
        "WallSaturationPressureRefusal"
    )


def test_sio_at_1300_c_refuses_unchecked_bakeoff_instead_of_clearing():
    """SiO at 1300 C and 100 Pa is neither a cold spot nor a silent clear.

    1300 C is above the 1050 C landing setpoint. Wall P_sat refuses, so
    the millibar bake-off requirement is a typed refusal of that quantity.
    It is not a supersaturation finding: refused P_sat is not P_sat = 0.
    """

    diagnostic = cold_spot_diagnostic(
        [_upstream_segment("stage_3", 1300.0)],
        {"SiO": 1.0},
        species_partial_pressures_pa={"SiO": MILLIBAR_BAKEOFF_PA},
    )

    assert diagnostic["has_cold_spot"] is False
    assert diagnostic["has_upstream_hot_wall_violation"] is False
    assert diagnostic["upstream_hot_wall_findings"] == []
    assert diagnostic["upstream_hot_wall_refused"] is True
    refusal = diagnostic["upstream_hot_wall_refusals"][0]
    assert refusal["status"] == "refused"
    assert refusal["output_status"] == "refused"
    assert refusal["quantity"] == "upstream_hot_wall"
    assert refusal["species"] == "SiO"
    assert refusal["refusal_type"] == "WallSaturationPressureRefusal"
    assert refusal["wall_temperature_C"] == 1300.0
    assert "wall_saturation_pressure_refused" in refusal["reason"]


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

    diagnostic = cold_spot_diagnostic(
        [_upstream_segment("stage_1", 1300.0)],
        {"Fe": 1.0},
        species_partial_pressures_pa=partials,
    )

    assert diagnostic["has_upstream_hot_wall_violation"] is False
    assert diagnostic["upstream_hot_wall_refused"] is True
    refusal = diagnostic["upstream_hot_wall_refusals"][0]
    assert refusal["refusal_type"] == "DepositionInputRefusal"
    assert refusal["parameter"] == "species_partial_pressure_pa"
    assert refusal["reason"] == reason
    assert refusal["species"] == "Fe"


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
    return cold_spot_diagnostic(
        [_upstream_segment(f"stage_{stage}", wall_C)],
        {species: 1.0},
        species_partial_pressures_pa=_hot_wall_partial_pressures(
            species, pressure_case
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

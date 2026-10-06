"""One condensation-onset derivation for the trace vapour carriers.

Expected temperatures come from the JANAF Pb 1-bar fugacity mark and from
inverting ``reaction_equilibrium_constant`` on the source rail. They are
not a second copy of the bisection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import simulator.condensation as condensation
from simulator.condensation import (
    CONDENSATION_TEMPS_C,
    CondensationModel,
    TraceVapourCondensationOnset,
    _landing_stages_for_onset,
    trace_vapour_condensation_onset,
    trace_vapour_saturation_pressure_pa,
)
from simulator.condensation_routing import DESIGNATED_STAGE, designated_stage_number
from simulator.state import CondensationTrain, EvaporationFlux, MeltState
from simulator.vapour_rail.source_rail import STANDARD_PRESSURE_PA


_JANAF_PB_1BAR_BOILING_K = 2019.022


def test_one_function_derives_the_onset_and_the_landing_stage() -> None:
    source = Path(trace_vapour_condensation_onset.__code__.co_filename).read_text(
        encoding="utf-8"
    )
    assert source.count("def trace_vapour_condensation_onset(") == 1
    assert source.count("def _landing_stages_for_onset(") == 1
    assert "def first_stage_below" not in source
    assert "def dewpoint" not in source
    for species in ("Ga", "In", "Pb", "GeO", "SnO", "Rb", "Cs", "BO2", "VO2", "Li"):
        assert species not in DESIGNATED_STAGE


def test_designated_species_keep_the_declared_temperature(monkeypatch) -> None:
    def rail_must_not_load():
        raise AssertionError("declared routing loaded the source rail")

    monkeypatch.setattr(
        "simulator.vapour_rail.source_rail.load_source_rail",
        rail_must_not_load,
    )
    iron = trace_vapour_condensation_onset("Fe", 100.0)
    assert iron.method == "declared_routing_temperature"
    assert iron.status == "declared"
    assert iron.temperature_C == pytest.approx(CONDENSATION_TEMPS_C["Fe"])
    assert iron.landing_stage_number == designated_stage_number("Fe")
    assert iron.wall_landing_stage_number == 1
    assert iron.disposition == "designated"
    calcium = trace_vapour_condensation_onset("Ca", 100.0)
    assert calcium.temperature_C == pytest.approx(CONDENSATION_TEMPS_C["Ca"])
    assert calcium.landing_stage_number is None
    assert calcium.wall_landing_stage_number is None
    assert calcium.hot_train_applicability == "declared_no_wall_stage"


def test_pb_onset_matches_the_janaf_1_bar_boiling_point() -> None:
    """JANAF Pb-003 marks liquid <-> ideal gas at 1 bar and 2019.022 K."""

    onset = trace_vapour_condensation_onset("Pb", STANDARD_PRESSURE_PA)
    assert onset.method == "thermo_saturation"
    assert onset.source_id == "nist-janaf-4th"
    assert onset.receiving_phase == "Pb(l)"
    assert onset.temperature_K == pytest.approx(_JANAF_PB_1BAR_BOILING_K, abs=0.05)
    assert onset.temperature_C == pytest.approx(
        onset.temperature_K - 273.15, abs=1e-9
    )
    assert onset.landing_stage_number == 1
    assert onset.hot_train_applicability == "condenses_upstream_of_train"


def test_onset_inside_the_hot_duct_is_upstream_of_the_train() -> None:
    """Cu at 100 Pa sits inside the stage-0 band and still lands on stage 1."""

    stages = CondensationTrain.create_default().stages
    hot_duct = next(stage for stage in stages if stage.stage_number == 0)
    onset = trace_vapour_condensation_onset("Cu", 100.0, stages=stages)
    assert onset.status == "ok"
    assert onset.temperature_C >= hot_duct.temp_range_C[0]
    assert onset.landing_stage_number == 1
    assert onset.hot_train_applicability == "condenses_upstream_of_train"
    gallium = trace_vapour_condensation_onset("Ga", 100.0, stages=stages)
    assert gallium.temperature_C < hot_duct.temp_range_C[0]
    assert gallium.hot_train_applicability == "applicable"


def test_onset_inverts_the_rail_equilibrium_constant() -> None:
    """The onset inverts the production saturation pressure, not a second picker."""

    temperature_K = 1500.0
    pressure_pa = trace_vapour_saturation_pressure_pa("Pb", temperature_K)
    assert pressure_pa is not None and pressure_pa > 0.0
    onset = trace_vapour_condensation_onset("Pb", pressure_pa)
    assert onset.status == "ok"
    assert onset.receiving_phase == "Pb(l)"
    assert onset.temperature_K == pytest.approx(temperature_K, abs=1e-3)


def test_cs_and_rb_match_janaf_printed_rows_at_1_pa() -> None:
    """JANAF log Kf rows at 1 Pa. The code uses NASA Glenn, so the check is cross-compilation.

    Cs-005: -5.377 at 400 K and -4.318 at 450 K interpolates to 416.5 K.
    Rb-005: -5.793 at 400 K and -4.671 at 450 K interpolates to 434.1 K.
    """

    cesium = trace_vapour_condensation_onset("Cs", 1.0)
    rubidium = trace_vapour_condensation_onset("Rb", 1.0)
    assert cesium.temperature_K == pytest.approx(416.5, abs=1.0)
    assert rubidium.temperature_K == pytest.approx(434.1, abs=1.0)
    assert cesium.receiving_phase == "Cs(L)"
    assert rubidium.receiving_phase == "Rb(L)"


def test_geo_onset_is_the_disproportionation() -> None:
    """½ Ge + ½ GeO2 on the NASA compilation, about 779 °C at 100 Pa."""

    onset = trace_vapour_condensation_onset("GeO", 100.0)
    assert onset.status == "ok"
    assert onset.source_id == "nasa-glenn"
    assert onset.receiving_phase == "0.5*Ge(cr)+0.5*GeO2(II)"
    assert onset.temperature_C == pytest.approx(778.6, abs=0.5)
    assert onset.landing_stage_number == 4
    assert onset.wall_landing_stage_number == 4


def test_rb_and_cs_are_flagged_uncaptured_at_both_report_pressures() -> None:
    stage_4 = next(
        stage
        for stage in CondensationTrain.create_default().stages
        if stage.stage_number == 4
    )
    lower_C, _upper_C = stage_4.temp_range_C
    for species in ("Rb", "Cs"):
        for pressure_pa in (100.0, 1.0):
            onset = trace_vapour_condensation_onset(species, pressure_pa)
            assert isinstance(onset, TraceVapourCondensationOnset)
            assert onset.status == "ok"
            assert onset.temperature_C < lower_C
            assert onset.landing_stage_number is None
            assert onset.wall_landing_stage_number is not None
            assert onset.disposition == "flagged_uncaptured_condensable"
            assert onset.hot_train_applicability == "uncaptured_condensable"
            capture, wall = _landing_stages_for_onset(
                onset.temperature_C,
                CondensationTrain.create_default().stages,
            )
            assert capture == onset.landing_stage_number
            assert wall == onset.wall_landing_stage_number


def test_typed_gaps_name_the_receiver_they_actually_lack() -> None:
    dioxide = trace_vapour_condensation_onset("BO2", 100.0)
    assert dioxide.status == "receiving_phase_requires_local_pO2"
    assert dioxide.receiving_phase == "B2O3"
    assert dioxide.temperature_K is None
    assert dioxide.disposition == "unavailable"
    assert "O2" in dioxide.detail

    dimer = trace_vapour_condensation_onset("B2", 100.0)
    assert dimer.status == "pressure_outside_saturation_domain"
    assert dimer.receiving_phase == "2*B"
    assert dimer.temperature_K is None
    assert "B2O3" in dimer.detail

    dioxide_vanadium = trace_vapour_condensation_onset("VO2", 100.0)
    assert dioxide_vanadium.status == "ok"
    assert dioxide_vanadium.receiving_phase == "0.5*V2O4(l)"
    assert dioxide_vanadium.landing_stage_number == 1


def test_a_foreign_channel_source_is_flagged(monkeypatch) -> None:
    from simulator.condensation import _trace_vapour_carrier_sources

    def foreign() -> dict[str, str | None]:
        return {"Pb": "not-a-compilation"}

    monkeypatch.setattr(
        "simulator.condensation._trace_vapour_carrier_sources",
        foreign,
    )
    _trace_vapour_carrier_sources.cache_clear()
    onset = trace_vapour_condensation_onset("Pb", 100.0)
    assert onset.status == "ok"
    assert onset.source_id == "nist-janaf-4th"
    assert "source_differs_from_channel" in onset.detail


def test_non_positive_pressure_does_not_invent_an_onset() -> None:
    onset = trace_vapour_condensation_onset("Rb", 0.0)
    assert onset.status == "inputs_required"
    assert onset.temperature_K is None
    assert onset.disposition == "unavailable"


def test_onset_is_reused_inside_one_route_tick(monkeypatch) -> None:
    stages = CondensationTrain.create_default().stages
    calls = {"n": 0}
    original = condensation._thermo_saturation_onset

    def counting(species, pressure_pa, *, stages):
        calls["n"] += 1
        return original(species, pressure_pa, stages=stages)

    monkeypatch.setattr(condensation, "_thermo_saturation_onset", counting)

    outside_a = trace_vapour_condensation_onset("Rb", 100.0, stages=stages)
    outside_b = trace_vapour_condensation_onset("Rb", 100.0, stages=stages)
    assert calls["n"] == 2
    assert outside_a is not outside_b

    calls["n"] = 0
    condensation._push_trace_onset_tick()
    try:
        first = trace_vapour_condensation_onset("Rb", 100.0, stages=stages)
        second = trace_vapour_condensation_onset("Rb", 100.0, stages=stages)
        # Six significant figures: 100.0001 Pa is the same key as 100 Pa.
        rounded = trace_vapour_condensation_onset(
            "Rb", 100.0001, stages=stages
        )
        other = trace_vapour_condensation_onset("Rb", 1.0, stages=stages)
    finally:
        condensation._pop_trace_onset_tick()
    assert calls["n"] == 2
    assert first is second is rounded
    assert other is not first

    calls["n"] = 0
    model = CondensationModel(CondensationTrain.create_default())
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        species_partial_pressures_mbar={"Rb": 1.0},
        pipe_diameter_m=0.12,
        gas_temperature_C=1700.0,
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in model.train.stages
        },
    )
    flux = EvaporationFlux(species_kg_hr={"Rb": 0.4})
    flux.update_totals()
    model.route(flux, MeltState())
    assert calls["n"] == 1
    model.route(flux, MeltState())
    assert calls["n"] == 2

"""One condensation-onset derivation for the trace vapour carriers.

Expected temperatures come from the JANAF Pb 1-bar fugacity mark and from
inverting ``reaction_equilibrium_constant`` on the source rail. They are
not a second copy of the bisection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from simulator.condensation import (
    CONDENSATION_TEMPS_C,
    TraceVapourCondensationOnset,
    _landing_stages_for_onset,
    trace_vapour_condensation_onset,
)
from simulator.condensation_routing import DESIGNATED_STAGE, designated_stage_number
from simulator.state import CondensationTrain
from simulator.vapour_rail.nasa_cea import reaction_equilibrium_constant
from simulator.vapour_rail.source_rail import (
    STANDARD_PRESSURE_PA,
    load_source_rail,
)


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
    assert onset.receiving_phase == "l"
    assert onset.temperature_K == pytest.approx(_JANAF_PB_1BAR_BOILING_K, abs=0.05)
    assert onset.temperature_C == pytest.approx(
        onset.temperature_K - 273.15, abs=1e-9
    )
    assert onset.landing_stage_number == 1
    assert onset.hot_train_applicability == "applicable"


def test_onset_inverts_the_rail_equilibrium_constant() -> None:
    temperature_K = 1500.0
    rail = load_source_rail()
    gas = next(
        record
        for record in rail.records_for("Pb", "gas")
        if record.source_id == "nist-janaf-4th"
    )
    liquid = next(
        record
        for record in rail.records_for("Pb", "condensed_liquid")
        if record.source_id == "nist-janaf-4th"
    )
    solid = next(
        record
        for record in rail.records_for("Pb", "condensed_solid")
        if record.source_id == "nist-janaf-4th"
        and record.T_min_K <= temperature_K <= record.T_max_K
    )
    gas_state = gas.thermo.evaluate(temperature_K)
    liquid_state = liquid.thermo.evaluate(temperature_K)
    solid_state = solid.thermo.evaluate(temperature_K)
    condensed_state = (
        liquid_state
        if liquid_state.g_over_RT <= solid_state.g_over_RT
        else solid_state
    )
    k_eq = reaction_equilibrium_constant(
        ((1.0, gas_state), (-1.0, condensed_state)),
        T_K=temperature_K,
    )
    onset = trace_vapour_condensation_onset(
        "Pb",
        STANDARD_PRESSURE_PA * k_eq,
    )
    assert onset.status == "ok"
    assert onset.temperature_K == pytest.approx(temperature_K, abs=1e-3)


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


def test_missing_receiving_phase_is_a_visible_gap() -> None:
    for species in ("GeO", "BO2", "VO2"):
        onset = trace_vapour_condensation_onset(species, 100.0)
        assert onset.status == "no_receiving_condensed_phase"
        assert onset.temperature_K is None
        assert onset.wall_landing_stage_number is None
        assert onset.disposition == "unavailable"
        assert onset.detail


def test_non_positive_pressure_does_not_invent_an_onset() -> None:
    onset = trace_vapour_condensation_onset("Rb", 0.0)
    assert onset.status == "inputs_required"
    assert onset.temperature_K is None
    assert onset.disposition == "unavailable"

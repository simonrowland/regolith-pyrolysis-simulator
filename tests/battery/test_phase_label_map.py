"""Engine phase labels classify; they are not guessed onto a role."""

from __future__ import annotations

from simulator.battery.enums import PhaseRole
from simulator.battery.score import _select_phase_composition
from simulator.melt_backend.base import EquilibriumResult
from simulator.melt_backend.phase_composition import (
    LIQUID_ROLE_LABELS,
    UNMAPPED_CRYSTAL_LABELS,
    PhaseLabelClass,
    classify_phase_label,
)


def test_label_table_keeps_liquids_and_refuses_to_guess_crystals_or_g():
    for label in sorted(LIQUID_ROLE_LABELS):
        klass, reason = classify_phase_label(label)
        assert klass is PhaseLabelClass.LIQUID
        assert reason is None
    for label in sorted(UNMAPPED_CRYSTAL_LABELS):
        klass, reason = classify_phase_label(label)
        assert klass is PhaseLabelClass.UNMAPPED
        assert reason == "no_battery_role"
    klass, reason = classify_phase_label("g")
    assert klass is PhaseLabelClass.AMBIGUOUS
    assert reason == "garnet_or_gas"
    klass, reason = classify_phase_label("q")
    assert klass is PhaseLabelClass.UNMAPPED
    assert reason == "no_battery_role"


def test_selector_matches_liq_and_does_not_call_g_glass():
    melt = EquilibriumResult(
        liquid_fraction=1.0,
        phase_compositions={"liq": {"SiO2": 55.0}, "ol": {"SiO2": 40.0}},
    )
    selected, issue = _select_phase_composition(melt, PhaseRole.MELT)
    assert issue is None
    assert selected == {"SiO2": 55.0}

    glass = EquilibriumResult(
        liquid_fraction=0.0,
        phase_compositions={"g": {"SiO2": 41.0}},
    )
    selected, issue = _select_phase_composition(glass, PhaseRole.GLASS)
    assert selected is None
    assert issue == "ambiguous_phase_label:garnet_or_gas"

    crystal_only = EquilibriumResult(
        liquid_fraction=0.0,
        phase_compositions={"ol": {"MgO": 50.0}},
    )
    selected, issue = _select_phase_composition(crystal_only, PhaseRole.MELT)
    assert selected is None
    assert issue == "phase_not_in_assemblage"


def test_two_liquid_names_stay_ambiguous():
    result = EquilibriumResult(
        liquid_fraction=1.0,
        phase_compositions={
            "liq": {"SiO2": 40.0},
            "liquid": {"SiO2": 70.0},
        },
    )
    selected, issue = _select_phase_composition(result, PhaseRole.MELT)
    assert selected is None
    assert issue == "ambiguous_phase"

"""An unsupported print form names only fields that are present and unusable.

An absent sibling is missing evidence, not an unsupported form. Route
selection is unchanged: the gap still exists, it just stops naming a field
that was never there. Dropped printed species stay on their own branch.
"""

from dataclasses import replace
from decimal import Decimal

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import wt_pct_to_mole_fraction
from simulator.battery.records import Located, Sample, Value
from simulator.battery.waypoints import (
    GapReason,
    ReadinessStatus,
    charge_moles_by_species,
    consumer_readiness,
    normalized_composition,
)
from tests.battery import factories
from tests.battery.test_waypoints import _bench


_PRINTED = "experiment.sample.printed_composition"
_INITIAL = "experiment.sample.initial_composition"


def _categorical_print(text: str = "Fe-Mn master alloy"):
    return factories.located(Value(ValueKind.CATEGORICAL, categorical=text))


def test_unsupported_print_form_omits_an_absent_sibling() -> None:
    experiment = replace(
        factories.kems_experiment(),
        sample=Sample(printed_composition=_categorical_print()),
    )
    result = normalized_composition(experiment, _bench())
    assert result.selected is None
    assert result.absence is not None
    assert result.absence.reason is GapReason.UNSUPPORTED_PRINT_FORM
    assert result.absence.missing == (_PRINTED,)
    assert _INITIAL not in result.absence.missing
    engine = next(
        item
        for item in consumer_readiness(experiment, _bench())
        if item.consumer == "engine_point" and item.engine == "internal-analytical"
    )
    assert engine.status is ReadinessStatus.GAP
    gap = next(gap for gap in engine.gaps if gap.waypoint == "normalized_composition")
    assert gap.reason is GapReason.UNSUPPORTED_PRINT_FORM
    assert gap.missing == (_PRINTED,)


def test_typed_printed_composition_is_unwrapped_for_readiness() -> None:
    typed = {
        "basis": "printed_oxides",
        "amount_basis": "mass_percent",
        "components": [["SiO2", "60"], ["MgO", "40"]],
    }
    experiment = replace(
        factories.kems_experiment(),
        sample=Sample(printed_composition=factories.located(typed)),
    )

    result = normalized_composition(experiment, _bench())

    assert result.selected is not None
    assert set(result.selected.value) == {"SiO2", "MgO"}
    assert sum(result.selected.value.values()) == 1


def test_ambiguous_mass_percent_withholds_engine_routes() -> None:
    typed = {
        "basis": "printed_oxides",
        "amount_basis": "mass_percent",
        "components": [["SiO2", "60"], ["Cl", "40"]],
    }
    sample = Sample(
        mass_kg=factories.located(Value.point_of("0.001")),
        printed_composition=factories.located(typed),
    )
    experiment = replace(factories.kems_experiment(), sample=sample)

    normalized = normalized_composition(experiment, _bench())
    charge = charge_moles_by_species(experiment, _bench())

    assert normalized.selected is None
    assert normalized.absence is not None
    assert normalized.absence.reason is GapReason.UNSUPPORTED_PRINT_FORM
    assert not charge
    assert charge.absence is not None


def test_both_absent_compositions_stay_missing_evidence() -> None:
    experiment = replace(factories.kems_experiment(), sample=Sample())
    result = normalized_composition(experiment, _bench())
    assert result.selected is None
    assert result.absence is not None
    assert result.absence.reason is GapReason.MISSING_EVIDENCE
    assert result.absence.missing == (_PRINTED, _INITIAL)


def test_both_present_unusable_forms_are_both_named() -> None:
    class _WeightPercent:
        amount_basis = "weight_percent"

    experiment = replace(
        factories.kems_experiment(),
        sample=Sample(
            printed_composition=_categorical_print("basalt"),
            initial_composition=factories.located(_WeightPercent()),
        ),
    )
    result = normalized_composition(experiment, _bench())
    assert result.selected is None
    assert result.absence is not None
    assert result.absence.reason is GapReason.UNSUPPORTED_PRINT_FORM
    assert result.absence.missing == (_PRINTED, _INITIAL)


def test_dropped_printed_species_still_names_only_the_species() -> None:
    printed = {"SiO2": Decimal("50"), "MgO": Decimal("30"), "FeOT": Decimal("20")}
    wt = {"SiO2": Decimal("50"), "MgO": Decimal("30")}
    experiment = replace(
        factories.kems_experiment(),
        sample=Sample(
            printed_composition=factories.located(printed),
            initial_composition=Located(
                factories.State.of(wt_pct_to_mole_fraction(wt)),
                factories.loc(),
            ),
        ),
    )
    result = normalized_composition(experiment, _bench())
    assert result.selected is None
    assert result.absence is not None
    assert result.absence.reason is GapReason.UNSUPPORTED_PRINT_FORM
    assert result.absence.missing == (f"{_PRINTED}.FeOT",)

from __future__ import annotations

from types import MappingProxyType

import pytest

from simulator.battery.enums import (
    Engine,
    NoticeKind,
    Quantity,
    Rail,
    ResidualStatus,
)
from simulator.battery.score import load_score_context, quantity_token, score_store


RICHTER_2007 = "kems-010-richter-2007"
RICHTER_2011 = (
    "richter-mendybaev-christensen-etal-2011-olivine-chondrule-evaporation"
)


@pytest.fixture(scope="module")
def richter_2007_context():
    return load_score_context(sources=(RICHTER_2007,))


@pytest.fixture(scope="module")
def richter_2011_context():
    return load_score_context(sources=(RICHTER_2011,))


def _reference(context, source_id: str, experiment_suffix: str, formula: str):
    rows = [
        observation
        for observation in context.observations.values()
        if observation.source_id == source_id
        and observation.experiment_id.rsplit("::", 1)[-1] == experiment_suffix
        and quantity_token(observation.identity)
        is Quantity.RESIDUE_COMPONENT_COMPOSITION
        and observation.identity.species.formula == formula
    ]
    assert len(rows) == 1
    return rows[0]


def _score_one(monkeypatch, context, target):
    import simulator.battery.score as score

    original_filter = score._observation_matches_run_filters

    def only_target(context, observation, **kwargs):
        return (
            observation.observation_id == target.observation_id
            and original_filter(context, observation, **kwargs)
        )

    monkeypatch.setattr(score, "_observation_matches_run_filters", only_target)
    residuals, _candidates = score_store(
        context,
        engines=(Engine.OPENIMCC,),
        rail=Rail.RESIDUE_COMPOSITION,
    )
    matches = [
        residual for residual in residuals
        if residual.reference == target.observation_id
    ]
    assert len(matches) == 1
    return matches[0]


def test_richter_2007_scores_through_score_store_with_area_and_pressure_flags(
    monkeypatch: pytest.MonkeyPatch,
    richter_2007_context,
) -> None:
    import simulator.battery.residue as residue

    target = _reference(richter_2007_context, RICHTER_2007, "cai-r3-10", "MgO")
    captured: dict[str, object] = {}
    original_predict = residue._predict_langmuir_residue_cohort

    def capture_predictions(*args, **kwargs):
        predictions = original_predict(*args, **kwargs)
        captured["predictions"] = predictions
        return predictions

    monkeypatch.setattr(
        residue, "_predict_langmuir_residue_cohort", capture_predictions
    )
    residual = _score_one(monkeypatch, richter_2007_context, target)

    assert residual.status is not ResidualStatus.REFUSED
    assert residual.numeric is not None
    reasons = {
        notice.reason
        for notice in residual.notices
        if notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
    }
    assert "area_evolution_unreported" in reasons
    assert "total_pressure_upper_bound_used" in reasons

    predictions = captured["predictions"]
    primary = next(
        row
        for row in predictions
        if row.experiment_id.rsplit("::", 1)[-1] == "cai-r3-10"
        and row.alpha_arm == "alpha_common_unity_sensitivity"
    )
    diagnostics = primary.provenance["area_diagnostics"]
    assert set(diagnostics) == {"sphere_constant", "sphere_shrinking"}
    for geometry in diagnostics.values():
        assert geometry["predicted_final_area_mm2"] > 0.0
        assert geometry["printed_final_area_mm2"] > 0.0


def test_richter_2007_zero_time_charge_is_typed_as_starting_bulk(
    monkeypatch: pytest.MonkeyPatch,
    richter_2007_context,
) -> None:
    target = _reference(richter_2007_context, RICHTER_2007, "cai-r3-12", "SiO2")
    residual = _score_one(monkeypatch, richter_2007_context, target)

    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.detail["reason"] == "starting_bulk_not_residue"


def test_richter_2007_missing_initial_area_is_a_typed_cell_refusal(
    monkeypatch: pytest.MonkeyPatch,
    richter_2007_context,
) -> None:
    target = _reference(richter_2007_context, RICHTER_2007, "cai-r-13", "MgO")
    residual = _score_one(monkeypatch, richter_2007_context, target)

    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.detail["reason"] == "initial_area_unreported"


def test_richter_2011_missing_mass_is_typed_per_cell(
    monkeypatch: pytest.MonkeyPatch,
    richter_2011_context,
) -> None:
    target = _reference(
        richter_2011_context, RICHTER_2011, "richter-2011-iiab1-1", "MgO"
    )
    residual = _score_one(monkeypatch, richter_2011_context, target)

    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.detail["reason"] == "initial_mass_unreported"


def test_removing_a_langmuir_cohort_entry_restores_quantity_not_predicted(
    monkeypatch: pytest.MonkeyPatch,
    richter_2007_context,
) -> None:
    import simulator.battery.score as score

    target = _reference(richter_2007_context, RICHTER_2007, "cai-r3-10", "MgO")
    monkeypatch.setattr(
        score,
        "_LANGMUIR_RESIDUE_COHORTS",
        MappingProxyType(
            {
                source_id: spec
                for source_id, spec in score._LANGMUIR_RESIDUE_COHORTS.items()
                if source_id != RICHTER_2007
            }
        ),
    )
    residual = _score_one(monkeypatch, richter_2007_context, target)

    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.detail["reason"] == "quantity_not_predicted"

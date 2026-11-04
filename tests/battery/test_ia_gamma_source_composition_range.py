from __future__ import annotations

from decimal import Decimal

from simulator.battery.enums import Engine, ExecutionState
from simulator.battery.generators.bench import activity_request_for_engine
from simulator.battery.score import predict_with_engine
from simulator.chemistry.melt_activity import (
    MELT_OXIDE_ACTIVITY_COEFFICIENTS,
    P2O5_ACTIVITY_COEFFICIENT,
    melt_oxide_activity,
)
from tests.battery.test_melt_activity_requirements import _case, _composition


_NOTICE_ID = "ia_constant_gamma_outside_source_composition_range"


def _score_activity(
    oxide: str,
    composition: dict[str, str],
    temperature_K: str,
    *,
    baseline_value: str | None = None,
):
    experiment, bench, observation = _case(
        composition=_composition(*composition.items()),
        formula=oxide,
        temperature=Decimal(temperature_K),
    )
    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL,
        observation,
        experiment=experiment,
        bench=bench,
        handles={},
        isolated=False,
    )
    assert prediction.execution.state is ExecutionState.PRODUCED
    if baseline_value is None:
        request = activity_request_for_engine(
            experiment, observation, "internal-analytical"
        )
        assert request is not None and request.payload is not None
        expected = melt_oxide_activity(
            oxide,
            request.payload["composition_mol"],
            temperature_K=float(temperature_K),
        )
        assert expected is not None
        assert prediction.value == Decimal(
            str(expected.thermodynamic_parent_activity())
        )
    else:
        assert prediction.value == Decimal(baseline_value)
    return prediction


def _source_range_notices(prediction):
    return tuple(
        notice for notice in prediction.notices if notice.reason.startswith(_NOTICE_ID)
    )


def test_kambayashi_kato_mgo_binary_row_is_flagged_without_changing_value() -> None:
    # Kambayashi & Kato 1984 Table 2, p. 246: x(SiO2)=0.795, x(MgO)=0.205,
    # 1973 K. This binary composition is outside the cited CMAS basicity band.
    prediction = _score_activity(
        "MgO",
        {"SiO2": "0.795", "MgO": "0.205"},
        "1973",
        # Golden output from the unmodified 0ea42527e scorer.
        baseline_value="0.009960556690592466",
    )

    notices = _source_range_notices(prediction)
    assert len(notices) == 1
    reason = notices[0].reason
    assert "oxide=MgO" in reason
    assert "CMAS" in reason and "0.55" in reason and "0.65" in reason
    assert "Beckett 2002" in reason


def test_cmas_midrange_row_is_not_flagged() -> None:
    prediction = _score_activity(
        "MgO",
        {"CaO": "0.25", "MgO": "0.25", "Al2O3": "0.1", "SiO2": "0.4"},
        "1873",
        # Golden output from the unmodified 0ea42527e scorer.
        baseline_value="0.011042745776710052",
    )

    assert _source_range_notices(prediction) == ()


def test_unrecorded_source_composition_range_is_still_flagged() -> None:
    prediction = _score_activity(
        "K2O", {"K2O": "0.2", "SiO2": "0.8"}, "1500"
    )

    notices = _source_range_notices(prediction)
    assert len(notices) == 1
    reason = notices[0].reason
    assert "oxide=K2O" in reason
    assert "unrecorded" in reason.lower()
    assert "DeMaria et al. 1971" in reason


def test_every_declared_gamma_has_source_composition_range_metadata() -> None:
    coefficients = (
        *MELT_OXIDE_ACTIVITY_COEFFICIENTS.values(),
        P2O5_ACTIVITY_COEFFICIENT,
    )
    assert all(coeff.source_composition_range for coeff in coefficients)
    assert all(coeff.source_composition_range.citation for coeff in coefficients)

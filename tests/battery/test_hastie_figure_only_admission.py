"""Hastie Fig. 4 figure-only KEMS evidence keeps its digitization band."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import (
    AdmissionStatus,
    Engine,
    EvidenceClass,
    NoticeKind,
    Phase,
    Quantity,
    RefusalReason,
    UncertaintyKind,
    ValueKind,
)
from simulator.battery.identity import quantity_token
from simulator.battery.migrate import load_migrated_store
from simulator.battery.records import Uncertainty
from simulator.battery.score import (
    _evidence_class_notice,
    _printed_uncertainty_band,
    predict_with_engine,
)
from simulator.battery.validity import effusion_regime_unverified


SOURCE_ID = "kems-020-hastie-1981-nbsir"
ROOT = Path(__file__).resolve().parents[2]


def _hastie_store():
    return load_migrated_store(ROOT, sources=(SOURCE_ID,))


def _fig4_sio_points(observations):
    return tuple(
        sorted(
            (
                observation
                for observation in observations.values()
                if observation.source_id == SOURCE_ID
                and observation.locator is not None
                and observation.locator.figure == "4"
                and observation.value.kind is ValueKind.POINT
                and quantity_token(observation.identity) is Quantity.P_PARTIAL
                and observation.identity.species is not None
                and observation.identity.species.formula == "SiO"
            ),
            key=lambda observation: observation.observation_id,
        )
    )


def test_every_hastie_fig4_sio_point_is_admitted_with_its_figure_band() -> None:
    _works, experiments, observations = _hastie_store()
    rows = _fig4_sio_points(observations)

    assert len(rows) == 6
    for row in rows:
        assert row.admission.status is AdmissionStatus.ADMITTED
        assert row.evidence.class_.is_value
        assert row.evidence.class_.value is EvidenceClass.FIGURE_ONLY
        assert row.identity.species.phase.is_value
        assert row.identity.species.phase.value is Phase.G

        experiment = experiments[row.experiment_id]
        method = experiment.method.value if experiment.method.is_value else None
        resolution = _printed_uncertainty_band(
            Quantity.P_PARTIAL,
            row.uncertainty,
            row.value.point,
            source_observation=row,
            method=method,
        )
        assert resolution.refusal is None
        assert resolution.band is not None
        assert resolution.band.value == Decimal("0.10")
        assert resolution.band.unit == "dimensionless"
        assert resolution.band.band_basis == "row_printed"

        notice = _evidence_class_notice(Quantity.P_PARTIAL, row)
        assert notice.kind is NoticeKind.FIGURE_ONLY
        assert notice.reason == "figure_only"
        assert notice.origin == row.observation_id


def test_figure_only_without_digitization_uncertainty_has_no_zero_band() -> None:
    _works, _experiments, observations = _hastie_store()
    row = _fig4_sio_points(observations)[0]
    missing_uncertainty = replace(
        row, uncertainty=Uncertainty(kind=UncertaintyKind.NONE)
    )

    resolution = _printed_uncertainty_band(
        Quantity.P_PARTIAL,
        missing_uncertainty.uncertainty,
        missing_uncertainty.value.point,
        source_observation=missing_uncertainty,
    )

    assert resolution.band is None
    assert resolution.refusal is None
    notice = _evidence_class_notice(Quantity.P_PARTIAL, missing_uncertainty)
    assert notice.kind is NoticeKind.FIGURE_ONLY
    assert notice.origin == row.observation_id


def test_hastie_figure_rows_without_supported_inputs_keep_typed_refusals() -> None:
    _works, experiments, observations = _hastie_store()
    for row in _fig4_sio_points(observations):
        assert row.point_conditions["composition"].state.is_unknown
        experiment = experiments[row.experiment_id]
        regime = effusion_regime_unverified(
            experiment, Quantity.P_PARTIAL, observation=row
        )
        assert regime.reason is RefusalReason.EFFUSION_REGIME_UNVERIFIED

        prediction = predict_with_engine(
            Engine.INTERNAL_ANALYTICAL, row, experiment=experiment
        )
        assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
        assert (
            prediction.refusal_detail["reason"]
            == "composition_not_stated_pure_reservoir"
        )

    unsupported_figure_summaries = tuple(
        observation
        for observation in observations.values()
        if observation.source_id == SOURCE_ID
        and observation.locator is not None
        and observation.locator.figure == "4"
        and observation.value.kind is ValueKind.CATEGORICAL
        and observation.identity.quantity.is_unknown
    )
    assert len(unsupported_figure_summaries) == 3
    for row in unsupported_figure_summaries:
        prediction = predict_with_engine(
            Engine.INTERNAL_ANALYTICAL,
            row,
            experiment=experiments[row.experiment_id],
        )
        assert prediction.refusal_reason is RefusalReason.IDENTITY_UNKNOWN
        assert prediction.refusal_detail["reason"] == "quantity_unknown"

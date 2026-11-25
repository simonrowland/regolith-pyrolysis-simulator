"""Hastie method evidence stays row-specific and preserves typed refusals."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from simulator.battery.enums import (
    AdmissionStatus,
    Engine,
    EvidenceClass,
    NoticeKind,
    MethodToken,
    Phase,
    Quantity,
    RefusalReason,
    ResidualStatus,
    UncertaintyKind,
    ValueKind,
)
from simulator.battery.identity import quantity_token
from simulator.battery.records import (
    INITIAL_CHARGE_ONLY_PROXY_FLAG,
    INITIAL_CHARGE_ONLY_PROXY_NOTICE,
    State,
    Uncertainty,
)
from simulator.battery.input_provenance import PRINTED_STARTING_BULK
from tests.battery.test_migrate import _migrate_real_extract
from simulator.battery.score import (
    _evidence_class_notice,
    _missing_apparatus_fact,
    _printed_uncertainty_band,
    _unverified_apparatus_notices,
    ScoreContext,
    compile_residual,
    predict_with_engine,
)
from simulator.battery.validity import effusion_regime_unverified, run_validity_gates


SOURCE_ID = "kems-020-hastie-1981-nbsir"
ROOT = Path(__file__).resolve().parents[2]
SCORING_ENGINES = (Engine.OPENIMCC, Engine.INTERNAL_ANALYTICAL)


def _hastie_store(tmp_path):
    result = _migrate_real_extract(
        tmp_path, f"{SOURCE_ID}.yaml", write=False
    )
    return result.works, result.experiments, result.observations


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


def _printed_hastie_band(row, experiment, *, confidence_level_override=None):
    method = experiment.method.value if experiment.method.is_value else None
    return _printed_uncertainty_band(
        Quantity.P_PARTIAL,
        row.uncertainty,
        row.value.point,
        source_observation=row,
        method=method,
        confidence_level_override=confidence_level_override,
    )


def test_every_hastie_fig4_sio_point_is_admitted_with_its_figure_band(
    tmp_path,
) -> None:
    _works, experiments, observations = _hastie_store(tmp_path)
    rows = _fig4_sio_points(observations)

    assert len(rows) == 6
    for row in rows:
        assert row.admission.status is AdmissionStatus.ADMITTED
        assert row.evidence.class_.is_value
        assert row.evidence.class_.value is EvidenceClass.FIGURE_ONLY
        assert row.identity.species.phase.is_value
        assert row.identity.species.phase.value is Phase.G
        assert row.identity.composition is not None
        assert row.identity.composition.is_value
        assert (
            row.identity.composition.value.proxy_flag
            == INITIAL_CHARGE_ONLY_PROXY_FLAG
        )
        assert any(
            notice.kind is NoticeKind.SOURCE_DISAGREEMENT
            and INITIAL_CHARGE_ONLY_PROXY_NOTICE in notice.reason
            for notice in row.notices
        )
        assert row.uncertainty.kind is UncertaintyKind.PRINTED
        assert row.uncertainty.verbatim["value"] == 0.10
        assert row.uncertainty.verbatim["units"] == "dex"
        assert row.uncertainty.verbatim["scope"] == "every point in this series"
        assert row.uncertainty.verbatim["as_printed"] == "±0.10 dex"
        assert row.uncertainty.verbatim.get("confidence_level", "not_stated") == (
            "not_stated"
        )

        experiment = experiments[row.experiment_id]
        resolution = _printed_hastie_band(row, experiment)
        explicit_one_sigma = _printed_hastie_band(
            row, experiment, confidence_level_override="one_sigma"
        )
        assert resolution.refusal is None
        assert resolution.band is not None
        assert explicit_one_sigma.refusal is None
        assert explicit_one_sigma.band is not None
        assert resolution.band == explicit_one_sigma.band
        assert resolution.band.unit == "dimensionless"
        assert resolution.band.band_basis == "row_printed"

        notice = _evidence_class_notice(Quantity.P_PARTIAL, row)
        assert notice.kind is NoticeKind.FIGURE_ONLY
        assert notice.reason == "figure_only"
        assert notice.origin == row.observation_id


def test_figure_only_without_digitization_uncertainty_has_no_zero_band(
    tmp_path,
) -> None:
    _works, _experiments, observations = _hastie_store(tmp_path)
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


def test_hastie_figure_rows_without_supported_inputs_keep_typed_refusals(
    tmp_path,
) -> None:
    _works, experiments, observations = _hastie_store(tmp_path)
    for row in _fig4_sio_points(observations):
        assert row.point_conditions["composition"].state.is_value
        assert (
            row.point_conditions["composition"].state.value.proxy_flag
            == INITIAL_CHARGE_ONLY_PROXY_FLAG
        )
        experiment = experiments[row.experiment_id]
        regime = effusion_regime_unverified(
            experiment, Quantity.P_PARTIAL, observation=row
        )
        assert regime.reason is RefusalReason.EFFUSION_REGIME_UNVERIFIED

        for engine in SCORING_ENGINES:
            prediction = predict_with_engine(engine, row, experiment=experiment)
            assert prediction.refusal_reason is None, prediction.refusal_detail

    unsupported_quantity_rows = tuple(
        observation
        for observation in observations.values()
        if observation.source_id == SOURCE_ID
        and observation.identity.quantity.is_unknown
    )
    assert len(unsupported_quantity_rows) == 69
    for row in unsupported_quantity_rows:
        for engine in SCORING_ENGINES:
            prediction = predict_with_engine(
                engine,
                row,
                experiment=experiments[row.experiment_id],
            )
            assert prediction.refusal_reason is RefusalReason.IDENTITY_UNKNOWN
            assert prediction.refusal_detail["reason"] == "quantity_unknown"


def test_hastie_unknown_kms_regime_rows_still_refuse_with_their_reason(
    tmp_path,
) -> None:
    works, experiments, observations = _hastie_store(tmp_path)
    rows = tuple(
        row
        for row in observations.values()
        if row.source_id == SOURCE_ID
        and quantity_token(row.identity) is Quantity.ACTIVITY_COEFFICIENT
        and experiments[row.experiment_id].method.is_value
        and experiments[row.experiment_id].method.value
        is MethodToken.KNUDSEN_EFFUSION
    )
    assert len(rows) == 18
    context = ScoreContext(
        works=works,
        experiments=experiments,
        observations=observations,
        extract_review={SOURCE_ID: "reviewed"},
        root=ROOT,
    )

    for row in rows:
        experiment = experiments[row.experiment_id]
        gates = run_validity_gates(
            experiment,
            row,
            point_observations=tuple(observations.values()),
        )
        assert not gates.passed
        assert gates.reason is RefusalReason.EFFUSION_REGIME_UNVERIFIED
        assert gates.primary_check == "in_cell_partial_pressure_sum"
        for engine in SCORING_ENGINES:
            residual, _candidate = compile_residual(
                row,
                engine,
                context=context,
                point_observations=tuple(observations.values()),
            )
            assert residual.numeric is None
            assert residual.refusal is not None
            assert residual.refusal.reason is RefusalReason.EFFUSION_REGIME_UNVERIFIED


def test_hastie_fig5_points_use_the_printed_initial_glass_proxy(tmp_path) -> None:
    _works, _experiments, observations = _hastie_store(tmp_path)
    rows = tuple(
        row
        for row in observations.values()
        if row.source_id == SOURCE_ID
        and "fig5_glass_Na_this_work_digitized" in row.observation_id
        and quantity_token(row.identity) is Quantity.P_PARTIAL
        and row.identity.temperature_K is not None
        and row.identity.temperature_K.is_value
    )
    assert len(rows) == 6
    proxy_compositions = []

    for row in rows:
        composition = row.identity.composition
        assert composition is not None and composition.is_value
        assert composition.value.proxy_flag == INITIAL_CHARGE_ONLY_PROXY_FLAG
        assert composition.value.input_provenance == PRINTED_STARTING_BULK
        proxy_compositions.append(composition.value.components)
        point_composition = row.point_conditions["composition"]
        assert point_composition.state.is_value
        assert point_composition.state.value.proxy_flag == INITIAL_CHARGE_ONLY_PROXY_FLAG
        assert any(
            notice.kind is NoticeKind.SOURCE_DISAGREEMENT
            and INITIAL_CHARGE_ONLY_PROXY_NOTICE in notice.reason
            for notice in row.notices
        )
    assert all(components == proxy_compositions[0] for components in proxy_compositions)


def test_hastie_fig4_rows_keep_knudsen_method_but_refuse_missing_pressure_sum(
    tmp_path,
) -> None:
    works, experiments, observations = _hastie_store(tmp_path)
    rows = _fig4_sio_points(observations)
    point_observations = tuple(observations.values())
    context = ScoreContext(
        works=works,
        experiments=experiments,
        observations=observations,
        extract_review={SOURCE_ID: "reviewed"},
        root=ROOT,
    )

    for engine in SCORING_ENGINES:
        for row in rows:
            experiment = experiments[row.experiment_id]
            prediction = predict_with_engine(engine, row, experiment=experiment)
            gates = run_validity_gates(
                experiment, row, point_observations=point_observations
            )
            pressure_sum_check = next(
                check
                for check in gates.checks
                if check.name == "in_cell_partial_pressure_sum"
            )
            assert not gates.passed
            assert gates.reason is RefusalReason.EFFUSION_REGIME_UNVERIFIED
            assert gates.primary_check == "in_cell_partial_pressure_sum"
            assert [
                check.name for check in gates.checks if not check.passed
            ] == ["in_cell_partial_pressure_sum"]
            assert [check.name for check in gates.checks] == [
                "in_cell_partial_pressure_sum",
                "kems_calibration",
                "geometry_determinants",
            ]
            assert not any(check.name == "method" for check in gates.checks)
            assert experiment.method == State.of(MethodToken.KNUDSEN_EFFUSION)
            assert {notice.kind for notice in row.notices} == {
                NoticeKind.ADMISSION_DEFAULTED,
                NoticeKind.SOURCE_DISAGREEMENT,
            }
            assert not pressure_sum_check.passed
            assert (
                pressure_sum_check.detail["reason"]
                == "no admitted printed partial pressures at this exact point"
            )
            assert (
                pressure_sum_check.detail.get("printed_partial_pressure_sum_Pa")
                is None
            )
            assert (
                _missing_apparatus_fact(
                    pressure_sum_check,
                    allow_calibration=False,
                    allow_unknown_method=True,
                )
                == "in_cell_partial_pressure_sum"
            )
            apparatus_notices = _unverified_apparatus_notices(
                row, experiment, gates
            )
            residual, _candidate = compile_residual(
                row,
                engine,
                context=context,
                prediction=prediction,
                point_observations=point_observations,
            )
            assert residual.status in {
                ResidualStatus.MATCH,
                ResidualStatus.MISMATCH,
            }
            assert residual.numeric is not None
            assert residual.refusal is None
            assert len(apparatus_notices) == 2
            assert all(
                notice.kind is NoticeKind.UNVERIFIED_APPARATUS
                for notice in apparatus_notices
            )
            assert any(
                notice.reason.startswith(
                    "apparatus_unverified:in_cell_partial_pressure_sum;"
                )
                for notice in apparatus_notices
            )
            assert any(
                notice.reason.startswith("calibration_not_grounded:")
                for notice in apparatus_notices
            )
            assert any(
                notice.kind is NoticeKind.FIGURE_ONLY
                for notice in residual.notices
            )
            assert any(
                notice.kind is NoticeKind.SOURCE_DISAGREEMENT
                and INITIAL_CHARGE_ONLY_PROXY_NOTICE in notice.reason
                for notice in residual.notices
            )
            assert any(
                notice.kind is NoticeKind.UNVERIFIED_APPARATUS
                and notice.reason.startswith(
                    "apparatus_unverified:in_cell_partial_pressure_sum;"
                )
                for notice in residual.notices
            )
            assert any(
                notice.kind is NoticeKind.UNVERIFIED_APPARATUS
                and notice.reason.startswith("calibration_not_grounded:")
                for notice in residual.notices
            )


def test_hastie_fig4_method_pin_mutation_rejects_unspecified(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.battery.migrate as migration_module

    original_map_method = migration_module.map_method

    def legacy_unspecified_regime(raw):
        if raw == "kems_effusion":
            return State.of(MethodToken.UNSPECIFIED)
        return original_map_method(raw)

    monkeypatch.setattr(migration_module, "map_method", legacy_unspecified_regime)
    _works, experiments, observations = _hastie_store(tmp_path)
    rows = _fig4_sio_points(observations)
    with pytest.raises(AssertionError):
        assert all(
            experiments[row.experiment_id].method
            == State.of(MethodToken.KNUDSEN_EFFUSION)
            for row in rows
        )


def test_hastie_evidence_unknown_pressure_keeps_method_unknown_refusal(
    tmp_path,
) -> None:
    works, experiments, observations = _hastie_store(tmp_path)
    row = _fig4_sio_points(observations)[0]
    row = replace(
        row,
        evidence=replace(
            row.evidence,
            class_=State.unknown("evidence class unavailable"),
        ),
    )
    experiment = experiments[row.experiment_id]
    context = ScoreContext(
        works=works,
        experiments=experiments,
        observations=observations,
        extract_review={SOURCE_ID: "reviewed"},
        root=ROOT,
    )

    residual, _candidate = compile_residual(
        row,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        point_observations=tuple(observations.values()),
    )

    assert residual.numeric is None
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.EFFUSION_REGIME_UNVERIFIED

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import (
    AdmissionStatus,
    Authority,
    Engine,
    EvidenceClass,
    NoticeKind,
    PerBasis,
    Quantity,
    QUANTITY_UNITS,
    RefusalReason,
    ResidualStatus,
)
from simulator.battery.identity import (
    Identity,
    ideal_gas_entropy_pressure_shift_J_per_mol_K,
)
from simulator.battery.records import State, Value
from simulator.battery.score import (
    ScoreContext,
    compile_residual,
    headline_records,
    headline_summary_payload,
    predict_with_engine,
)
from tests.battery import factories as F


def _o2_observation(
    quantity: Quantity,
    *,
    temperature_K: Decimal = Decimal("1000"),
    value: Decimal = Decimal("1"),
    standard_pressure_Pa: State[Decimal] | None = None,
):
    experiment = F.tabulation_experiment(T_K=temperature_K)
    identity = replace(
        F.o2_identity(temperature_K),
        quantity=quantity,
        reaction=State.not_applicable("not a formation quantity"),
        formation_elements=State.not_applicable("not a formation quantity"),
        standard_pressure_Pa=(
            standard_pressure_Pa
            if standard_pressure_Pa is not None
            else State.of(Decimal("100000"))
        ),
    )
    observation = F.observation(
        f"synthetic-nasa-cea::{quantity.value}",
        experiment.experiment_id,
        identity,
        value,
        evidence=EvidenceClass.MEASURED_DIRECT,
        admission=AdmissionStatus.ADMITTED,
        source_id="synthetic-nasa-cea-score",
    )
    context = ScoreContext(
        works={experiment.work_id: F.work(experiment.work_id)},
        experiments={experiment.experiment_id: experiment},
        observations={observation.observation_id: observation},
        extract_review={observation.source_id or "": "reviewed"},
        hostname="test",
        root=Path(__file__).resolve().parents[2],
    )
    return context, observation


def test_nasa_cea_9_scores_supported_synthetic_heat_capacity() -> None:
    context, observation = _o2_observation(
        Quantity.CP, temperature_K=Decimal("298.15")
    )
    assert isinstance(observation.identity, Identity)
    assert observation.identity.per is not None
    assert observation.identity.per.value is PerBasis.MOL_SPECIES

    prediction = predict_with_engine(
        Engine.NASA_CEA_9,
        observation,
        experiment=context.experiments[observation.experiment_id],
    )
    assert prediction.value is not None
    assert abs(prediction.value - Decimal("29.3782")) < Decimal("0.001")
    assert prediction.unit == QUANTITY_UNITS[Quantity.CP]
    assert prediction.authority is Authority.BRIDGE

    reference = replace(
        observation,
        value=Value.point_of(prediction.value + Decimal("1")),
    )
    context = replace(
        context,
        observations={reference.observation_id: reference},
    )
    residual, _candidate = compile_residual(
        reference,
        Engine.NASA_CEA_9,
        context=context,
        prediction=prediction,
        comparison_ids={reference.observation_id},
    )

    assert residual.numeric is not None, residual.refusal
    assert residual.numeric.unit == QUANTITY_UNITS[Quantity.CP]
    assert abs(residual.numeric.value) == Decimal("1")

    records = headline_records(
        (residual,), context=context, engines=(Engine.NASA_CEA_9,)
    )
    summary = headline_summary_payload(records)
    assert summary["kind"] == "battery_headline_summary"
    assert any(
        row["engine"] == Engine.NASA_CEA_9.value
        for row in summary["records"]
    )


def test_nasa_cea_9_gas_entropy_uses_printed_pressure_shift() -> None:
    predictions = {}
    for pressure in (Decimal("100000"), Decimal("101325")):
        context, observation = _o2_observation(
            Quantity.S,
            temperature_K=Decimal("298.15"),
            standard_pressure_Pa=State.of(pressure),
        )
        predictions[pressure] = predict_with_engine(
            Engine.NASA_CEA_9,
            observation,
            experiment=context.experiments[observation.experiment_id],
        )

    at_reference = predictions[Decimal("100000")]
    at_printed = predictions[Decimal("101325")]
    assert at_reference.value is not None
    assert at_printed.value is not None
    expected_shift = ideal_gas_entropy_pressure_shift_J_per_mol_K(
        Decimal("100000"), Decimal("101325")
    )
    assert abs(at_printed.value - at_reference.value - expected_shift) < Decimal(
        "1e-24"
    )


def test_nasa_cea_9_gas_entropy_refuses_missing_or_nonpositive_pressure() -> None:
    for pressure in (
        State.unknown("source does not report pressure"),
        State.of(Decimal("0")),
    ):
        context, observation = _o2_observation(
            Quantity.S,
            temperature_K=Decimal("298.15"),
            standard_pressure_Pa=pressure,
        )
        prediction = predict_with_engine(
            Engine.NASA_CEA_9,
            observation,
            experiment=context.experiments[observation.experiment_id],
        )

        assert prediction.value is None
        assert prediction.refusal_reason is RefusalReason.IDENTITY_UNKNOWN
        assert prediction.refusal_detail["reason"].startswith(
            "nasa-cea-gas-entropy-pressure-"
        )


def test_nasa_cea_9_refuses_unsupported_quantity_with_typed_reason() -> None:
    context, observation = _o2_observation(Quantity.PARTIAL_MOLAR_ENTHALPY)
    residual, _candidate = compile_residual(
        observation,
        Engine.NASA_CEA_9,
        context=context,
        comparison_ids={observation.observation_id},
    )

    assert residual.status is ResidualStatus.REFUSED
    assert residual.numeric is None
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.UNSUPPORTED


def test_nasa_cea_9_predicts_and_flags_out_of_domain_temperature() -> None:
    _context, observation = _o2_observation(
        Quantity.CP,
        temperature_K=Decimal("25000"),
    )
    prediction = predict_with_engine(Engine.NASA_CEA_9, observation)

    assert prediction.value is not None
    assert prediction.authority is Authority.EXTRAPOLATED
    flag = next(
        notice
        for notice in prediction.notices
        if notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
    )
    assert flag.band is not None
    assert "20000" in flag.band

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from simulator.battery.enums import (
    Authority,
    Engine,
    EvidenceClass,
    ExecutionState,
    NoticeKind,
    Quantity,
    RefusalReason,
    ResidualStatus,
)
from simulator.battery.records import (
    DecisionBand,
    Execution,
    Notice,
    State,
)
from simulator.battery.score import (
    EnginePrediction,
    ScoreContext,
    compile_residual,
    predict_with_engine,
)
from tests.battery import factories as F


_PRESSURE_NOTICE_ORIGIN = "score:knudsen_total_pressure"


def _partial_row(observation_id: str):
    experiment = F.kems_experiment()
    identity = replace(
        F.pref_identity(T_K=Decimal("1500")),
        quantity=Quantity.P_PARTIAL,
        composition=F.activity_identity().composition,
        total_pressure_Pa=State.unknown("total pressure not printed"),
    )
    reference = F.observation(
        observation_id,
        experiment.experiment_id,
        identity,
        Decimal("10"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="knudsen-pressure-test",
    )
    work = F.work()
    context = ScoreContext(
        works={work.work_id: work},
        experiments={experiment.experiment_id: experiment},
        observations={reference.observation_id: reference},
        extract_review={"knudsen-pressure-test": None},
        hostname="test",
    )
    return experiment, reference, context


def _pressure_notice(total_pressure_pa: str, sensitivity_dex: str) -> Notice:
    return Notice(
        kind=NoticeKind.PRESSURE_PROVENANCE_UNKNOWN,
        affected_quantities=(Quantity.P_PARTIAL,),
        reason=(
            "total pressure not printed; derived from Knudsen-cell equilibrium "
            f"partial pressures; total_pressure_Pa={total_pressure_pa}; "
            f"sensitivity_dex={sensitivity_dex}"
        ),
        origin=_PRESSURE_NOTICE_ORIGIN,
    )


@pytest.mark.parametrize("engine", (Engine.ALPHAMELTS, Engine.VAPOROCK))
def test_knudsen_partial_pressure_derives_total_pressure_and_scores(
    engine, monkeypatch
) -> None:
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    from simulator.diagnostic_helpers.binary_pot_battery import (
        EngineHandle,
        EquilibrateCell,
    )
    import simulator.battery.score as score_mod

    experiment, reference, context = _partial_row(f"knudsen-pressure-{engine.value}")
    calls: list[float] = []

    def fail_on_default_pressure(*_args, **_kwargs):
        pytest.fail("Knudsen p_partial scoring reached the 1e-6-bar default")

    def fake_equilibrate(
        _handle,
        pot,
        *,
        temperature_K,
        po2,
        physical_pressure_bar,
        **_kwargs,
    ):
        calls.append(physical_pressure_bar)
        return EquilibrateCell(
            pot_id=pot.pot_id,
            engine=engine.value,
            temperature_K=temperature_K,
            po2=po2,
            status="ok",
            refusal_reason=None,
            engine_status="ok",
            engine_reason=None,
            melt_activities={},
            gas_partial_pressures_Pa={"Na": 10.0, "O2": 10.0},
            liquid_fraction=1.0,
            wall_s=0.0,
            cpu_s=0.0,
            hostname="test",
        )

    monkeypatch.setattr(score_mod, "total_pressure_bar_for_score", fail_on_default_pressure)
    monkeypatch.setattr(battery, "equilibrate_cell", fake_equilibrate)
    handle = EngineHandle(
        name=engine.value,
        backend=object(),
        available=True,
        unavailable_reason=None,
        takes_fo2=True,
        supports_intrinsic_fo2=False,
    )

    prediction = predict_with_engine(
        engine,
        reference,
        handles={engine.value: handle},
        isolated=False,
        experiment=experiment,
    )

    assert prediction.execution.state is ExecutionState.PRODUCED
    assert prediction.value == Decimal("10.0")
    assert calls == pytest.approx([1e-3, 20 / 1e5])
    assert all(pressure != 1e-6 for pressure in calls)
    assert prediction.identity.total_pressure_Pa == State.of(Decimal("20.0"))
    pressure_notices = [
        notice
        for notice in prediction.notices
        if notice.origin == _PRESSURE_NOTICE_ORIGIN
    ]
    assert len(pressure_notices) == 1
    assert "not printed" in pressure_notices[0].reason
    assert "total_pressure_Pa=20" in pressure_notices[0].reason
    assert "sensitivity_dex=0" in pressure_notices[0].reason
    assert pressure_notices[0].fo2_mode is None

    residual, _candidate = compile_residual(
        reference,
        engine,
        context=context,
        prediction=prediction,
        derived_band=DecisionBand(
            Decimal("0.01"), "dimensionless", "test row band"
        ),
    )

    assert residual.status is ResidualStatus.MATCH
    assert residual.numeric is not None
    assert pressure_notices[0] in residual.notices


def test_knudsen_total_pressure_sensitivity_above_row_band_refuses() -> None:
    experiment, reference, context = _partial_row("knudsen-pressure-over-band")
    pressure_notice = _pressure_notice("20", "0.02")
    prediction_identity = replace(
        reference.identity,
        total_pressure_Pa=State.of(Decimal("20")),
    )
    prediction = EnginePrediction(
        engine=Engine.VAPOROCK,
        channel=Engine.VAPOROCK.value,
        execution=Execution(state=ExecutionState.PRODUCED),
        value=Decimal("10"),
        unit="Pa",
        authority=Authority.CERTIFIED,
        coefficient_sources=("nasa-cea-thermo",),
        lineage_complete=True,
        notices=(pressure_notice,),
        identity=prediction_identity,
    )

    residual, _candidate = compile_residual(
        reference,
        Engine.VAPOROCK,
        context=context,
        prediction=prediction,
        derived_band=DecisionBand(
            Decimal("0.01"), "dimensionless", "test row band"
        ),
    )

    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.IDENTITY_INCOMPLETE
    assert residual.refusal.detail["reason"] == (
        "knudsen_total_pressure_sensitivity_exceeds_band"
    )
    assert residual.refusal.detail["sensitivity_dex"] == "0.02"
    assert residual.refusal.detail["band_dex"] == "0.01"
    assert pressure_notice in residual.notices

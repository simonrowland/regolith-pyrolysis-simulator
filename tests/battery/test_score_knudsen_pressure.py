from __future__ import annotations

import math
from dataclasses import replace
from decimal import Decimal

import pytest

from simulator.battery.enums import (
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
    State,
)
from simulator.battery.score import (
    ScoreContext,
    compile_residual,
    predict_with_engine,
)
from tests.battery import factories as F


_PRESSURE_NOTICE_ORIGIN = "score:knudsen_total_pressure"


def _partial_row(observation_id: str, *, observed_value: Decimal = Decimal("10")):
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
        observed_value,
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


def _mock_knudsen_prediction(
    engine,
    experiment,
    reference,
    monkeypatch,
    *,
    sensitivity_dex: float = 0.0,
    na_pressure: float = 10.0,
    o2_pressure: float = 10.0,
    updated_o2_pressure: float | None = None,
    invalid_components_call: int | None = None,
    oxygen_balance_pO2_values: tuple[float, ...] | None = None,
    po2_calls: list[tuple[float, str, float | None]] | None = None,
    oxygen_balance_calls: list[float] | None = None,
):
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    from simulator.diagnostic_helpers.binary_pot_battery import (
        EngineHandle,
        EquilibrateCell,
        PO2_COMMANDED,
        PO2_OXYGEN_BALANCE_EFFUSION,
        Po2Request,
    )
    import simulator.battery.score as score_mod

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
        call_number = len(calls) + 1
        calls.append(physical_pressure_bar)
        if po2_calls is not None:
            po2_calls.append((physical_pressure_bar, po2.mode, po2.po2_bar))
        if oxygen_balance_pO2_values is None:
            reported_na_pressure = (
                na_pressure
                if call_number == 1
                else na_pressure * math.pow(10.0, sensitivity_dex)
            )
        else:
            reported_na_pressure = (
                na_pressure
                if po2.po2_bar == oxygen_balance_pO2_values[0]
                else na_pressure * math.pow(10.0, sensitivity_dex)
            )
        reported_o2_pressure = (
            o2_pressure
            if call_number == 1 or updated_o2_pressure is None
            else updated_o2_pressure
        )
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
            gas_partial_pressures_Pa={
                "Na": reported_na_pressure,
                "O2": reported_o2_pressure,
            },
            liquid_fraction=1.0,
            wall_s=0.0,
            cpu_s=0.0,
            hostname="test",
            invalid_gas_partial_pressure_components=(
                ("O2",) if call_number == invalid_components_call else ()
            ),
        )

    if oxygen_balance_pO2_values is not None:
        def fake_oxygen_balance(engine_call_at_po2, **_kwargs):
            index = 0 if oxygen_balance_calls is None else len(oxygen_balance_calls)
            solved_po2_bar = oxygen_balance_pO2_values[index]
            if oxygen_balance_calls is not None:
                oxygen_balance_calls.append(solved_po2_bar)
            cell = engine_call_at_po2(solved_po2_bar)
            return (
                solved_po2_bar,
                cell.gas_partial_pressures_Pa,
                {
                    "residual": 0.0,
                    "bracket": (-2.0, -1.0),
                    "dominant_O_carriers": (),
                    "dominant_metal_carriers": (),
                },
                1.0,
                {
                    "cell_material": "W",
                    "buffer_pinned": False,
                    "buffer_pO2_bar": None,
                    "cell_oxide_janaf_sources": (),
                },
            )

        monkeypatch.setattr(
            battery, "_solve_vapor_engine_cell_oxygen_balance", fake_oxygen_balance
        )
        monkeypatch.setattr(
            score_mod,
            "_oxygen_input_request",
            lambda **_kwargs: Po2Request(
                mode=PO2_OXYGEN_BALANCE_EFFUSION,
                po2_bar=None,
                cell_material="W",
            ),
        )

    monkeypatch.setattr(
        score_mod, "total_pressure_bar_for_score", fail_on_default_pressure
    )
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
    return prediction, calls


@pytest.mark.parametrize(
    "engine", (Engine.ALPHAMELTS, Engine.VAPOROCK, Engine.THERMOENGINE)
)
def test_knudsen_partial_pressure_derives_total_pressure_and_scores(
    engine, monkeypatch
) -> None:
    experiment, reference, context = _partial_row(f"knudsen-pressure-{engine.value}")
    prediction, calls = _mock_knudsen_prediction(
        engine, experiment, reference, monkeypatch
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


def test_knudsen_total_pressure_sensitivity_above_row_band_refuses(
    monkeypatch,
) -> None:
    experiment, reference, context = _partial_row("knudsen-pressure-over-band")
    prediction, _calls = _mock_knudsen_prediction(
        Engine.VAPOROCK,
        experiment,
        reference,
        monkeypatch,
        sensitivity_dex=0.02,
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
    assert float(residual.refusal.detail["sensitivity_dex"]) == pytest.approx(0.02)
    assert residual.refusal.detail["band_dex"] == "0.01"
    assert any(
        notice.origin == _PRESSURE_NOTICE_ORIGIN
        for notice in residual.notices
    )


@pytest.mark.parametrize(
    ("invalid_call", "expected_pass"), ((1, "seed"), (2, "derived"))
)
def test_invalid_gas_channel_refuses_knudsen_pressure_sum(
    invalid_call, expected_pass, monkeypatch
) -> None:
    experiment, reference, _context = _partial_row(
        f"knudsen-invalid-pressure-{invalid_call}"
    )
    prediction, _calls = _mock_knudsen_prediction(
        Engine.VAPOROCK,
        experiment,
        reference,
        monkeypatch,
        invalid_components_call=invalid_call,
    )

    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert prediction.refusal_detail["reason"] == (
        "knudsen_total_pressure_invalid_gas_components"
    )
    assert prediction.refusal_detail["pressure_pass"] == expected_pass
    assert prediction.refusal_detail["invalid_components"] == ["O2"]


def test_knudsen_sensitivity_uses_target_species_not_other_channels(
    monkeypatch,
) -> None:
    experiment, reference, context = _partial_row(
        "knudsen-pressure-target-species-sensitivity"
    )
    prediction, _calls = _mock_knudsen_prediction(
        Engine.VAPOROCK,
        experiment,
        reference,
        monkeypatch,
        updated_o2_pressure=100.0,
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

    assert prediction.value == Decimal("10.0")
    assert residual.status is ResidualStatus.MATCH
    pressure_notice = next(
        notice
        for notice in residual.notices
        if notice.origin == _PRESSURE_NOTICE_ORIGIN
    )
    assert "sensitivity_dex=0" in pressure_notice.reason


def test_rung_three_pressure_sensitivity_re_solves_coupled_oxygen_balance(
    monkeypatch,
) -> None:
    experiment, reference, context = _partial_row(
        "knudsen-pressure-coupled-oxygen-sensitivity"
    )
    pressure_calls: list[tuple[float, str, float | None]] = []
    oxygen_balance_calls: list[float] = []
    prediction, _calls = _mock_knudsen_prediction(
        Engine.VAPOROCK,
        experiment,
        reference,
        monkeypatch,
        sensitivity_dex=1.0,
        oxygen_balance_pO2_values=(0.01, 0.1),
        po2_calls=pressure_calls,
        oxygen_balance_calls=oxygen_balance_calls,
    )

    assert oxygen_balance_calls == pytest.approx([0.01, 0.1])
    assert [call[0] for call in pressure_calls] == pytest.approx(
        [1e-3, 1e-3, 20 / 1e5, 20 / 1e5]
    )
    assert [call[2] for call in pressure_calls] == pytest.approx(
        [0.01, 0.01, 0.1, 0.1]
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
    assert residual.refusal.detail["reason"] == (
        "knudsen_total_pressure_sensitivity_exceeds_band"
    )
    assert float(residual.refusal.detail["sensitivity_dex"]) == pytest.approx(1.0)


def test_knudsen_total_pressure_round_trip_matches_prediction_identity(
    monkeypatch,
) -> None:
    experiment, reference, context = _partial_row(
        "knudsen-pressure-decimal-round-trip",
        observed_value=Decimal("0.1"),
    )
    prediction, _calls = _mock_knudsen_prediction(
        Engine.VAPOROCK,
        experiment,
        reference,
        monkeypatch,
        na_pressure=0.1,
        o2_pressure=0.0,
    )

    assert prediction.value == Decimal("0.1")
    residual, _candidate = compile_residual(
        reference,
        Engine.VAPOROCK,
        context=context,
        prediction=prediction,
        derived_band=DecisionBand(
            Decimal("0.01"), "dimensionless", "test row band"
        ),
    )

    assert residual.status is ResidualStatus.MATCH

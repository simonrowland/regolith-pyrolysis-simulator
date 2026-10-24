from dataclasses import replace
from decimal import Decimal
import math
from types import SimpleNamespace

import pytest

from simulator.battery.enums import (
    AmountBasis,
    Authority,
    Engine,
    ExecutionState,
    FO2Channel,
    NoticeKind,
    Phase,
    Quantity,
    Rail,
    RefusalReason,
)
from simulator.battery.identity import Identity
from simulator.battery.migrate import _log_fO2_to_pa
from simulator.battery.records import Composition, FO2Control, Located, Species, State
from simulator.battery.score import predict_with_engine, rail_for_quantity
from tests.battery import factories as F


_PRINTED_OXIDE_SPLIT = Composition(
    basis="printed_oxide_wt_pct",
    components=(
        ("SiO2", Decimal("47.8")),
        ("Al2O3", Decimal("15")),
        ("FeO", Decimal("8")),
        ("Fe2O3", Decimal("5")),
        ("MgO", Decimal("10")),
        ("CaO", Decimal("10")),
        ("Na2O", Decimal("4.2")),
    ),
    amount_basis=AmountBasis.MASS_PERCENT,
)


def _fugacity_case(
    *,
    formula: str = "O2",
    composition: Composition | None = _PRINTED_OXIDE_SPLIT,
    pressure_pa: Decimal | None = Decimal("100000"),
    fO2_control: FO2Control | None = None,
):
    temperature_K = Decimal("1800")
    experiment = F.tabulation_experiment(
        experiment_id="closed-fugacity-exp",
        T_K=temperature_K,
        total_P=pressure_pa or Decimal("1e-6"),
        fO2_control=fO2_control,
    )
    initial_composition = (
        None if composition is None else F.located(composition)
    )
    experiment = replace(
        experiment,
        sample=replace(
            experiment.sample,
            initial_composition=initial_composition,
        ),
    )
    identity = Identity(
        quantity=Quantity.FUGACITY,
        species=Species(formula, Phase.G),
        temperature_K=State.of(temperature_K),
    )
    observation = F.observation(
        "closed-fugacity-row",
        experiment.experiment_id,
        identity,
        Decimal("1e-8"),
        source_id="synthetic-closed-fugacity",
    )
    if pressure_pa is None:
        experiment = replace(
            experiment,
            pressure_environment=replace(
                experiment.pressure_environment,
                total_pressure_Pa=Located(
                    State.unknown("unit_not_stated"), locator=F.loc()
                ),
            ),
        )
    return experiment, observation


def test_fugacity_uses_the_redox_rail():
    assert rail_for_quantity(Quantity.FUGACITY, species_formula="O2") is Rail.REDOX


def test_openimcc_predicts_fugacity_from_closed_printed_oxide_split(monkeypatch):
    pytest.importorskip("openimcc")
    import openimcc

    experiment, observation = _fugacity_case()
    calls = []
    original_evaluate_redox = openimcc.evaluate_redox

    def capture_closed_call(*args, **kwargs):
        result = original_evaluate_redox(*args, **kwargs)
        calls.append((args, kwargs, result))
        return result

    monkeypatch.setattr(openimcc, "evaluate_redox", capture_closed_call)
    prediction = predict_with_engine(
        Engine.OPENIMCC, observation, experiment=experiment
    )

    assert prediction.execution.state is ExecutionState.PRODUCED
    assert prediction.value is not None
    assert prediction.unit == "Pa"
    assert prediction.authority is Authority.BRIDGE
    assert len(calls) == 1
    args, kwargs, result = calls[0]
    assert args[2] == "closed"
    assert "lambda_imposed" not in kwargs
    log10_fO2_bar = result.lambda_ln_f_o2 / math.log(10.0)
    assert prediction.value == _log_fO2_to_pa(Decimal(str(log10_fO2_bar)))
    assert len(result.flags) == 6
    assert tuple(
        notice.reason
        for notice in prediction.notices
        if notice.kind is NoticeKind.ENGINE_MODEL_CAVEAT
    ) == tuple(result.flags)
    assert all(
        notice.affected_quantities == (Quantity.FUGACITY,)
        and notice.origin == "openimcc.evaluate_redox"
        for notice in prediction.notices
    )
    assert not any(
        notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        for notice in prediction.notices
    )


def test_thermoengine_uses_intrinsic_closed_solved_fugacity(monkeypatch):
    experiment, observation = _fugacity_case()
    calls = []

    def intrinsic_equilibrium(engine, **kwargs):
        calls.append((engine, kwargs))
        return SimpleNamespace(
            status="ok",
            # A default-looking field must not override the solved diagnostic.
            fO2_log=-9.0,
            diagnostics={"solved_fO2_log": -8.5},
        )

    monkeypatch.setattr(
        "simulator.battery.score._equilibrate_phase_assemblage",
        intrinsic_equilibrium,
    )
    prediction = predict_with_engine(
        Engine.THERMOENGINE, observation, experiment=experiment
    )

    assert prediction.execution.state is ExecutionState.PRODUCED
    assert prediction.value == _log_fO2_to_pa(Decimal("-8.5"))
    assert prediction.unit == "Pa"
    assert len(calls) == 1
    engine, kwargs = calls[0]
    assert engine is Engine.THERMOENGINE
    assert kwargs["fO2_log"] is None
    assert kwargs["pressure_bar"] == 1.0


def test_imposed_fugacity_is_refused_as_circular(monkeypatch):
    experiment, observation = _fugacity_case(
        fO2_control=FO2Control(
            channel=State.of(FO2Channel.BUFFER), buffer=F.located("IW")
        )
    )

    def unexpected_engine_call(*_args, **_kwargs):
        pytest.fail("imposed fO2 must be refused before the engine call")

    monkeypatch.setattr("openimcc.evaluate_redox", unexpected_engine_call)
    prediction = predict_with_engine(
        Engine.OPENIMCC, observation, experiment=experiment
    )

    assert prediction.value is None
    assert prediction.refusal_reason is RefusalReason.CYCLIC_DERIVATION
    assert prediction.refusal_detail == {
        "reason": "fugacity_imposed",
        "channel": FO2Channel.BUFFER.value,
    }


def test_non_oxygen_fugacity_is_refused():
    experiment, observation = _fugacity_case(formula="FeO")

    prediction = predict_with_engine(
        Engine.OPENIMCC, observation, experiment=experiment
    )

    assert prediction.value is None
    assert prediction.refusal_reason is RefusalReason.OUTSIDE_SUPPORTED_SPECIES
    assert prediction.refusal_detail == {
        "reason": "fugacity_requires_O2",
        "species_formula": "FeO",
    }


def test_unpublished_starting_composition_is_refused():
    experiment, observation = _fugacity_case(composition=None)
    experiment = replace(
        experiment,
        sample=replace(
            experiment.sample,
            initial_composition=Located(
                State.unknown("not_published"), locator=F.loc()
            ),
        ),
    )

    prediction = predict_with_engine(
        Engine.OPENIMCC, observation, experiment=experiment
    )

    assert prediction.value is None
    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert prediction.refusal_detail == {
        "reason": "missing_input",
        "input": "starting_composition",
        "source_reason": "not_published",
    }


def test_missing_pressure_is_refused_before_thermoengine_call(monkeypatch):
    experiment, observation = _fugacity_case(pressure_pa=None)
    monkeypatch.setattr(
        "simulator.battery.score._equilibrate_phase_assemblage",
        lambda *_args, **_kwargs: pytest.fail("missing pressure must fail closed"),
    )

    prediction = predict_with_engine(
        Engine.THERMOENGINE, observation, experiment=experiment
    )

    assert prediction.value is None
    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert prediction.refusal_detail == {
        "reason": "missing_input",
        "input": "total_pressure_Pa",
        "source_reason": "unit_not_stated",
    }


def test_alphamelts_has_no_independent_closed_fugacity_output(monkeypatch):
    experiment, observation = _fugacity_case()
    monkeypatch.setattr(
        "simulator.battery.score._equilibrate_phase_assemblage",
        lambda *_args, **_kwargs: pytest.fail("AlphaMELTS must remain refused"),
    )

    prediction = predict_with_engine(
        Engine.ALPHAMELTS, observation, experiment=experiment
    )

    assert prediction.value is None
    assert prediction.authority is Authority.REFUSED
    assert prediction.refusal_reason is RefusalReason.UNSUPPORTED
    assert prediction.refusal_detail == {
        "reason": "engine_does_not_predict_oxygen_fugacity",
        "detail": "AlphaMELTS has no independent closed-system fO2 output; its default echo is circular.",
    }


@pytest.mark.parametrize("log10_fO2_bar", (Decimal("-18.25"), Decimal("-2.5"), Decimal("0")))
def test_fugacity_log_bar_pa_round_trip(log10_fO2_bar):
    pressure_pa = _log_fO2_to_pa(log10_fO2_bar)
    identity = Identity(
        quantity=Quantity.FUGACITY,
        species=Species("O2", Phase.G),
        temperature_K=State.of(Decimal("1800")),
        fO2_Pa=State.of(pressure_pa),
    )

    from simulator.battery.score import _printed_fo2_log10_bar

    assert _printed_fo2_log10_bar(identity) == pytest.approx(
        float(log10_fO2_bar), abs=1e-12
    )

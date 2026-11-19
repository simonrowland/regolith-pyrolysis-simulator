"""d-280: unprinted condensed liquidus/solidus pressure.

The owner decides. These tests call it. They do not reimplement the phrase
list or the 1 bar value.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from simulator.battery.enums import (
    AmbientPressureEvidenceClass,
    BenchIdentityBasis,
    EvidenceClass,
    MethodToken,
    NoticeKind,
    Quantity,
)
from simulator.battery.records import (
    AmbientPressureEvidence,
    Apparatus,
    ApparatusGeometry,
    Bench,
    BenchIdentity,
    Located,
    State,
)
from simulator.battery.score import flagged_strata
from simulator.battery.transition_pressure import (
    unprinted_liquidus_pressure_block,
    unprinted_liquidus_solidus_pressure,
)
from simulator.physical_constants import BAR_PA_DECIMAL, PA_PER_BAR
from tests.battery import factories as F

_AMBIENT_REASON = "ambient pressure assumed, not printed"


def _row(
    *,
    method: MethodToken | None = None,
    method_reason: str | None = None,
    form: str | None = None,
    locator_note: str | None = None,
    point_note: str | None = None,
    point_reason: str | None = None,
    bench_text: str | None = None,
):
    experiment = F.tabulation_experiment()
    if method is not None:
        experiment = replace(experiment, method=State.of(method))
    elif method_reason is not None:
        experiment = replace(experiment, method=State.unknown(method_reason))
    if form is not None:
        experiment = replace(
            experiment,
            sample=replace(experiment.sample, form=F.located(form)),
        )
    if locator_note is not None:
        experiment = replace(
            experiment, locator=F.loc(page=1, section="methods", note=locator_note)
        )
    identity = replace(
        F.activity_identity(),
        quantity=Quantity.TRANSITION_TEMPERATURE,
        subtype=State.of("liquidus"),
        total_pressure_Pa=State.unknown("pressure is not printed"),
    )
    point_conditions = None
    if point_note is not None or point_reason is not None:
        point_conditions = {
            "total_pressure_Pa": Located(
                State.unknown(point_reason or "pressure is not printed"),
                locator=F.loc(page=2, note=point_note or "row note"),
            )
        }
    observation = F.observation(
        "liquidus-unprinted-pressure",
        experiment.experiment_id,
        identity,
        Decimal("1700"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    if point_conditions is not None:
        observation = replace(observation, point_conditions=point_conditions)
    bench = None
    if bench_text is not None:
        bench = Bench(
            "bench-1",
            experiment.work_id or "work-1",
            BenchIdentity(BenchIdentityBasis.DESCRIBED_IN_THIS_WORK),
            apparatus_family=F.located(bench_text),
        )
    return experiment, observation, bench


def _decision(**kwargs):
    experiment, observation, bench = _row(**kwargs)
    before = observation.identity
    block = unprinted_liquidus_pressure_block(observation, experiment, bench)
    decision = unprinted_liquidus_solidus_pressure(observation, experiment, bench)
    assert observation.identity is before
    assert before.total_pressure_Pa is not None
    assert before.total_pressure_Pa.is_unknown
    return block, decision


def test_unprinted_ambient_row_is_one_bar_with_the_notice() -> None:
    block, decision = _decision()
    assert block is None
    assert decision is not None
    assert decision.pressure_Pa is BAR_PA_DECIMAL
    assert decision.pressure_bar == float(BAR_PA_DECIMAL) / PA_PER_BAR
    assert decision.pressure_bar == 1.0
    notice = decision.notice
    assert notice.kind is NoticeKind.AMBIENT_PRESSURE_ASSUMED
    assert notice.reason == _AMBIENT_REASON
    assert notice.affected_quantities == (Quantity.TRANSITION_TEMPERATURE,)
    assert notice.origin == "liquidus-unprinted-pressure"
    assert flagged_strata((notice,)) == ()


def test_absent_experiment_still_assumes_one_bar() -> None:
    _experiment, observation, _bench = _row()
    decision = unprinted_liquidus_solidus_pressure(observation, None, None)
    assert decision is not None
    assert decision.pressure_bar == 1.0
    assert decision.notice.reason == _AMBIENT_REASON


@pytest.mark.parametrize(
    "text",
    (
        "piston-cylinder",
        "piston cylinder",
        "piston_cylinder",
        "pistoncylinder",
        "end-loaded piston-cylinders",
        "multi-anvil",
        "multi anvil",
        "multianvil",
        "multi-anvils",
        "diamond-anvil",
        "diamond anvil cell",
        "internally heated pressure vessel",
        "internally-heated pressure vessels",
        "externally heated pressure vessel",
        "externally-heated pressure vessels",
        "cold-seal pressure vessel",
        "cold seal pressure vessels",
        "gas-medium apparatus",
        "gas medium presses",
        "belt apparatus",
        "belt-type apparatus",
        "Autoclave",
        "cold-seal autoclaves",
    ),
)
def test_high_pressure_apparatus_phrase_refuses(text: str) -> None:
    block, decision = _decision(form=text)
    assert block == "high_pressure_apparatus"
    assert decision is None


@pytest.mark.parametrize(
    "text",
    (
        "Pressed cylinder",
        "pressed pellet",
        "graphite cylinder",
        "10 kbar",
        "3 GPa",
        "P_GPa",
        "no pressure field under keys total_pressure_Pa / P_GPa / P_kbar",
        "no pressure field under keys piston-cylinder / P_GPa",
    ),
)
def test_ordinary_prose_is_not_a_high_pressure_apparatus(text: str) -> None:
    block, decision = _decision(form=text)
    assert block is None
    assert decision is not None
    assert decision.pressure_bar == 1.0


def test_piston_cylinder_token_refuses_without_prose() -> None:
    block, decision = _decision(method=MethodToken.PISTON_CYLINDER)
    assert block == "high_pressure_apparatus"
    assert decision is None


def test_method_reason_names_the_apparatus() -> None:
    block, decision = _decision(
        method_reason="equilibrated in a piston-cylinder apparatus"
    )
    assert block == "high_pressure_apparatus"
    assert decision is None


def test_unknown_method_without_apparatus_assumes() -> None:
    block, decision = _decision(method_reason="source does not state method")
    assert block is None
    assert decision is not None
    assert decision.pressure_bar == 1.0


def test_knudsen_token_does_not_assume_one_bar() -> None:
    experiment = F.kems_experiment()
    _experiment, observation, _bench = _row()
    observation = replace(observation, experiment_id=experiment.experiment_id)
    # The effusion cell's stored total pressure is a vacuum seed. The owner
    # must not adopt it and must not replace it with 1 bar.
    stored = experiment.pressure_environment.total_pressure_Pa.state.value
    assert stored.point == Decimal("1e-6")
    block = unprinted_liquidus_pressure_block(observation, experiment, None)
    decision = unprinted_liquidus_solidus_pressure(observation, experiment, None)
    assert block == "knudsen_closed_cell"
    assert decision is None


def test_knudsen_prose_on_a_quench_row_is_not_the_token() -> None:
    block, decision = _decision(
        method_reason="quench; the paper also cites a Knudsen effusion study"
    )
    assert block is None
    assert decision is not None
    assert decision.pressure_bar == 1.0


def test_point_condition_reason_is_not_apparatus_text() -> None:
    block, decision = _decision(
        point_reason=(
            "no pressure field under keys total_pressure_Pa / P_GPa / P_kbar"
        )
    )
    assert block is None
    assert decision is not None


def test_point_condition_note_names_the_apparatus() -> None:
    block, decision = _decision(point_note="run in a multi-anvil press")
    assert block == "high_pressure_apparatus"
    assert decision is None


def test_key_list_sentence_does_not_become_an_apparatus() -> None:
    block, decision = _decision(
        locator_note="no pressure field under keys piston-cylinder / P_GPa"
    )
    assert block is None
    assert decision is not None


def test_bench_apparatus_family_refuses() -> None:
    block, decision = _decision(bench_text="end-loaded piston-cylinder")
    assert block == "high_pressure_apparatus"
    assert decision is None


_PRESSURE_NOTE = "Run in a piston-cylinder; total pressure was not reported"


def _pressure_note_row(field: str):
    experiment, observation, bench = _row()
    environment = experiment.pressure_environment
    if field == "cell_internal_pressure_note":
        environment = replace(
            environment,
            cell_internal_pressure_note=F.located(_PRESSURE_NOTE),
        )
    elif field == "gauge":
        environment = replace(
            environment,
            gauge={"pressure_gauge": F.located(_PRESSURE_NOTE)},
        )
    elif field == "pumping":
        environment = replace(
            environment,
            pumping={"pump": F.located(_PRESSURE_NOTE)},
        )
    elif field == "total_pressure_reason":
        environment = replace(
            environment,
            total_pressure_Pa=Located(
                State.unknown(_PRESSURE_NOTE),
                locator=F.loc(page=1),
            ),
        )
    experiment = replace(experiment, pressure_environment=environment)
    if field == "ambient_quote":
        experiment = replace(
            experiment,
            ambient_pressure_evidence=AmbientPressureEvidence(
                AmbientPressureEvidenceClass.ATMOSPHERIC_PRESSURE_STATEMENT,
                _PRESSURE_NOTE,
                F.loc(page=3, section="methods"),
            ),
        )
    elif field == "thermocouple_note":
        experiment = replace(
            experiment,
            apparatus=Apparatus(
                temperature_measurement={
                    "thermocouple_note": F.located(_PRESSURE_NOTE),
                },
            ),
        )
    elif field == "orifice_shape":
        experiment = replace(
            experiment,
            apparatus=Apparatus(
                geometry=ApparatusGeometry(
                    orifice_shape=F.located(_PRESSURE_NOTE),
                ),
            ),
        )
    elif field == "bench_pumping_type":
        bench = Bench(
            "bench-1",
            experiment.work_id or "work-1",
            BenchIdentity(BenchIdentityBasis.DESCRIBED_IN_THIS_WORK),
            pumping_type=F.located(_PRESSURE_NOTE),
        )
    elif field == "bench_identity_reason":
        bench = Bench(
            "bench-1",
            experiment.work_id or "work-1",
            BenchIdentity(
                BenchIdentityBasis.INFERRED_FROM_EMBEDDED_EVIDENCE,
                reason=_PRESSURE_NOTE,
            ),
        )
    elif field not in {
        "cell_internal_pressure_note",
        "gauge",
        "pumping",
        "total_pressure_reason",
    }:
        raise AssertionError(field)
    return experiment, observation, bench


@pytest.mark.parametrize(
    "field",
    (
        "cell_internal_pressure_note",
        "gauge",
        "pumping",
        "ambient_quote",
        "thermocouple_note",
        "orifice_shape",
        "bench_pumping_type",
        "bench_identity_reason",
    ),
)
def test_source_backed_pressure_field_names_the_apparatus(field: str) -> None:
    experiment, observation, bench = _pressure_note_row(field)
    before = observation.identity
    block = unprinted_liquidus_pressure_block(observation, experiment, bench)
    decision = unprinted_liquidus_solidus_pressure(observation, experiment, bench)
    assert observation.identity is before
    assert block == "high_pressure_apparatus"
    assert decision is None


def test_pressure_note_without_apparatus_still_assumes_one_bar() -> None:
    experiment, observation, bench = _row()
    experiment = replace(
        experiment,
        pressure_environment=replace(
            experiment.pressure_environment,
            cell_internal_pressure_note=F.located(
                "total pressure was not reported"
            ),
        ),
    )
    block = unprinted_liquidus_pressure_block(observation, experiment, bench)
    decision = unprinted_liquidus_solidus_pressure(observation, experiment, bench)
    assert block is None
    assert decision is not None
    assert decision.pressure_bar == 1.0


def test_ambient_furnace_run_excludes_autoclave_mention() -> None:
    experiment, observation, bench = _row()
    experiment = replace(
        experiment,
        pressure_environment=replace(
            experiment.pressure_environment,
            cell_internal_pressure_note=F.located(
                "Run in a 1 atm furnace. The autoclave was not used for these runs."
            ),
        ),
    )
    block = unprinted_liquidus_pressure_block(observation, experiment, bench)
    decision = unprinted_liquidus_solidus_pressure(observation, experiment, bench)
    assert block is None
    assert decision is not None
    assert decision.pressure_bar == 1.0


def test_positive_high_pressure_apparatus_assignment_refuses() -> None:
    experiment, observation, bench = _row()
    experiment = replace(
        experiment,
        pressure_environment=replace(
            experiment.pressure_environment,
            cell_internal_pressure_note=F.located(
                "Run in an externally heated pressure vessel."
            ),
        ),
    )
    block = unprinted_liquidus_pressure_block(observation, experiment, bench)
    decision = unprinted_liquidus_solidus_pressure(observation, experiment, bench)
    assert block == "high_pressure_apparatus"
    assert decision is None


def test_apparatus_exclusion_without_ambient_run_context_still_refuses() -> None:
    experiment, observation, bench = _row()
    experiment = replace(
        experiment,
        pressure_environment=replace(
            experiment.pressure_environment,
            cell_internal_pressure_note=F.located(
                "The autoclave was not used for these runs."
            ),
        ),
    )
    block = unprinted_liquidus_pressure_block(observation, experiment, bench)
    decision = unprinted_liquidus_solidus_pressure(observation, experiment, bench)
    assert block == "high_pressure_apparatus"
    assert decision is None


def test_pressure_key_list_in_the_note_is_not_an_apparatus() -> None:
    experiment, observation, bench = _row()
    experiment = replace(
        experiment,
        pressure_environment=replace(
            experiment.pressure_environment,
            cell_internal_pressure_note=F.located(
                "no pressure field under keys piston-cylinder / P_GPa"
            ),
        ),
    )
    block = unprinted_liquidus_pressure_block(observation, experiment, bench)
    decision = unprinted_liquidus_solidus_pressure(observation, experiment, bench)
    assert block is None
    assert decision is not None
    assert decision.pressure_bar == 1.0


def test_unknown_pressure_reason_is_not_source_apparatus_prose() -> None:
    experiment, observation, bench = _pressure_note_row("total_pressure_reason")
    block = unprinted_liquidus_pressure_block(observation, experiment, bench)
    decision = unprinted_liquidus_solidus_pressure(observation, experiment, bench)
    assert block is None
    assert decision is not None
    assert decision.pressure_bar == 1.0

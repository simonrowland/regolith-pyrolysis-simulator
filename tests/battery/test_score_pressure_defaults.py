from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from simulator.battery.enums import (
    AmbientPressureEvidenceClass,
    MethodToken,
    NoticeKind,
    Quantity,
)
from simulator.battery.identity import PA_PER_BAR_DEC
from simulator.battery.records import AmbientPressureEvidence, State
from simulator.battery.score import (
    flagged_strata,
    _resolve_score_total_pressure,
    total_pressure_bar_for_score,
)
from simulator.diagnostic_helpers.binary_pot_battery import _DEFAULT_PRESSURE_BAR
from tests.battery import factories as F


def test_unknown_score_pressure_uses_dp001_atmosphere_and_typed_flag() -> None:
    identity = replace(
        F.pref_identity(),
        total_pressure_Pa=State.unknown("not printed"),
    )

    pressure_bar, notice, invalid = total_pressure_bar_for_score(
        identity, Quantity.ACTIVITY
    )

    assert pressure_bar == 1.01325
    assert notice is not None
    assert notice.kind is NoticeKind.ASSUMED_PRESSURE
    assert notice.policy == {
        "id": "DP-001",
        "version": "v1",
        "status": "canonical",
        "value": "101325",
        "units": "Pa",
    }
    assert invalid is None


def test_redox_pressure_numeric_pin_and_generic_default_after_move() -> None:
    identity = replace(
        F.pref_identity(),
        total_pressure_Pa=State.of(Decimal("25000")),
    )
    observation = F.observation(
        "pressure-pin",
        "pressure-pin-experiment",
        identity,
        Decimal("1"),
        source_id="pressure-pin-source",
    )

    pressure = _resolve_score_total_pressure(
        identity, Quantity.FE3_FE2_RATIO, observation=observation
    )

    assert pressure == (0.25, identity.total_pressure_Pa, None, None)

    missing_identity = replace(
        identity,
        total_pressure_Pa=State.unknown("not printed"),
    )
    missing_observation = replace(observation, identity=missing_identity)

    missing = _resolve_score_total_pressure(
        missing_identity,
        Quantity.FE3_FE2_RATIO,
        observation=missing_observation,
    )

    assert missing[0] == 1.01325
    assert missing[1] == State.of(Decimal("101325"))
    assert missing[2] is not None
    assert missing[2].kind is NoticeKind.ASSUMED_PRESSURE
    assert missing[2].policy["id"] == "DP-001"
    assert missing[3] is None


def test_knudsen_pressure_fallback_is_proposed_and_separate() -> None:
    experiment = F.kems_experiment()
    identity = replace(
        F.pref_identity(),
        total_pressure_Pa=State.unknown("in-cell total pressure is unprinted"),
    )

    pressure_bar, state, notice, invalid = _resolve_score_total_pressure(
        identity,
        Quantity.FE3_FE2_RATIO,
        experiment=experiment,
    )

    assert experiment.method.value is MethodToken.KNUDSEN_EFFUSION
    assert pressure_bar == _DEFAULT_PRESSURE_BAR
    assert state == State.of(Decimal(str(_DEFAULT_PRESSURE_BAR)) * PA_PER_BAR_DEC)
    assert notice is not None
    assert notice.kind is NoticeKind.ASSUMED_PRESSURE
    assert notice.policy == {
        "id": "DP-100",
        "version": "v1",
        "status": "proposed",
        "value": str(_DEFAULT_PRESSURE_BAR),
        "units": "bar",
    }
    assert "PROPOSED-POLICY" in flagged_strata((notice,))
    assert invalid is None


def test_method_fixed_ambient_pressure_uses_dp002() -> None:
    for evidence_class, quote in (
        (AmbientPressureEvidenceClass.GAS_MIXING, "gas-mixing furnace"),
        (
            AmbientPressureEvidenceClass.ATMOSPHERIC_PRESSURE_STATEMENT,
            "in air",
        ),
    ):
        experiment = replace(
            F.tabulation_experiment(),
            ambient_pressure_evidence=AmbientPressureEvidence(
                evidence_class=evidence_class,
                quote=quote,
                locator=F.loc(page=1, paragraph="Experimental setup"),
            ),
        )
        identity = replace(
            F.pref_identity(),
            total_pressure_Pa=State.unknown("total pressure is not printed"),
        )

        pressure_bar, state, notice, invalid = _resolve_score_total_pressure(
            identity,
            Quantity.ACTIVITY,
            experiment=experiment,
        )

        assert pressure_bar == 1.01325
        assert state == State.of(Decimal("101325"))
        assert notice is not None
        assert notice.kind is NoticeKind.METHOD_IMPLIED
        assert notice.policy == {
            "id": "DP-002",
            "version": "v1",
            "status": "canonical",
            "value": "101325",
            "units": "Pa",
        }
        assert "method_implied" in flagged_strata((notice,))
        assert invalid is None


def test_policy_metadata_round_trips_as_a_structured_notice_field() -> None:
    from simulator.battery.migrate import _notice_from_plain, to_plain
    from simulator.battery.records import Notice

    notice = Notice(
        kind=NoticeKind.ASSUMED_PRESSURE,
        affected_quantities=(Quantity.ACTIVITY,),
        reason="default_total_pressure_Pa=101325; original_reason=not printed",
        origin="test:pressure",
        policy={
            "id": "DP-001",
            "version": "v1",
            "status": "canonical",
            "value": "101325",
            "units": "Pa",
        },
    )

    assert _notice_from_plain(to_plain(notice)) == notice

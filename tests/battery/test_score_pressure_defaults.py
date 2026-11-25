from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from simulator.battery.default_policy import resolve as resolve_policy
from simulator.battery.enums import (
    AmbientPressureEvidenceClass,
    Engine,
    DEFAULT_POLICY_NOTICE_KINDS,
    MethodToken,
    NoticeKind,
    Quantity,
)
from simulator.battery.identity import PA_PER_BAR_DEC
from simulator.battery.records import (
    AmbientPressureEvidence,
    Notice,
    NoticePolicy,
    State,
)
from simulator.battery.fo2_ladder import fo2_tier_notice, resolve_row_fo2
from simulator.battery.score import (
    flagged_strata,
    _resolve_score_total_pressure,
    _pressure_refusal,
    total_pressure_bar_for_score,
)
from tests.battery import factories as F


def test_each_default_policy_flag_has_one_matching_stratum() -> None:
    policy = NoticePolicy("DP-001", 1, "canonical", "101325", "Pa")
    notices = tuple(
        Notice(
            kind=kind,
            affected_quantities=(Quantity.ACTIVITY,),
            reason="default policy flag",
            origin="test",
            policy=policy,
        )
        for kind in DEFAULT_POLICY_NOTICE_KINDS
    )

    strata = flagged_strata(notices)

    for kind in DEFAULT_POLICY_NOTICE_KINDS:
        assert strata.count(kind.value) == 1


def _without_run_pressure(experiment):
    environment = replace(
        experiment.pressure_environment,
        total_pressure_Pa=replace(
            experiment.pressure_environment.total_pressure_Pa,
            state=State.unknown("total pressure is not printed"),
        ),
    )
    return replace(experiment, pressure_environment=environment)


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
    assert notice.policy == NoticePolicy(
        "DP-001", 1, "canonical", "101325", "Pa"
    )
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
    missing_point_conditions = dict(observation.point_conditions or {})
    missing_point_conditions.pop("total_pressure_Pa", None)
    missing_observation = replace(
        observation,
        identity=missing_identity,
        point_conditions=missing_point_conditions,
    )

    missing = _resolve_score_total_pressure(
        missing_identity,
        Quantity.FE3_FE2_RATIO,
        observation=missing_observation,
    )

    assert missing[0] == 1.01325
    assert missing[1] == State.of(Decimal("101325"))
    assert missing[2] is not None
    assert missing[2].kind is NoticeKind.ASSUMED_PRESSURE
    assert missing[2].policy is not None
    assert missing[2].policy.id == "DP-001"
    assert missing[3] is None


def test_knudsen_pressure_uses_dp004_and_its_own_flag() -> None:
    experiment = _without_run_pressure(F.kems_experiment())
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
    assert pressure_bar is None
    assert state is None
    assert notice is not None
    assert notice.kind is NoticeKind.EFFUSION_REGIME_ASSUMED
    assert notice.policy == NoticePolicy(
        "DP-004",
        1,
        "canonical",
        "P_total(cell) = sum of measured partial pressures",
        "Pa",
    )
    assert "effusion_regime_assumed" in flagged_strata((notice,))
    assert invalid is None


def test_method_fixed_ambient_pressure_uses_dp002() -> None:
    for evidence_class, quote in (
        (AmbientPressureEvidenceClass.GAS_MIXING, "gas-mixing furnace"),
        (AmbientPressureEvidenceClass.GAS_FLOW, "gas flow"),
        (AmbientPressureEvidenceClass.VENTED, "vented run"),
        (
            AmbientPressureEvidenceClass.ATMOSPHERIC_PRESSURE_STATEMENT,
            "in air",
        ),
    ):
        experiment = replace(
            _without_run_pressure(F.tabulation_experiment()),
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
        assert notice.policy == NoticePolicy(
            "DP-002", 1, "canonical", "101325", "Pa"
        )
        assert "method_implied" in flagged_strata((notice,))
        assert invalid is None


def test_oxide_reference_pressure_uses_the_defined_pressure() -> None:
    identity = replace(
        F.pref_identity(),
        total_pressure_Pa=State.unknown("ambient pressure unknown"),
    )
    pressure = _resolve_score_total_pressure(
        identity,
        Quantity.P_REFERENCE,
        experiment=F.tabulation_experiment(),
        engine=Engine.INTERNAL_ANALYTICAL,
    )

    assert pressure == (1.0, State.of(Decimal("100000")), None, None)


def test_closed_system_pressure_uses_supported_path_or_typed_capability_gap() -> None:
    experiment = _without_run_pressure(F.tabulation_experiment())
    experiment = replace(
        experiment,
        sample=replace(
            experiment.sample,
            container=F.located("sealed silica ampoule"),
        ),
    )
    identity = replace(
        F.pref_identity(),
        total_pressure_Pa=State.unknown("closed ampoule has no run background"),
    )

    supported = _resolve_score_total_pressure(
        identity,
        Quantity.P_PARTIAL,
        experiment=experiment,
        engine=Engine.OPENIMCC,
    )
    assert supported == (None, None, None, None)

    unsupported = _resolve_score_total_pressure(
        identity,
        Quantity.ACTIVITY,
        experiment=experiment,
        engine=Engine.ALPHAMELTS,
    )
    assert unsupported[3] == "closed_system_engine_capability_gap"
    assert unsupported[3] is not None
    refusal, detail = _pressure_refusal(
        Engine.ALPHAMELTS, Quantity.ACTIVITY, unsupported[3]
    )
    assert refusal.value == "unsupported"
    assert detail["engine"] == Engine.ALPHAMELTS.value
    assert detail["quantity"] == Quantity.ACTIVITY.value


def test_unstated_method_vapour_pressure_waits_for_partial_sum_and_rate_names_gap() -> None:
    experiment = replace(
        _without_run_pressure(F.tabulation_experiment()),
        method=State.unknown("method not printed"),
    )
    identity = replace(
        F.pref_identity(),
        quantity=State.of(Quantity.P_PARTIAL),
        total_pressure_Pa=State.unknown("total pressure not printed"),
    )
    vapor = _resolve_score_total_pressure(
        identity,
        Quantity.P_PARTIAL,
        experiment=experiment,
        engine=Engine.INTERNAL_ANALYTICAL,
    )
    assert vapor == (None, None, None, None)

    rate = _resolve_score_total_pressure(
        identity,
        Quantity.EVAPORATION_RATE,
        experiment=experiment,
        engine=Engine.INTERNAL_ANALYTICAL,
    )
    assert rate[3] == "rate_quantity_requires_method_for_ambient_regime"


def test_vacuum_method_uses_dp019_method_implied_pressure() -> None:
    experiment = _without_run_pressure(
        F.tabulation_experiment(method=MethodToken.VACUUM_CHAMBER_PYROLYSIS)
    )
    identity = replace(
        F.pref_identity(),
        total_pressure_Pa=State.unknown("not printed under vacuum"),
    )

    pressure_bar, state, notice, invalid = _resolve_score_total_pressure(
        identity, Quantity.ACTIVITY, experiment=experiment
    )

    dp019_pa = resolve_policy("DP-019").value
    assert pressure_bar == float(dp019_pa / PA_PER_BAR_DEC)
    assert state == State.of(dp019_pa)
    assert notice is not None
    assert notice.kind is NoticeKind.METHOD_IMPLIED
    assert notice.policy is not None
    assert notice.policy.id == "DP-019"
    assert notice.policy.value == "0.1"
    assert notice.policy.units == "Pa"
    assert invalid is None


def test_air_default_in_oxygen_ladder_uses_dp003() -> None:
    experiment = replace(
        _without_run_pressure(F.tabulation_experiment()),
        ambient_pressure_evidence=AmbientPressureEvidence(
            evidence_class=AmbientPressureEvidenceClass.ATMOSPHERIC_PRESSURE_STATEMENT,
            quote="in air",
            locator=F.loc(page=1, paragraph="Experimental setup"),
        ),
    )

    resolved = resolve_row_fo2(
        experiment, None, None, temperature_K=1200.0, quantity=Quantity.ACTIVITY
    )
    assert resolved is not None
    notice = fo2_tier_notice(Quantity.ACTIVITY, resolved)

    assert notice.kind is NoticeKind.METHOD_IMPLIED
    assert notice.policy is not None
    assert notice.policy.id == "DP-003"
    assert notice.policy.status == "canonical"
    expected_pa = resolve_policy("DP-003", P_total=resolve_policy("DP-002").value).value
    assert Decimal(notice.policy.value) == expected_pa
    assert notice.policy.units == "Pa"


def test_policy_metadata_round_trips_as_a_structured_notice_field() -> None:
    from simulator.battery.migrate import _notice_from_plain, to_plain
    from simulator.battery.records import Notice

    notice = Notice(
        kind=NoticeKind.ASSUMED_PRESSURE,
        affected_quantities=(Quantity.ACTIVITY,),
        reason="default_total_pressure_Pa=101325; original_reason=not printed",
        origin="test:pressure",
        policy=NoticePolicy("DP-001", "v1", "canonical", "101325", "Pa"),
    )

    assert _notice_from_plain(to_plain(notice)) == notice

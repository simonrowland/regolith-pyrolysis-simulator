"""Compilation tier: series cells as points, engine thermo, separate headline.

Printed anchors are JANAF Na-014 Na2O(l) at 2200 K: ΔfG° = −3.886 kJ/mol,
log10 Kf = 0.092. The Ellingham segment at that temperature is
4 Na(g)+O2 → 2 Na2O(l), dG = −7.4056 kJ/mol O2.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from fractions import Fraction

from simulator.battery.compilation_tier import (
    _parse_product,
    compilation_series_points,
    predict_thermo_attempt,
)
from simulator.battery.enums import (
    AdmissionStatus,
    Authority,
    Engine,
    EvidenceClass,
    ExecutionState,
    NoticeKind,
    PerBasis,
    Phase,
    Polymorph,
    Quantity,
    RefusalReason,
    ResidualStatus,
    SourceRelation,
    ValueKind,
)
from simulator.battery.identity import log10K_from_delta_fG_kJ_mol
from simulator.battery.records import Species, State, Uncertainty, Value
from simulator.battery.score import (
    EnginePrediction,
    ScoreContext,
    compile_residual,
    comparison_candidates,
    decision_band_for,
    headline_rows,
    render_score_report,
)
from simulator.chemistry.ellingham_thermo import ELLINGHAM_FIT_SEGMENTS
from tests.battery import factories as F

_PRINTED_DFG = Decimal("-3.886")
_PRINTED_LOGK = Decimal("0.092")
_T = Decimal("2200")


def _na2o_liquid(quantity: Quantity = Quantity.DELTA_FG):
    identity = F.oxide_identity(
        "Na2O",
        Phase.L,
        T_K=_T,
        per=PerBasis.MOL_SPECIES,
        metal_formula="Na",
        nu_metal=Fraction(4),
        nu_o2=Fraction(1),
    )
    if quantity is not Quantity.DELTA_FG:
        identity = replace(identity, quantity=quantity)
    return identity


def _context(*observations, works=None, experiments=None, origins=None):
    work = replace(
        F.work(),
        source_ids=("work-1", "janaf-4th", "nist-webbook", "nasa-glenn"),
    )
    experiment = F.tabulation_experiment()
    return ScoreContext(
        works=works or {work.work_id: work},
        experiments=experiments or {experiment.experiment_id: experiment},
        observations={obs.observation_id: obs for obs in observations},
        origins=origins or {},
        extract_review={obs.source_id or "": None for obs in observations},
        hostname="test",
    )


def test_every_ellingham_segment_names_one_product() -> None:
    for metal, segments in ELLINGHAM_FIT_SEGMENTS.items():
        for segment in segments:
            product = _parse_product(segment)
            assert product is not None, (metal, segment.phase_basis)
            assert product.formula


def test_series_cells_are_points_and_intervals_are_not_midpoints() -> None:
    identity = _na2o_liquid()
    experiment = F.tabulation_experiment()
    series = F.observation(
        "na2o-series",
        experiment.experiment_id,
        replace(identity, temperature_K=State.unknown("series")),
        Decimal("0"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    series = replace(
        series,
        value=Value(
            ValueKind.SERIES,
            series=(
                (_T, _PRINTED_DFG),
                (Decimal("2100"), Decimal("-10")),
            ),
        ),
    )
    points = compilation_series_points(series, "compilations-janaf/janaf-Na.yaml")
    assert [point.observation_id for point in points] == [
        "na2o-series#t2200v-3.886",
        "na2o-series#t2100v-10",
    ]
    swapped = replace(
        series,
        value=Value(
            ValueKind.SERIES,
            series=(
                (Decimal("2100"), Decimal("-10")),
                (_T, _PRINTED_DFG),
            ),
        ),
    )
    assert [point.observation_id for point in compilation_series_points(swapped, "compilations-janaf/janaf-Na.yaml")] == [
        "na2o-series#t2100v-10",
        "na2o-series#t2200v-3.886",
    ]
    assert points[0].value.point == _PRINTED_DFG
    assert points[1].value.point == Decimal("-10")
    assert points[0].identity.temperature_K.value == _T
    # Not the mean of −3.886 and −10.
    assert points[0].value.point != ( _PRINTED_DFG + Decimal("-10") ) / 2
    ctx = _context(series)
    assert comparison_candidates(ctx) == ()


def test_transition_temperature_and_empirical_series_stay_series() -> None:
    identity = _na2o_liquid(Quantity.TRANSITION_TEMPERATURE)
    experiment = F.tabulation_experiment()
    transition = F.observation(
        "transition",
        experiment.experiment_id,
        identity,
        Decimal("0"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
    )
    transition = replace(
        transition,
        value=Value(ValueKind.SERIES, series=((Decimal("1405"), Decimal("1405")),)),
    )
    assert compilation_series_points(transition, "compilations-janaf/x.yaml") == (
        transition,
    )
    empirical = replace(
        transition,
        observation_id="empirical-series",
        evidence=replace(
            transition.evidence, class_=State.of(EvidenceClass.MEASURED_DIRECT)
        ),
        value=Value(ValueKind.SERIES, series=((Decimal("1"), Decimal("2")), (Decimal("3"), Decimal("4")))),
    )
    assert compilation_series_points(empirical, "kems.yaml") == (empirical,)


def test_ellingham_na2o_matches_printed_janaf_within_fit_bound() -> None:
    identity = _na2o_liquid()
    attempt = predict_thermo_attempt(
        Engine.INTERNAL_ANALYTICAL,
        F.observation("na2o", "exp-1", identity, _PRINTED_DFG),
    )
    assert attempt.value is not None
    assert attempt.unit == "kJ_per_declared_mol_basis"
    residual = attempt.value - _PRINTED_DFG
    # 0.442 kJ/mol O2 / n_ox 2 = 0.221 kJ/mol oxide.
    assert abs(residual - Decimal("0.1832")) < Decimal("0.001")
    assert abs(residual) < Decimal("0.221")
    assert attempt.refusal_reason is None


def test_log10_kf_follows_delta_fg_and_has_no_kj_band() -> None:
    identity = _na2o_liquid(Quantity.LOG10_KF)
    attempt = predict_thermo_attempt(
        Engine.INTERNAL_ANALYTICAL,
        F.observation("na2o-k", "exp-1", identity, _PRINTED_LOGK),
    )
    assert attempt.value is not None
    gibbs = predict_thermo_attempt(
        Engine.INTERNAL_ANALYTICAL,
        F.observation("na2o-g", "exp-1", _na2o_liquid(), _PRINTED_DFG),
    )
    assert attempt.value == log10K_from_delta_fG_kJ_mol(gibbs.value, _T)
    assert abs(attempt.value - _PRINTED_LOGK) < Decimal("0.01")
    assert decision_band_for(Quantity.LOG10_KF, SourceRelation.INDEPENDENT) is None
    assert decision_band_for(Quantity.DELTA_FG, SourceRelation.INDEPENDENT) is not None
    for quantity in (
        Quantity.CP,
        Quantity.S,
        Quantity.DELTA_FH,
        Quantity.H_MINUS_H298,
    ):
        assert decision_band_for(quantity, SourceRelation.INDEPENDENT) is None
        assert decision_band_for(quantity, SourceRelation.SAME_INPUT) is None


def test_zero_kelvin_row_does_not_blank_later_series_points() -> None:
    from simulator.battery.compilation_tier import compilation_tier_census

    identity = replace(_na2o_liquid(), temperature_K=State.unknown("series"))
    experiment = F.tabulation_experiment()
    series = F.observation(
        "na2o-series",
        experiment.experiment_id,
        identity,
        Decimal("0"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    series = replace(
        series,
        value=Value(
            ValueKind.SERIES,
            series=((Decimal("0"), Decimal("0")), (_T, _PRINTED_DFG)),
        ),
    )
    out = compilation_tier_census(
        _context(series),
        engines=(Engine.VAPOROCK,),
        audit_compile_residual=False,
    )
    row = out["rows"][0]
    assert row["reachable"] == 2
    assert row["refused"]["identity_unknown:temperature-not-positive"] == 1
    assert row["refused"]["unsupported:engine-thermo-does-not-emit"] == 1


def test_non_positive_temperature_is_a_refusal_not_an_exception() -> None:
    identity = replace(_na2o_liquid(), temperature_K=State.of(Decimal("0")))
    attempt = predict_thermo_attempt(
        Engine.INTERNAL_ANALYTICAL,
        F.observation("na2o-0", "exp-1", identity, _PRINTED_DFG),
    )
    assert attempt.value is None
    assert attempt.refusal_detail["reason"] == "temperature-not-positive"


def test_ellingham_does_not_emit_zero_for_cp_or_uncovered_formula() -> None:
    cp = predict_thermo_attempt(
        Engine.INTERNAL_ANALYTICAL,
        F.observation("na2o-cp", "exp-1", _na2o_liquid(Quantity.CP), Decimal("104.6")),
    )
    assert cp.value is None
    assert cp.refusal_detail["reason"] == "ellingham-emits-reaction-dg-only"
    argon = replace(
        _na2o_liquid(),
        species=Species("Ar", Phase.G),
    )
    missing = predict_thermo_attempt(
        Engine.INTERNAL_ANALYTICAL,
        F.observation("ar", "exp-1", argon, Decimal("0")),
    )
    assert missing.value is None
    assert missing.value != Decimal("0")
    assert missing.refusal_detail["reason"] == "engine-thermo-does-not-emit"


def test_melt_engines_refuse_apparent_gibbs_as_delta_fg() -> None:
    attempt = predict_thermo_attempt(
        Engine.MAGEMIN,
        F.observation("na2o", "exp-1", _na2o_liquid(), _PRINTED_DFG),
    )
    assert attempt.value is None
    assert attempt.refusal_detail["reason"] == "apparent-gibbs-is-not-delta-fG"
    vaporock = predict_thermo_attempt(
        Engine.VAPOROCK,
        F.observation("na2o", "exp-1", _na2o_liquid(), _PRINTED_DFG),
    )
    assert vaporock.value is None
    assert vaporock.refusal_detail["reason"] == "engine-thermo-does-not-emit"


def test_pure_phase_enthalpy_increment_uses_injected_accessor() -> None:
    identity = F.oxide_identity(
        "MgO",
        Phase.CR,
        T_K=Decimal("1000"),
        per=PerBasis.MOL_SPECIES,
        metal_formula="Mg",
        polymorph=Polymorph.PERICLASE.value,
    )
    identity = replace(identity, quantity=Quantity.H_MINUS_H298)

    class _Props:
        def __init__(self, enthalpy):
            self.formula = "MgO"
            self.H_J_mol = enthalpy
            self.Cp_J_K_mol = 40.0
            self.S_J_K_mol = 30.0
            self.absences = ()

    def pure_phase(_engine, _symbol, temperature_K, _pressure_bar):
        if abs(temperature_K - 298.15) < 1e-6:
            return _Props(1000.0)
        return _Props(5000.0)

    attempt = predict_thermo_attempt(
        Engine.MAGEMIN,
        F.observation("mgo", "exp-1", identity, Decimal("4")),
        pure_phase=pure_phase,
    )
    assert attempt.value == Decimal("4")
    assert attempt.unit == "kJ_per_declared_mol_basis"
    assert attempt.refusal_reason is None


def test_compilation_tier_is_beside_measured_and_same_source_is_flagged() -> None:
    experiment = F.tabulation_experiment()
    identity = _na2o_liquid()
    reference = F.observation(
        "na2o-janaf",
        experiment.experiment_id,
        identity,
        _PRINTED_DFG,
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        admission=AdmissionStatus.PENDING,
        source_id="nist-janaf-4th",
    )
    ctx = _context(reference)
    residual, candidate = compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=ctx,
        comparison_ids=set(),
    )
    assert reference.evidence.class_.value is EvidenceClass.COMPILATION_ASSESSED
    assert reference.admission.status is AdmissionStatus.PENDING
    assert residual.score_eligible is False
    assert "reference_measured_evidence" in residual.exclusions
    assert residual.numeric is not None
    assert residual.numeric.unit == "kJ_per_declared_mol_basis"
    assert residual.numeric.operation.value == "absolute"
    assert residual.source_relation is SourceRelation.SAME_INPUT
    assert residual.status is ResidualStatus.MISMATCH
    assert any(
        notice.kind is NoticeKind.DERIVATION_USES_COMPILATION
        and "nist-janaf-4th" in notice.reason
        and "none" in notice.reason
        for notice in residual.notices
    )
    assert candidate is not None
    assert candidate.evidence.class_.value is EvidenceClass.ENGINE_PREDICTION
    assert comparison_candidates(ctx) == ()

    pankratz_work = replace(F.work("pankratz-work"), source_ids=("pankratz-work",))
    janaf_work = replace(
        F.work("janaf-work"),
        source_ids=("janaf-work", "janaf-4th", "nist-webbook", "nasa-glenn"),
    )
    janaf_exp = F.tabulation_experiment(experiment_id="janaf-exp", work_id="janaf-work")
    pank_exp = F.tabulation_experiment(experiment_id="pank-exp", work_id="pankratz-work")
    janaf_row = replace(reference, observation_id="janaf-row", experiment_id="janaf-exp")
    pank_row = replace(
        reference,
        observation_id="pank-row",
        experiment_id="pank-exp",
        source_id="pankratz-1984",
    )
    independent_ctx = ScoreContext(
        works={janaf_work.work_id: janaf_work, pankratz_work.work_id: pankratz_work},
        experiments={
            janaf_exp.experiment_id: janaf_exp,
            pank_exp.experiment_id: pank_exp,
        },
        observations={
            janaf_row.observation_id: janaf_row,
            pank_row.observation_id: pank_row,
        },
        hostname="test",
    )
    independent, _ = compile_residual(
        pank_row,
        Engine.INTERNAL_ANALYTICAL,
        context=independent_ctx,
        comparison_ids=set(),
    )
    assert independent.source_relation is SourceRelation.INDEPENDENT
    assert independent.score_eligible is False
    assert independent.numeric is not None
    assert independent.status is ResidualStatus.MATCH
    assert all(
        notice.kind is not NoticeKind.DERIVATION_USES_COMPILATION
        for notice in independent.notices
    )

    ledger = replace(reference, observation_id="ledger-row", evidence=replace(
        reference.evidence, class_=State.of(EvidenceClass.MEASURED_DIRECT)
    ))
    ledger_ctx = replace(
        ctx,
        observations={ledger.observation_id: ledger, reference.observation_id: reference},
        origins={ledger.observation_id: "gibbs_battery_residual_ledger.yaml"},
    )
    from simulator.battery.records import Execution
    from simulator.battery.score import EnginePrediction

    forced, _ = compile_residual(
        ledger,
        Engine.INTERNAL_ANALYTICAL,
        context=ledger_ctx,
        comparison_ids={ledger.observation_id},
        predict=lambda engine, obs, **kwargs: EnginePrediction(
            engine=engine,
            channel="internal-analytical",
            execution=Execution(state=ExecutionState.PRODUCED, call_evidence="test"),
            value=_PRINTED_DFG,
            unit="kJ_per_declared_mol_basis",
            authority=Authority.CERTIFIED,
            coefficient_sources=("nasa-cea-thermo",),
            lineage_complete=True,
            identity=obs.identity,
        ),
    )
    assert forced.source_relation is SourceRelation.UNKNOWN
    assert forced.status is ResidualStatus.NO_BAND
    assert forced.numeric is not None
    assert forced.numeric.decision_band is None
    assert forced.refusal is None

    both = replace(
        independent_ctx,
        observations={
            reference.observation_id: reference,
            pank_row.observation_id: pank_row,
        },
        experiments={
            **independent_ctx.experiments,
            experiment.experiment_id: experiment,
        },
        origins={
            reference.observation_id: "compilations-janaf/janaf-Na.yaml",
            pank_row.observation_id: "compilations-pankratz-1984-usbm-b677.yaml",
        },
    )
    report = render_score_report(
        (residual, independent),
        context=both,
        engines=(Engine.INTERNAL_ANALYTICAL,),
    )
    assert "## Measured tier" in report
    assert "## Compilation tier" in report
    assert "never added" in report
    measured = [
        row
        for row in headline_rows((residual, independent), context=both)
        if row["rail"] == "thermochemistry" and row["engine"] == "internal-analytical"
    ]
    assert measured[0]["n_candidates"] == 0
    assert measured[0]["n_refused"] == 0
    assert measured[0]["n_scored"] == 0
    assert "| internal-analytical | 2 |" in report


def test_out_of_range_reaction_is_not_formation_gibbs() -> None:
    identity = F.oxide_identity(
        "K2O",
        Phase.CR,
        T_K=Decimal("298.15"),
        per=PerBasis.MOL_SPECIES,
        metal_formula="K",
        nu_metal=Fraction(4),
        nu_o2=Fraction(1),
        polymorph="beta",
    )
    identity = replace(
        identity,
        species=replace(identity.species, polymorph=State.unknown("no polymorph named")),
    )
    printed = Decimal("-322.094")
    observation = F.observation(
        "k2o-298",
        "exp-1",
        identity,
        printed,
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    attempt = predict_thermo_attempt(Engine.INTERNAL_ANALYTICAL, observation)
    assert attempt.value is None
    assert attempt.refusal_detail["reason"] == "ellingham-formation-outside-segment"
    ctx = _context(
        observation,
        origins={observation.observation_id: "compilations-janaf/janaf-K.yaml"},
    )
    residual, _candidate = compile_residual(
        observation,
        Engine.INTERNAL_ANALYTICAL,
        context=ctx,
        comparison_ids=set(),
    )
    assert residual.numeric is None
    assert residual.status is ResidualStatus.REFUSED
    report = render_score_report(
        (residual,),
        context=ctx,
        engines=(Engine.INTERNAL_ANALYTICAL,),
    )
    census = report.split("## Refusal census", 1)[1].split("## Admission", 1)[0]
    assert "ellingham-formation-outside-segment" not in census
    assert "| internal-analytical | 1 | 1 |" in report
    beta = F.oxide_identity(
        "Na2O",
        Phase.CR,
        T_K=Decimal("1100"),
        per=PerBasis.MOL_SPECIES,
        metal_formula="Na",
        nu_metal=Fraction(4),
        nu_o2=Fraction(1),
        polymorph="beta",
    )
    in_band = predict_thermo_attempt(
        Engine.INTERNAL_ANALYTICAL,
        F.observation("na2o-beta", "exp-1", beta, Decimal("-267.206")),
    )
    assert in_band.value is not None
    assert abs(in_band.value - Decimal("-267.206")) < Decimal("0.001")
    alumina = F.oxide_identity(
        "Al2O3",
        Phase.L,
        T_K=Decimal("2900"),
        per=PerBasis.MOL_SPECIES,
        metal_formula="Al",
        nu_metal=Fraction(4, 3),
        nu_o2=Fraction(1),
    )
    past_end = predict_thermo_attempt(
        Engine.INTERNAL_ANALYTICAL,
        F.observation("al2o3-2900", "exp-1", alumina, Decimal("-754.429")),
    )
    assert past_end.value is None
    assert past_end.refusal_detail["reason"] == "ellingham-formation-outside-segment"


def test_quoted_compilation_row_is_not_omitted() -> None:
    identity = replace(_na2o_liquid(), quantity=Quantity.DELTA_FH)
    quoted = F.observation(
        "quoted-dh",
        "exp-1",
        identity,
        Decimal("-2594.1"),
        evidence=EvidenceClass.QUOTED_ATTRIBUTED,
        source_id="hemingway-haas-robinson-1982-usgs-b1544",
    )
    origin = "compilations-hemingway-haas-robinson-1982-usgs-b1544/table.yaml"
    ctx = _context(quoted, origins={quoted.observation_id: origin})
    residual, _candidate = compile_residual(
        quoted,
        Engine.INTERNAL_ANALYTICAL,
        context=ctx,
        comparison_ids=set(),
    )
    assert residual.score_eligible is False
    report = render_score_report(
        (residual,),
        context=ctx,
        engines=(Engine.INTERNAL_ANALYTICAL,),
    )
    assert "`compilations-hemingway-haas-robinson-1982-usgs-b1544`" in report
    assert "`delta_fH`" in report
    measured = [
        row
        for row in headline_rows((residual,), context=ctx)
        if row["rail"] == "thermochemistry" and row["engine"] == "internal-analytical"
    ]
    assert measured[0]["n_candidates"] == 0
    assert measured[0]["n_refused"] == 0


def test_nasa_glenn_is_same_source_and_pankratz_bulletins_are_not() -> None:
    nasa_exp = F.tabulation_experiment(experiment_id="nasa-exp", work_id="nasa-work")
    janaf_work = replace(
        F.work("janaf-work"),
        source_ids=("janaf-work", "janaf-4th", "nist-webbook"),
    )
    nasa_work = replace(F.work("nasa-work"), source_ids=("nasa-work", "nasa-glenn"))
    row = F.observation(
        "nasa-row",
        nasa_exp.experiment_id,
        _na2o_liquid(),
        _PRINTED_DFG,
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nasa-glenn",
    )
    ctx = ScoreContext(
        works={janaf_work.work_id: janaf_work, nasa_work.work_id: nasa_work},
        experiments={nasa_exp.experiment_id: nasa_exp},
        observations={row.observation_id: row},
        hostname="test",
    )
    residual, _candidate = compile_residual(
        row,
        Engine.INTERNAL_ANALYTICAL,
        context=ctx,
        comparison_ids=set(),
    )
    assert residual.source_relation is SourceRelation.SAME_INPUT
    assert any(
        notice.kind is NoticeKind.DERIVATION_USES_COMPILATION for notice in residual.notices
    )


def test_pure_phase_value_is_not_reused_at_the_next_temperature(monkeypatch) -> None:
    from simulator.battery.compilation_tier import ThermoAttempt, compilation_tier_census

    calls: list[Decimal] = []

    def fake(engine, observation, **kwargs):
        del engine, kwargs
        temperature = observation.identity.temperature_K.value
        calls.append(temperature)
        if temperature <= 0:
            return ThermoAttempt(
                value=None,
                unit=None,
                authority=Authority.REFUSED,
                notices=(),
                refusal_reason=RefusalReason.IDENTITY_UNKNOWN,
                refusal_detail={"reason": "temperature-not-positive"},
                call_evidence="t0",
            )
        return ThermoAttempt(
            value=temperature,
            unit="J_per_declared_mol_basis_per_K",
            authority=Authority.BRIDGE,
            notices=(),
            refusal_reason=None,
            refusal_detail={},
            call_evidence=f"T={temperature}",
        )

    monkeypatch.setattr("simulator.battery.compilation_tier.predict_thermo_attempt", fake)
    identity = replace(_na2o_liquid(Quantity.CP), temperature_K=State.unknown("series"))
    series = F.observation(
        "cp-series",
        "exp-1",
        identity,
        Decimal("0"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    series = replace(
        series,
        value=Value(
            ValueKind.SERIES,
            series=(
                (Decimal("0"), Decimal("0")),
                (Decimal("500"), Decimal("500")),
                (Decimal("1500"), Decimal("1500")),
            ),
        ),
    )
    ctx = _context(series)
    compilation_tier_census(
        ctx,
        engines=(Engine.VAPOROCK,),
        invoke_pure_phase=True,
        audit_compile_residual=False,
    )
    assert calls == [Decimal("0"), Decimal("500"), Decimal("1500")]
    calls.clear()
    compilation_tier_census(
        ctx,
        engines=(Engine.VAPOROCK,),
        invoke_pure_phase=False,
        audit_compile_residual=False,
    )
    assert calls == [Decimal("0"), Decimal("500")]


def test_report_rebuilt_from_payloads_keeps_the_tier_split() -> None:
    from simulator.battery.score import render_score_report_from_payloads, residual_to_plain

    experiment = F.tabulation_experiment()
    reference = F.observation(
        "na2o-janaf",
        experiment.experiment_id,
        _na2o_liquid(),
        _PRINTED_DFG,
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        admission=AdmissionStatus.PENDING,
        source_id="nist-janaf-4th",
    )
    ctx = _context(
        reference,
        origins={reference.observation_id: "compilations-janaf/janaf-Na.yaml"},
    )
    residual, _candidate = compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=ctx,
        comparison_ids=set(),
    )
    report = render_score_report_from_payloads(
        [residual_to_plain(residual)],
        engines=(Engine.INTERNAL_ANALYTICAL,),
        hostname="test",
        observations=ctx.observations,
        origins=ctx.origins,
    )
    assert "## Measured tier" in report
    assert "## Compilation tier" in report
    assert "| thermochemistry | internal-analytical | 0 | 0 | 0 |" in report
    assert "| internal-analytical | 1 |" in report
    census = report.split("## Refusal census", 1)[1]
    assert "internal-analytical" not in census or "0 |" in report


def test_replaced_observation_is_not_served_from_the_work_cache() -> None:
    from simulator.battery.validate import _inputs_registered_under_work, _table_ids

    work = F.work()
    experiment = F.tabulation_experiment()
    identity = _na2o_liquid()
    first_obs = F.observation("obs-A", experiment.experiment_id, identity, Decimal("1"))
    second_obs = F.observation("obs-B", experiment.experiment_id, identity, Decimal("2"))
    observations = {first_obs.observation_id: first_obs}
    experiments = {experiment.experiment_id: experiment}
    works = {work.work_id: work}
    table_ids = _table_ids(works)
    first = _inputs_registered_under_work(work, observations, experiments, table_ids)
    assert "obs-A" in first
    observations.clear()
    observations[second_obs.observation_id] = second_obs
    second = _inputs_registered_under_work(work, observations, experiments, table_ids)
    assert "obs-A" not in second
    assert "obs-B" in second


def test_score_store_pairs_tables_from_one_index(monkeypatch) -> None:
    from simulator.battery.records import Execution
    from simulator.battery.score import score_store
    from simulator.battery.validate import _table_payloads, build_printed_thermo_index

    calls = {"full": 0, "indexed": 0}
    real = _table_payloads

    def wrapped(reference, observations, index=None):
        calls["full" if index is None else "indexed"] += 1
        return real(reference, observations, index)

    monkeypatch.setattr("simulator.battery.validate._table_payloads", wrapped)
    log_identity = replace(_na2o_liquid(), quantity=Quantity.LOG10_KF)
    delta = F.observation(
        "delta",
        "exp-1",
        _na2o_liquid(),
        Decimal("-3.886"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    log_row = F.observation(
        "logk",
        "exp-1",
        log_identity,
        Decimal("5"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    ctx = _context(
        delta,
        log_row,
        origins={
            delta.observation_id: "compilations-janaf/janaf-Na.yaml",
            log_row.observation_id: "compilations-janaf/janaf-Na.yaml",
        },
    )
    direct = _table_payloads(delta, ctx.observations)
    indexed = _table_payloads(
        delta, ctx.observations, build_printed_thermo_index(ctx.observations)
    )
    assert len(direct) == 1
    assert indexed == direct
    calls["full"] = 0
    calls["indexed"] = 0

    def predict(engine, observation, **kwargs):
        del kwargs
        return EnginePrediction(
            engine=engine,
            channel="internal-analytical",
            execution=Execution(state=ExecutionState.NOT_PROBED),
            refusal_reason=RefusalReason.UNSUPPORTED,
            refusal_detail={"reason": "test"},
            identity=observation.identity if hasattr(observation, "identity") else None,
        )

    residuals, _candidates = score_store(
        ctx,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        predict=predict,
    )
    refused = [row for row in residuals if row.reference in {"delta", "logk"}]
    assert len(refused) == 2
    assert all(
        row.refusal is not None and row.refusal.reason is RefusalReason.INVALID_SOURCE
        for row in refused
    )
    assert calls["full"] == 0
    assert calls["indexed"] == 2


def test_score_progress_total_is_series_cells(monkeypatch, capsys) -> None:
    from simulator.battery.records import Execution
    from simulator.battery.score import score_store

    ticks = iter([0.0, 61.0])
    monkeypatch.setattr(
        "simulator.battery.score.time.monotonic",
        lambda: next(ticks, 61.0),
    )
    identity = replace(_na2o_liquid(), temperature_K=State.unknown("series"))
    series = F.observation(
        "na2o-series",
        "exp-1",
        identity,
        Decimal("0"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    series = replace(
        series,
        value=Value(
            ValueKind.SERIES,
            series=((_T, _PRINTED_DFG), (Decimal("2100"), Decimal("-10"))),
        ),
    )
    ctx = _context(
        series,
        origins={series.observation_id: "compilations-janaf/janaf-Na.yaml"},
    )

    def predict(engine, observation, **kwargs):
        del kwargs
        return EnginePrediction(
            engine=engine,
            channel="internal-analytical",
            execution=Execution(state=ExecutionState.NOT_PROBED),
            refusal_reason=RefusalReason.UNSUPPORTED,
            refusal_detail={"reason": "test"},
            identity=observation.identity,
        )

    score_store(ctx, engines=(Engine.INTERNAL_ANALYTICAL,), predict=predict)
    printed = capsys.readouterr().out
    assert "score progress 1/2 " in printed
    assert "score progress 1/1 " not in printed

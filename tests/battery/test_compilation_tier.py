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
    ScoreContext,
    compile_residual,
    comparison_candidates,
    decision_band_for,
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
        source_ids=("work-1", "janaf-4th", "nist-webbook"),
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
        "na2o-series#0",
        "na2o-series#1",
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
        source_ids=("janaf-work", "janaf-4th", "nist-webbook"),
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
    assert forced.numeric is None
    assert forced.refusal is not None
    assert forced.refusal.reason is RefusalReason.DECISION_RULE_MISSING

    report = render_score_report(
        (residual, independent),
        context=independent_ctx,
        engines=(Engine.INTERNAL_ANALYTICAL,),
    )
    assert "## Measured tier" in report
    assert "## Compilation tier" in report
    assert "never added" in report

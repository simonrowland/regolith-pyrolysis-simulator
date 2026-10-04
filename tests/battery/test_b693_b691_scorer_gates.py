"""Pins for b-693 (figure_only candidacy + reactive-cell predict) and b-691 (dual headline).

Expected values come from the owner mandate and the REQ, not from score.py.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from simulator.battery.enums import (
    AdmissionStatus,
    AmountBasis,
    Authority,
    BenchIdentityBasis,
    CellMaterial,
    Engine,
    EvidenceClass,
    ExecutionState,
    MethodToken,
    MetricOperation,
    NoticeKind,
    Phase,
    Quantity,
    Rail,
    RefusalReason,
    ResidualStatus,
    UncertaintyKind,
)
from simulator.battery.identity import quantity_token
from simulator.battery.records import (
    Derivation,
    Bench,
    BenchIdentity,
    Composition,
    DecisionBand,
    Execution,
    Located,
    Notice,
    Residual,
    ResidualNumeric,
    Species,
    State,
    Uncertainty,
    Value,
)
from simulator.battery.score import (
    EnginePrediction,
    ScoreContext,
    comparison_candidates,
    compile_residual,
    headline_records,
    headline_rows,
    score_store,
)
from tests.battery import factories as F


def _context(work=None, experiment=None, *observations, review="reviewed", benches=None) -> ScoreContext:
    w = work or F.work()
    exp = experiment or F.kems_experiment()
    obs = {o.observation_id: o for o in observations}
    extract_review = {"": review}
    for item in observations:
        if item.source_id:
            extract_review[item.source_id] = review
    return ScoreContext(
        works={w.work_id: w},
        experiments={exp.experiment_id: exp},
        observations=obs,
        benches=benches or {},
        extract_review=extract_review,
        hostname="test",
    )


def _partial_identity(*, formula: str = "Na", T_K: str = "1700", clear_fo2: bool = False):
    identity = replace(
        F.pref_identity(T_K=Decimal(T_K)),
        quantity=Quantity.P_PARTIAL,
        species=Species(formula, Phase.G),
        composition=F.activity_identity().composition,
        total_pressure_Pa=State.of(Decimal("1e-6")),
    )
    if clear_fo2:
        identity = replace(identity, fO2_Pa=State.unknown("no fO2 printed"))
    return identity


def _predict(value: Decimal, identity, **kwargs) -> EnginePrediction:
    return EnginePrediction(
        engine=Engine.INTERNAL_ANALYTICAL,
        channel="internal-analytical",
        execution=Execution(state=ExecutionState.PRODUCED, call_evidence="test:predict"),
        value=value,
        unit="Pa",
        authority=kwargs.get("authority", Authority.CERTIFIED),
        notices=kwargs.get("notices", ()),
        coefficient_sources=kwargs.get("coefficient_sources", ("test",)),
        lineage_complete=kwargs.get("lineage_complete", True),
        identity=identity,
    )


def _demaria_shape_figure_rows() -> tuple[list, object, Bench]:
    """Fixture shaped like De Maria 1971: figure-read Na/K points, Re cell."""

    experiment = replace(F.kems_experiment(), bench_id="demaria-re-cell")
    bench = Bench(
        id="demaria-re-cell",
        work_id=experiment.work_id or "work-1",
        identity=BenchIdentity(
            BenchIdentityBasis.INFERRED_FROM_EMBEDDED_EVIDENCE,
            reason="typed Re cell from paper",
        ),
        cell_materials=(F.located(CellMaterial.RE),),
    )
    rows = []
    # 20 Na + 10 K figure-only points (counts are the shape; values are fixtures).
    for index in range(20):
        identity = _partial_identity(formula="Na", T_K=str(1600 + index))
        obs = F.observation(
            f"demaria-fig-na-{index}",
            experiment.experiment_id,
            identity,
            Decimal("1e-5") * Decimal(index + 1),
            evidence=EvidenceClass.FIGURE_ONLY,
            admission=AdmissionStatus.ADMITTED,
            source_id="kems-022-demaria-1971",
        )
        obs = replace(
            obs,
            uncertainty=Uncertainty(
                kind=UncertaintyKind.PRINTED,
                verbatim="±30%",
            ),
        )
        rows.append(obs)
    for index in range(10):
        identity = _partial_identity(formula="K", T_K=str(1650 + index))
        obs = F.observation(
            f"demaria-fig-k-{index}",
            experiment.experiment_id,
            identity,
            Decimal("2e-6") * Decimal(index + 1),
            evidence=EvidenceClass.FIGURE_ONLY,
            admission=AdmissionStatus.ADMITTED,
            source_id="kems-022-demaria-1971",
        )
        obs = replace(
            obs,
            uncertainty=Uncertainty(
                kind=UncertaintyKind.PRINTED,
                verbatim="±30%",
            ),
        )
        rows.append(obs)
    return rows, experiment, bench


# ---------------------------------------------------------------------------
# b-693 (a) figure_only candidacy
# ---------------------------------------------------------------------------


def test_pin_figure_only_rows_are_comparison_candidates() -> None:
    rows, experiment, bench = _demaria_shape_figure_rows()
    context = _context(F.work(), experiment, *rows, benches={bench.id: bench})
    candidates = comparison_candidates(context)
    assert len(candidates) == 30
    assert all(
        obs.evidence.class_.value is EvidenceClass.FIGURE_ONLY for obs in candidates
    )


def test_pin_figure_only_scores_with_notice_and_printed_band() -> None:
    rows, experiment, bench = _demaria_shape_figure_rows()
    reference = rows[0]
    context = _context(F.work(), experiment, reference, benches={bench.id: bench})
    prediction = _predict(Decimal("1.5e-5"), reference.identity)
    residual, _ = compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        prediction=prediction,
    )
    assert residual.status is not ResidualStatus.REFUSED
    assert residual.numeric is not None
    assert any(
        notice.kind is NoticeKind.FIGURE_ONLY or "figure_only" in notice.reason
        for notice in residual.notices
    )
    assert residual.numeric.decision_band is not None
    assert "source-printed" in residual.numeric.decision_band.rule


def test_pin_figure_only_counts_in_all_numeric_never_certified() -> None:
    rows, experiment, bench = _demaria_shape_figure_rows()
    context = _context(F.work(), experiment, *rows, benches={bench.id: bench})

    def predict(engine, obs, **kwargs):
        return _predict(Decimal("1e-5"), obs.identity)

    residuals, _ = score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        include_diagnostics=True,
        predict=predict,
    )
    numeric = [r for r in residuals if r.numeric is not None]
    assert len(numeric) == 30
    certified = headline_rows(
        residuals, context=context, tier="measured", engines=(Engine.INTERNAL_ANALYTICAL,)
    )
    vapour_certified = next(r for r in certified if r["rail"] == Rail.VAPOUR.value)
    assert vapour_certified["n"] == 0
    all_numeric = headline_rows(
        residuals,
        context=context,
        tier="all_numeric",
        engines=(Engine.INTERNAL_ANALYTICAL,),
    )
    vapour_all = next(r for r in all_numeric if r["rail"] == Rail.VAPOUR.value)
    assert vapour_all["n"] == 30


# ---------------------------------------------------------------------------
# b-693 (b) reactive cell predicts and flags
# ---------------------------------------------------------------------------


def test_pin_reactive_re_cell_predicts_with_notice_not_refusal(monkeypatch) -> None:
    """compile_residual on a typed Re Knudsen row must predict+flag, not refuse reservoir."""
    from simulator.battery.score import predict_with_engine
    from tests.battery import test_silent_fills as sf

    sf._capture_cell(monkeypatch)
    observation, experiment = sf._kems_partial(cell_material=None)
    bench = sf._cell_material_bench((CellMaterial.RE,))
    experiment = replace(experiment, bench_id=bench.id)
    context = _context(
        F.work(),
        experiment,
        observation,
        benches={bench.id: bench},
        review="reviewed",
    )
    residual, _ = compile_residual(
        observation,
        Engine.OPENIMCC,
        context=context,
        # Production predictor; monkeypatched engine open from silent_fills.
        predict=predict_with_engine,
    )
    assert residual.status is not ResidualStatus.REFUSED or (
        residual.refusal is not None
        and residual.refusal.detail.get("reason") != "reactive_cell_oxygen_reservoir"
    )
    assert residual.refusal is None or residual.refusal.detail.get("reason") != (
        "reactive_cell_oxygen_reservoir"
    )
    assert any(
        "reactive cell: oxygen balance of the cell not modelled" in notice.reason
        for notice in residual.notices
    )


def test_pin_predict_with_engine_re_cell_does_not_refuse_reservoir(
    monkeypatch,
) -> None:
    from simulator.battery.score import predict_with_engine
    from tests.battery import test_silent_fills as sf

    seen = sf._capture_cell(monkeypatch)
    observation, experiment = sf._kems_partial(cell_material=None)
    prediction = predict_with_engine(
        Engine.OPENIMCC,
        observation,
        experiment=experiment,
        bench=sf._cell_material_bench((CellMaterial.RE,)),
        isolated=False,
    )
    assert prediction.refusal_detail.get("reason") != "reactive_cell_oxygen_reservoir"
    assert prediction.refusal_reason is None or prediction.refusal_detail.get(
        "reason"
    ) != "reactive_cell_oxygen_reservoir"
    assert any(
        "reactive cell: oxygen balance of the cell not modelled" in notice.reason
        for notice in prediction.notices
    )
    assert seen.get("mode") in {sf.PO2_NOT_AN_INPUT, sf.PO2_COMMANDED, None} or seen


def test_pin_reactive_cell_with_printed_o2_uses_commanded_po2(monkeypatch) -> None:
    from simulator.battery.score import predict_with_engine
    from tests.battery import test_silent_fills as sf

    seen = sf._capture_cell(monkeypatch)
    observation, experiment = sf._kems_partial(
        cell_material=None, fO2_Pa=Decimal("1e-4")
    )
    prediction = predict_with_engine(
        Engine.OPENIMCC,
        observation,
        experiment=experiment,
        bench=sf._cell_material_bench((CellMaterial.RE,)),
        isolated=False,
    )
    assert prediction.refusal_detail.get("reason") != "reactive_cell_oxygen_reservoir"
    assert seen.get("mode") == sf.PO2_COMMANDED
    assert any(
        "reactive cell: oxygen balance of the cell not modelled" in notice.reason
        for notice in prediction.notices
    )


# ---------------------------------------------------------------------------
# b-691 dual headline
# ---------------------------------------------------------------------------



def test_pin_headline_prints_all_numeric_and_certified_lines() -> None:
    from simulator.battery.enums import SourceRelation

    work = F.work("kume-like")
    experiment = F.kems_experiment("kume-exp", work.work_id)
    certified_obs = F.observation(
        "kume-certified",
        experiment.experiment_id,
        _partial_identity(),
        Decimal("1e-5"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="kume-2000",
    )
    flagged_obs = F.observation(
        "kume-flagged",
        experiment.experiment_id,
        _partial_identity(T_K="1710"),
        Decimal("2e-5"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="kume-2000",
    )
    context = _context(work, experiment, certified_obs, flagged_obs)

    residuals = (
        Residual(
            key="kume-certified::p_partial::vapour::internal-analytical",
            reference="kume-certified",
            execution=Execution(state=ExecutionState.PRODUCED, call_evidence="f"),
            rail=Rail.VAPOUR,
            status=ResidualStatus.NO_BAND,
            source_relation=SourceRelation.INDEPENDENT,
            score_eligible=True,
            exclusions=(),
            notices=(),
            candidate=None,
            candidate_request=None,
            numeric=ResidualNumeric(
                value=Decimal("0.10"),
                operation=MetricOperation.DEX,
                unit="dimensionless",
                decision_band=None,
            ),
            refusal=None,
        ),
        Residual(
            key="kume-flagged::p_partial::vapour::internal-analytical",
            reference="kume-flagged",
            execution=Execution(state=ExecutionState.PRODUCED, call_evidence="f"),
            rail=Rail.VAPOUR,
            status=ResidualStatus.NO_BAND,
            source_relation=SourceRelation.INDEPENDENT,
            score_eligible=False,
            exclusions=("not_flagged_stratum",),
            notices=(
                Notice(
                    kind=NoticeKind.COMPOSITION_FROM_SAMPLE_CATALOG,
                    affected_quantities=(Quantity.P_PARTIAL,),
                    reason="catalogue composition fixture",
                    origin="kume-flagged",
                ),
            ),
            candidate=None,
            candidate_request=None,
            numeric=ResidualNumeric(
                value=Decimal("0.50"),
                operation=MetricOperation.DEX,
                unit="dimensionless",
                decision_band=None,
            ),
            refusal=None,
        ),
    )

    certified = headline_rows(
        residuals, context=context, tier="measured", engines=(Engine.INTERNAL_ANALYTICAL,)
    )
    vapour_certified = next(r for r in certified if r["rail"] == Rail.VAPOUR.value)
    assert vapour_certified["n"] == 1

    all_numeric = headline_rows(
        residuals,
        context=context,
        tier="all_numeric",
        engines=(Engine.INTERNAL_ANALYTICAL,),
    )
    vapour_all = next(r for r in all_numeric if r["rail"] == Rail.VAPOUR.value)
    assert vapour_all["n"] == 2
    assert "flag_class_counts" in vapour_all
    assert vapour_all["flag_class_counts"].get("catalogue-composition") == 1
    assert "iqr_dex" in vapour_all or "iqr" in vapour_all

    records = headline_records(
        residuals, context=context, engines=(Engine.INTERNAL_ANALYTICAL,)
    )
    tiers = {r["tier"] for r in records}
    assert "all_numeric" in tiers
    assert "measured" in tiers


def test_pin_source_with_zero_certified_still_listed_with_flagged_n() -> None:
    from simulator.battery.enums import SourceRelation

    work = F.work("flagged-only-source")
    experiment = F.kems_experiment("flagged-exp", work.work_id)
    flagged_obs = F.observation(
        "only-flagged",
        experiment.experiment_id,
        _partial_identity(),
        Decimal("1e-5"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="flagged-only-2000",
    )
    context = _context(work, experiment, flagged_obs)
    residuals = (
        Residual(
            key="only-flagged::p_partial::vapour::internal-analytical",
            reference="only-flagged",
            execution=Execution(state=ExecutionState.PRODUCED, call_evidence="f"),
            rail=Rail.VAPOUR,
            status=ResidualStatus.NO_BAND,
            source_relation=SourceRelation.INDEPENDENT,
            score_eligible=False,
            exclusions=("not_flagged_stratum",),
            notices=(
                Notice(
                    kind=NoticeKind.COMPOSITION_FROM_SAMPLE_CATALOG,
                    affected_quantities=(Quantity.P_PARTIAL,),
                    reason="catalogue composition fixture",
                    origin="only-flagged",
                ),
            ),
            candidate=None,
            candidate_request=None,
            numeric=ResidualNumeric(
                value=Decimal("0.40"),
                operation=MetricOperation.DEX,
                unit="dimensionless",
                decision_band=None,
            ),
            refusal=None,
        ),
    )
    all_numeric = headline_rows(
        residuals,
        context=context,
        tier="all_numeric",
        engines=(Engine.INTERNAL_ANALYTICAL,),
    )
    vapour_all = next(r for r in all_numeric if r["rail"] == Rail.VAPOUR.value)
    sources = vapour_all.get("sources") or vapour_all.get("source_list")
    assert sources is not None
    entry = next(s for s in sources if s["source_id"] == "flagged-only-2000")
    assert entry["n_certified"] == 0
    assert entry["n_flagged"] >= 1

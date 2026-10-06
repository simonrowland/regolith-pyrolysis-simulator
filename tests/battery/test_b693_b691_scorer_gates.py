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
    _ResidualRowsPayloadView,
    _ScorePayloadAccumulator,
    _render_score_report_from_payloads_legacy,
    comparison_candidates,
    compile_residual,
    headline_payload_records,
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
    """Fixture shaped like the landed De Maria 1971 extract.

    Real store shape (green tip): 117 observations; 30 alkali figure points —
    20 Na across samples 12022/12065 (BC/NAA instrument series) and 10 K;
    rhenium cell; mapped dex reading uncertainty; catalogue composition.
    """

    work = F.work("kems-022-demaria-1971")
    experiment_na = replace(
        F.kems_experiment("demaria-1971-na-figure1", work.work_id),
        bench_id="demaria-1971-re-kems",
    )
    experiment_k = replace(
        F.kems_experiment("demaria-1971-k-figure2", work.work_id),
        bench_id="demaria-1971-re-kems",
    )
    bench = Bench(
        id="demaria-1971-re-kems",
        work_id=work.work_id,
        identity=BenchIdentity(
            BenchIdentityBasis.INFERRED_FROM_EMBEDDED_EVIDENCE,
            reason="typed Re cell from De Maria 1971",
        ),
        cell_materials=(F.located(CellMaterial.RE),),
    )
    # Catalogue composition as on the landed extract (sample-catalog proxy).
    composition_12022 = Composition(
        basis="sample_catalog_proxy",
        components=(
            ("SiO2", Decimal("0.423")),
            ("Al2O3", Decimal("0.082")),
            ("FeO", Decimal("0.216")),
            ("MgO", Decimal("0.119")),
            ("CaO", Decimal("0.090")),
            ("Na2O", Decimal("0.036")),
            ("K2O", Decimal("0.0068")),
            ("TiO2", Decimal("0.049")),
            ("MnO", Decimal("0.0031")),
        ),
        amount_basis=AmountBasis.MOLE_FRACTION,
        proxy_flag="composition_from_sample_catalog",
        proxy_source="DeMaria 1971 / Smales 1971 sample 12022",
        analysis_selection_rule="printed alkali fields plus same-sample catalog majors",
    )
    composition_12065 = replace(
        composition_12022,
        components=(
            ("SiO2", Decimal("0.423")),
            ("Al2O3", Decimal("0.082")),
            ("FeO", Decimal("0.216")),
            ("MgO", Decimal("0.119")),
            ("CaO", Decimal("0.090")),
            ("Na2O", Decimal("0.027")),
            ("K2O", Decimal("0.006")),
            ("TiO2", Decimal("0.049")),
            ("MnO", Decimal("0.0031")),
        ),
        proxy_source="DeMaria 1971 / Wolf 2022 sample 12065",
    )
    mapped_unc = Uncertainty(
        kind=UncertaintyKind.PRINTED,
        verbatim={
            "dex": "0.3",
            "sigma_log10P_dex_per_point": "0.3",
            "figure_reading_estimate": {
                "log10_p_atm_absolute_uncertainty_dex": "0.3",
            },
        },
    )

    def _figure_obs(
        obs_id: str,
        experiment_id: str,
        *,
        formula: str,
        T_K: str,
        value: Decimal,
        composition: Composition,
        instrument: str,
        sample: str,
    ):
        identity = replace(
            _partial_identity(formula=formula, T_K=T_K),
            composition=State.of(composition),
        )
        obs = F.observation(
            obs_id,
            experiment_id,
            identity,
            value,
            evidence=EvidenceClass.FIGURE_ONLY,
            admission=AdmissionStatus.ADMITTED,
            source_id="kems-022-demaria-1971",
        )
        return replace(
            obs,
            uncertainty=mapped_unc,
            notices=(
                Notice(
                    kind=NoticeKind.COMPOSITION_FROM_SAMPLE_CATALOG,
                    affected_quantities=(Quantity.P_PARTIAL,),
                    reason=(
                        "composition_from_sample_catalog: "
                        f"source={composition.proxy_source}; "
                        f"analysis_selection_rule={composition.analysis_selection_rule}; "
                        f"sample={sample}; instrument={instrument}"
                    ),
                    origin=obs_id,
                    source=composition.proxy_source,
                ),
            ),
        )

    rows = []
    # 20 Na: sample 12022 (6: BC/NAA series) + sample 12065 (14: BC/NAA series).
    na_12022 = [
        ("BC", "1600"), ("BC", "1610"), ("BC", "1620"),
        ("NAA", "1630"), ("NAA", "1640"), ("NAA", "1650"),
    ]
    for index, (instrument, T_K) in enumerate(na_12022):
        rows.append(
            _figure_obs(
                f"demaria-fig-na-12022-{instrument.lower()}-{index}",
                experiment_na.experiment_id,
                formula="Na",
                T_K=T_K,
                value=Decimal("1e-5") * Decimal(index + 1),
                composition=composition_12022,
                instrument=instrument,
                sample="12022",
            )
        )
    na_12065 = [
        ("BC", "1660"), ("BC", "1670"), ("BC", "1680"), ("BC", "1690"),
        ("BC", "1700"), ("NAA", "1710"), ("NAA", "1720"), ("NAA", "1730"),
        ("NAA", "1740"), ("NAA", "1750"), ("NAA", "1760"), ("NAA", "1770"),
        ("NAA", "1780"), ("NAA", "1790"),
    ]
    for index, (instrument, T_K) in enumerate(na_12065):
        rows.append(
            _figure_obs(
                f"demaria-fig-na-12065-{instrument.lower()}-{index}",
                experiment_na.experiment_id,
                formula="Na",
                T_K=T_K,
                value=Decimal("1.5e-5") * Decimal(index + 1),
                composition=composition_12065,
                instrument=instrument,
                sample="12065",
            )
        )
    # 10 K figure points (Fig. 2).
    for index in range(10):
        rows.append(
            _figure_obs(
                f"demaria-fig-k-{index}",
                experiment_k.experiment_id,
                formula="K",
                T_K=str(1650 + index),
                value=Decimal("2e-6") * Decimal(index + 1),
                composition=composition_12022,
                instrument="NAA",
                sample="12022",
            )
        )
    # Return both experiments via a small namespace so callers keep working.
    class _Experiments:
        def __init__(self, na, k):
            self.na = na
            self.k = k
            # backward-compat: treat as the primary (Na) experiment
            self.experiment_id = na.experiment_id
            self.work_id = na.work_id
            self.bench_id = na.bench_id

    return rows, _Experiments(experiment_na, experiment_k), bench


def _demaria_context(rows, experiments, bench) -> ScoreContext:
    work = F.work("kems-022-demaria-1971")
    # experiments were built with the same stable work_id token
    return ScoreContext(
        works={work.work_id: work},
        experiments={
            experiments.na.experiment_id: experiments.na,
            experiments.k.experiment_id: experiments.k,
        },
        observations={o.observation_id: o for o in rows},
        benches={bench.id: bench},
        extract_review={"kems-022-demaria-1971": "reviewed", "": "reviewed"},
        hostname="test",
    )


# ---------------------------------------------------------------------------
# b-693 (a) figure_only candidacy
# ---------------------------------------------------------------------------


def test_pin_figure_only_rows_are_comparison_candidates() -> None:
    rows, experiments, bench = _demaria_shape_figure_rows()
    context = _demaria_context(rows, experiments, bench)
    candidates = comparison_candidates(context)
    assert len(candidates) == 30
    assert all(
        obs.evidence.class_.value is EvidenceClass.FIGURE_ONLY for obs in candidates
    )
    na = [o for o in candidates if o.identity.species.formula == "Na"]
    k = [o for o in candidates if o.identity.species.formula == "K"]
    assert len(na) == 20
    assert len(k) == 10
    samples = {
        "12022" if "12022" in o.observation_id else "12065"
        for o in na
    }
    assert samples == {"12022", "12065"}
    assert all(
        o.uncertainty.kind is UncertaintyKind.PRINTED
        and isinstance(o.uncertainty.verbatim, dict)
        and "dex" in o.uncertainty.verbatim
        for o in candidates
    )
    assert all(
        o.identity.composition.is_value
        and o.identity.composition.value.proxy_flag == "composition_from_sample_catalog"
        for o in candidates
    )
    assert bench.cell_materials[0].state.value is CellMaterial.RE


def test_pin_figure_only_scores_with_notice_and_printed_band() -> None:
    rows, experiments, bench = _demaria_shape_figure_rows()
    reference = rows[0]
    context = _demaria_context([reference], experiments, bench)
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
    # Mapped dex=0.3 reading band must be taken from this row, not erased by
    # the catalogue-composition label.
    assert residual.numeric.decision_band is not None
    assert residual.numeric.decision_band.value == Decimal("0.3")
    assert "source-printed" in residual.numeric.decision_band.rule
    assert residual.score_eligible is False
    assert any(
        notice.kind is NoticeKind.COMPOSITION_FROM_SAMPLE_CATALOG
        for notice in residual.notices
    )


def test_pin_figure_only_counts_in_all_numeric_never_certified() -> None:
    rows, experiments, bench = _demaria_shape_figure_rows()
    context = _demaria_context(rows, experiments, bench)

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


def test_pin_reactive_re_cell_implicit_predictor_keeps_notice(monkeypatch) -> None:
    """Re cell: prediction+flag, or typed refusal naming the missing input — not old reservoir."""
    from tests.battery import test_silent_fills as sf

    sf._capture_cell(monkeypatch)
    # Default _kems_partial has no printed fO2 → expect typed missing_fO2 refusal.
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
        # Production default predictor (bench= forwarded only on this path).
    )
    assert residual.refusal is None or residual.refusal.detail.get("reason") != (
        "reactive_cell_oxygen_reservoir"
    )
    if residual.status is ResidualStatus.REFUSED:
        detail = dict(residual.refusal.detail) if residual.refusal is not None else {}
        reason = str(detail.get("reason") or detail.get("missing_input") or "")
        assert "fO2" in reason or reason == "missing_fO2", detail
    else:
        assert residual.numeric is not None
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

# ---------------------------------------------------------------------------
# Review-of-record FIX-FIRST probes (findings 1–6)
# ---------------------------------------------------------------------------


def test_pin_figure_absent_uncertainty_stays_no_band_when_other_kems_present() -> None:
    """Finding 2: figure with Uncertainty.NONE must not borrow Plante's band."""
    rows, experiments, bench = _demaria_shape_figure_rows()
    figure = replace(
        rows[0],
        uncertainty=Uncertainty(kind=UncertaintyKind.NONE),
        notices=(),  # isolate absent-band case from catalogue
        identity=replace(
            rows[0].identity,
            composition=F.activity_identity().composition,
        ),
    )
    plante = replace(
        F.observation(
            "kems-printed-pressure",
            experiments.na.experiment_id,
            _partial_identity(formula="K", T_K="1500"),
            Decimal("10"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id="kems-042-plante-1979",
        ),
        uncertainty=Uncertainty(
            kind=UncertaintyKind.PRINTED,
            verbatim={
                "temperature_quote": (
                    "At 1500 K, the estimated 20 K error yields an error "
                    "in K pressure of about 40 percent."
                )
            },
        ),
    )
    context = _demaria_context([figure, plante], experiments, bench)
    residual, _ = compile_residual(
        figure,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        prediction=_predict(Decimal("1.1e-5"), figure.identity),
    )
    assert residual.status is ResidualStatus.NO_BAND
    assert residual.numeric is not None
    assert residual.numeric.decision_band is None
    assert residual.score_eligible is False


def test_pin_figure_mapped_dex_uncertainty_is_read() -> None:
    """Finding 2: stored dex / sigma_log10P_dex_per_point mapping must be used."""
    rows, experiments, bench = _demaria_shape_figure_rows()
    figure = replace(
        rows[0],
        notices=(),
        identity=replace(
            rows[0].identity,
            composition=F.activity_identity().composition,
        ),
        uncertainty=Uncertainty(
            kind=UncertaintyKind.PRINTED,
            verbatim={
                "dex": "0.3",
                "sigma_log10P_dex_per_point": "0.3",
            },
        ),
    )
    context = _demaria_context([figure], experiments, bench)
    residual, _ = compile_residual(
        figure,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        prediction=_predict(Decimal("1.1e-5"), figure.identity),
    )
    assert residual.numeric is not None
    assert residual.numeric.decision_band is not None
    assert residual.numeric.decision_band.value == Decimal("0.3")
    assert "source-printed" in residual.numeric.decision_band.rule


def test_pin_catalogue_composition_does_not_erase_figure_reading_band() -> None:
    """Finding 3: catalogue label keeps figure out of certified but keeps band."""
    rows, experiments, bench = _demaria_shape_figure_rows()
    # rows[0] already has catalogue composition + mapped dex=0.3
    reference = rows[0]
    context = _demaria_context([reference], experiments, bench)
    residual, _ = compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        prediction=_predict(Decimal("1.1e-5"), reference.identity),
    )
    assert residual.numeric is not None
    assert residual.numeric.decision_band is not None
    assert residual.numeric.decision_band.value == Decimal("0.3")
    assert residual.score_eligible is False
    assert any(
        notice.kind is NoticeKind.COMPOSITION_FROM_SAMPLE_CATALOG
        for notice in residual.notices
    )
    assert any(notice.kind is NoticeKind.FIGURE_ONLY for notice in residual.notices)


def test_pin_all_numeric_non_dex_median_iqr_in_own_metric() -> None:
    """Finding 4: non-DEX rails get median/IQR in their metric on all_numeric."""
    from simulator.battery.enums import SourceRelation

    work = F.work("residue-relative")
    experiment = F.kems_experiment("residue-exp", work.work_id)
    context = _context(work, experiment)
    residuals = (
        Residual(
            key="r1::residue::internal-analytical",
            reference="r1",
            execution=Execution(state=ExecutionState.PRODUCED, call_evidence="f"),
            rail=Rail.RESIDUE_COMPOSITION,
            status=ResidualStatus.NO_BAND,
            source_relation=SourceRelation.INDEPENDENT,
            score_eligible=True,
            exclusions=(),
            notices=(),
            candidate=None,
            candidate_request=None,
            numeric=ResidualNumeric(
                value=Decimal("0.1"),
                operation=MetricOperation.RELATIVE,
                unit="dimensionless",
                decision_band=None,
            ),
            refusal=None,
        ),
        Residual(
            key="r2::residue::internal-analytical",
            reference="r2",
            execution=Execution(state=ExecutionState.PRODUCED, call_evidence="f"),
            rail=Rail.RESIDUE_COMPOSITION,
            status=ResidualStatus.NO_BAND,
            source_relation=SourceRelation.INDEPENDENT,
            score_eligible=True,
            exclusions=(),
            notices=(),
            candidate=None,
            candidate_request=None,
            numeric=ResidualNumeric(
                value=Decimal("0.3"),
                operation=MetricOperation.RELATIVE,
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
    row = next(r for r in all_numeric if r["rail"] == Rail.RESIDUE_COMPOSITION.value)
    assert row["n"] == 2
    # r2 finding 3: non-DEX values are never published under a *_dex key;
    # they are reported in their own (quantity, operation, unit) stratum.
    assert row["median_dex"] is None
    assert row["iqr_dex"] is None
    strata = [s for s in row["metric_strata"] if s["operation"] == "relative"]
    assert len(strata) == 1
    assert strata[0]["n"] == 2
    assert strata[0]["unit"] == "dimensionless"
    assert strata[0]["median"] == str(Decimal("0.2"))
    assert "dex" not in strata[0]["label"]
    # Measured tier must remain DEX-filtered (schema unchanged): n counts
    # numeric rows but median_dex stays None when no DEX values exist.
    measured = headline_rows(
        residuals,
        context=context,
        tier="measured",
        engines=(Engine.INTERNAL_ANALYTICAL,),
    )
    measured_row = next(
        r for r in measured if r["rail"] == Rail.RESIDUE_COMPOSITION.value
    )
    assert measured_row["n"] == 2
    assert measured_row["median_dex"] is None


def test_pin_injected_predictor_does_not_receive_bench() -> None:
    """Finding 5: caller-supplied predictors keep the handles/experiment contract."""
    rows, experiments, bench = _demaria_shape_figure_rows()
    reference = rows[0]
    context = _demaria_context([reference], experiments, bench)
    seen: dict[str, object] = {}

    def injected(engine, obs, *, handles=None, experiment=None, **kwargs):
        seen["kwargs"] = set(kwargs)
        seen["has_bench"] = "bench" in kwargs
        return _predict(Decimal("1e-5"), obs.identity)

    residual, _ = compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        predict=injected,
    )
    assert residual.numeric is not None
    assert seen["has_bench"] is False


def test_pin_figure_only_notice_attaches_when_quantity_unknown() -> None:
    """Finding 6: figure_only label survives quantity_unknown refusals."""
    rows, experiments, bench = _demaria_shape_figure_rows()
    reference = rows[0]
    # Force unknown quantity while keeping FIGURE_ONLY evidence.
    identity = replace(
        reference.identity,
        quantity=State.unknown("unsupported figure quantity"),
    )
    reference = replace(reference, identity=identity)
    context = _demaria_context([reference], experiments, bench)
    residual, _ = compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        prediction=_predict(Decimal("1e-5"), identity),
    )
    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.detail.get("reason") == "quantity_unknown"
    assert any(
        notice.kind is NoticeKind.FIGURE_ONLY for notice in residual.notices
    )


def test_pin_production_report_renders_all_numeric_headline() -> None:
    """Finding 1: production Markdown + JSON owners emit all-numeric fields."""
    rows, experiments, bench = _demaria_shape_figure_rows()
    context = _demaria_context(rows, experiments, bench)

    def predict(engine, obs, **kwargs):
        return _predict(Decimal("1e-5"), obs.identity)

    residuals, _ = score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        include_diagnostics=True,
        predict=predict,
    )
    typed = headline_records(
        residuals, context=context, engines=(Engine.INTERNAL_ANALYTICAL,)
    )
    all_numeric_typed = [r for r in typed if r["tier"] == "all_numeric"]
    vapour_typed = next(
        r for r in all_numeric_typed if r["rail"] == Rail.VAPOUR.value
    )
    assert vapour_typed["n"] == 30
    assert vapour_typed.get("iqr_dex") is not None
    assert vapour_typed.get("flag_class_counts")
    assert any(
        s["source_id"] == "kems-022-demaria-1971" for s in vapour_typed["sources"]
    )

    view = _ResidualRowsPayloadView(residuals, context)
    aggregate = _ScorePayloadAccumulator.from_rows(
        view, context=context, engines=(Engine.INTERNAL_ANALYTICAL,)
    )
    streamed = aggregate.headline_records(engines=(Engine.INTERNAL_ANALYTICAL,))
    vapour_streamed = next(
        r
        for r in streamed
        if r["tier"] == "all_numeric" and r["rail"] == Rail.VAPOUR.value
    )
    assert vapour_streamed["n"] == 30
    assert "iqr_dex" in vapour_streamed
    assert "flag_class_counts" in vapour_streamed
    assert "sources" in vapour_streamed

    payload_records = headline_payload_records(
        view,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        observations=context.observations,
        origins=context.origins,
    )
    assert any(r["tier"] == "all_numeric" for r in payload_records)

    report = _render_score_report_from_payloads_legacy(
        view,
        context=context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        store_stamp={},
        _aggregate=aggregate,
    )
    assert "## All-numeric tier" in report
    assert "## Measured tier" in report
    assert "ALL NUMERIC" in report.upper() or "All-numeric" in report
    assert "IQR" in report
    assert "kems-022-demaria-1971" in report

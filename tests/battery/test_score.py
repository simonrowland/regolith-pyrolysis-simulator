"""v2.1 scorer: score_eligible conjuncts, refusals, IMCC set, pins, determinism.

Expected values come from the schema contract, never from the code under test.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import math
import shutil
import subprocess
import sys
import warnings
from collections import Counter
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import (
    AdmissionStatus,
    AmountBasis,
    Authority,
    Engine,
    EvidenceClass,
    ExecutionState,
    MethodToken,
    MetricOperation,
    NoticeKind,
    PerBasis,
    Phase,
    Quantity,
    Rail,
    RefusalReason,
    ResidualStatus,
    SourceRelation,
    UncertaintyKind,
    ValueKind,
)
from simulator.battery.pins import (
    PinBandRecord,
    PinWidenError,
    assert_never_widen,
    pin_failures,
    tombstone_for_changed_identity,
)
from simulator.battery.identity import Exposure, SweepIdentity
from simulator.battery.records import (
    Apparatus,
    ApparatusGeometry,
    Composition,
    Derivation,
    EngineTrace,
    Execution,
    Located,
    Notice,
    ResidualNumeric,
    DecisionBand,
    Species,
    State,
    Uncertainty,
    Value,
)
from simulator.battery.validity import run_validity_gates, underdetermined_apparatus
from simulator.battery.score import (
    SCORE_ELIGIBLE_CONJUNCTS,
    SCORE_ENGINE_SET,
    EligibleConjuncts,
    EnginePrediction,
    ScoreContext,
    SINGLE_CATION_COEFFICIENT_BASIS,
    SINGLE_LIQUID_ENGINES,
    compile_residual,
    compute_metric,
    derive_kems_partial_pressure_band,
    dumps_residual_line,
    engines_from_names,
    flagged_stratum_payloads,
    headline_payloads,
    load_score_context,
    parse_species_formula,
    pooled_log_pressure_sd,
    resolve_source_relation,
    residual_to_plain,
    score_eligible_from_conjuncts,
    score_store,
)
from simulator.battery.validate import validate_corpus, validate_residual
from tests.battery import factories as F


def _green_conjuncts() -> EligibleConjuncts:
    return EligibleConjuncts(
        status=ResidualStatus.MATCH,
        finite_numeric_point_endpoints=True,
        valid_metric_domain=True,
        reference_measured_evidence=True,
        admission_admitted=True,
        extract_review_permits_use=True,
        validity_gates_pass=True,
        identity_equal=True,
        source_relation_independent_complete_ancestry=True,
        candidate_engine_prediction_authority_allowed=True,
        no_blocking_qualification=True,
        selected_independent_lineage_level=True,
    )


def test_score_eligible_conjuncts_cover_the_spec() -> None:
    assert SCORE_ELIGIBLE_CONJUNCTS == tuple(_green_conjuncts().as_mapping())
    assert score_eligible_from_conjuncts(_green_conjuncts()) is True


@pytest.mark.parametrize("conjunct", SCORE_ELIGIBLE_CONJUNCTS)
def test_score_eligible_conjunct_red_then_green(conjunct: str) -> None:
    green = _green_conjuncts()
    mapping = green.as_mapping()
    assert mapping[conjunct] is True
    # Mutate the named conjunct off. status is ResidualStatus, not a bool.
    if conjunct == "status_match_or_mismatch":
        red = replace(green, status=ResidualStatus.REFUSED)
    else:
        field = conjunct
        red = replace(green, **{field: False})
    assert score_eligible_from_conjuncts(red) is False
    assert conjunct in red.exclusions()
    assert score_eligible_from_conjuncts(green) is True


def test_imcc_present_in_engine_set_by_construction() -> None:
    assert Engine.IMCC_SF04 in SCORE_ENGINE_SET
    assert Engine.IMCC_SF04_EXT in SCORE_ENGINE_SET
    from simulator.battery import score as score_mod

    tree = ast.parse(inspect.getsource(score_mod))
    assignment = None
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "SCORE_ENGINE_SET":
            assignment = node.value
            break
        if isinstance(node, ast.Assign):
            if any(getattr(t, "id", None) == "SCORE_ENGINE_SET" for t in node.targets):
                assignment = node.value
                break
    assert assignment is not None
    src = ast.dump(assignment)
    assert "resolve_backend" not in src
    assert "Call" not in src
    with pytest.raises(ValueError, match="explicit SCORE_ENGINE_SET"):
        engines_from_names(["nasa_cea_9"])


def _context(work=None, experiment=None, *observations, review=None) -> ScoreContext:
    w = work or F.work()
    exp = experiment or F.tabulation_experiment()
    obs = {o.observation_id: o for o in observations}
    extract_review = {"": review, "janaf-4th": review}
    for item in observations:
        if item.source_id:
            extract_review[item.source_id] = review
    return ScoreContext(
        works={w.work_id: w},
        experiments={exp.experiment_id: exp},
        observations=obs,
        extract_review=extract_review,
        hostname="test",
    )


def _predict(value: Decimal, identity, **kwargs) -> EnginePrediction:
    return EnginePrediction(
        engine=Engine.INTERNAL_ANALYTICAL,
        channel="internal-analytical",
        execution=Execution(state=ExecutionState.PRODUCED, call_evidence="test:predict"),
        value=value,
        unit="kJ_per_declared_mol_basis",
        authority=kwargs.get("authority", Authority.CERTIFIED),
        notices=kwargs.get("notices", ()),
        coefficient_sources=kwargs.get("coefficient_sources", ("nasa-cea-thermo",)),
        lineage_complete=kwargs.get("lineage_complete", True),
        identity=identity,
        refusal_reason=kwargs.get("refusal_reason"),
        refusal_detail=kwargs.get("refusal_detail", {}),
    )


def _compile(reference, experiment, predict, review=None, extra_obs=()):
    ctx = _context(F.work(), experiment, reference, *extra_obs, review=review)
    return compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=ctx,
        comparison_ids={reference.observation_id, *(o.observation_id for o in extra_obs)},
        predict=lambda engine, obs, **kw: predict,
    )


def _partial_identity(*, phase_reason: str | None = None):
    identity = replace(
        F.pref_identity(),
        quantity=Quantity.P_PARTIAL,
        composition=F.activity_identity().composition,
    )
    if phase_reason is not None:
        identity = replace(
            identity,
            species=Species("Na", State.unknown(phase_reason)),
        )
    return identity


def _partial_prediction(engine, observation, **_kwargs):
    return EnginePrediction(
        engine=engine,
        channel=engine.value,
        execution=Execution(state=ExecutionState.PRODUCED, call_evidence="test:predict"),
        value=Decimal("1"),
        unit="Pa",
        authority=Authority.CERTIFIED,
        coefficient_sources=("nasa-cea-thermo",),
        lineage_complete=True,
        identity=observation.identity,
    )


def test_pooled_log_pressure_sd_known_replicates() -> None:
    assert pooled_log_pressure_sd(((-1, 0, 1), (9, 10, 11))) == Decimal("1")


def test_kems_band_derives_known_replicate_scatter() -> None:
    experiment = F.kems_experiment()
    identity = replace(
        _partial_identity(),
        total_pressure_Pa=State.of(Decimal("1e-6")),
    )
    first = F.observation(
        "kems-replicate-1",
        experiment.experiment_id,
        identity,
        Decimal("10"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    second = F.observation(
        "kems-replicate-2",
        experiment.experiment_id,
        identity,
        Decimal("100"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    band = derive_kems_partial_pressure_band(
        {first.observation_id: first, second.observation_id: second},
        {experiment.experiment_id: experiment},
    )
    assert band is not None
    assert band.unit == "dimensionless"
    assert band.value == Decimal("0.5").sqrt()
    assert "replicate scatter" in band.rule
    assert "pooled replicate" in band.rule


def test_kems_band_uses_source_printed_pressure_uncertainty() -> None:
    experiment = F.kems_experiment()
    reference = replace(
        F.observation(
            "kems-printed-pressure",
            experiment.experiment_id,
            _partial_identity(),
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
    band = derive_kems_partial_pressure_band(
        {reference.observation_id: reference},
        {experiment.experiment_id: experiment},
    )
    assert band is not None
    assert band.value == Decimal("1.4").ln() / Decimal("10").ln()
    assert "source-printed" in band.rule
    assert "log10(1.40)" in band.rule
    assert "page-280 1500 K sentence" in band.rule
    assert "upper multiplicative edge" in band.rule
    assert "[1/1.40, 1.40] (-28.6%..+40%)" in band.rule
    assert "+/-40% relative band" in band.rule
    assert "single 1500 K figure is applied across the tabulated T range" in band.rule


def test_kems_band_prefers_printed_envelope_over_replicate_scatter() -> None:
    experiment = F.kems_experiment()
    identity = _partial_identity()
    printed = replace(
        F.observation(
            "kems-printed-with-replicates",
            experiment.experiment_id,
            identity,
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
    replicate_low = F.observation(
        "kems-replicate-low",
        experiment.experiment_id,
        identity,
        Decimal("10"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    replicate_high = F.observation(
        "kems-replicate-high",
        experiment.experiment_id,
        identity,
        Decimal("100"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    band = derive_kems_partial_pressure_band(
        {
            item.observation_id: item
            for item in (printed, replicate_low, replicate_high)
        },
        {experiment.experiment_id: experiment},
    )
    assert band is not None
    assert band.value == Decimal("1.4").ln() / Decimal("10").ln()
    assert "source-printed" in band.rule
    assert "pooled replicate" not in band.rule


def test_zhang_alpha_gamma_bound_uses_implied_alpha_verdict() -> None:
    exp = F.kems_experiment()
    identity = replace(
        F.activity_identity(formula="NaO0.5"),
        quantity=Quantity.EVAPORATION_COEFFICIENT_ALPHA,
        species=Species("Na", Phase.L),
        subtype=State.of("langmuir_alpha"),
        per=State.of(PerBasis.DIMENSIONLESS),
        reference_state=State.not_applicable("alpha row does not carry activity standard state"),
        reservoir=State.of(Species("Na", Phase.G)),
        sweep_gas=State.of(
            SweepIdentity(
                species="N2",
                flow_sccm=State.of(Decimal("1")),
                partial_pressure_Pa=State.of(Decimal("1")),
            )
        ),
        exposure=State.of(
            Exposure(area_m2=State.of(Decimal("1")), duration_s=State.of(Decimal("1")))
        ),
    )
    reference = F.observation(
        "zhang-bound",
        exp.experiment_id,
        identity,
        Decimal("1e-6"),
        evidence=EvidenceClass.MEASURED_REDUCED,
        source_id="work-1",
    )
    reference = replace(
        reference,
        provenance={
            "scoring": {
                "kind": "implied_alpha_from_alpha_times_Gamma",
                "oxide_formula": "Na2O",
            }
        },
    )
    ctx = _context(F.work(), exp, reference)

    def predict(engine, observation, **_kwargs):
        return EnginePrediction(
            engine=engine,
            channel=engine.value,
            execution=Execution(state=ExecutionState.PRODUCED, call_evidence="test:gamma"),
            value=Decimal("1e-5"),
            unit="dimensionless",
            authority=Authority.CERTIFIED,
            coefficient_sources=("nasa-cea-thermo",),
            lineage_complete=True,
            identity=observation.identity,
            coefficient_basis=SINGLE_CATION_COEFFICIENT_BASIS,
        )

    residual, candidate = compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=ctx,
        predict=predict,
    )
    assert candidate is not None
    assert residual.numeric is not None
    assert residual.numeric.verdict == "consistent"
    assert residual.numeric.value == Decimal("-1")
    assert residual.status is ResidualStatus.MATCH

    def impossible_predict(engine, observation, **_kwargs):
        return replace(predict(engine, observation), value=Decimal("1e-7"))

    impossible_residual, _ = compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=ctx,
        predict=impossible_predict,
    )
    assert impossible_residual.numeric is not None
    assert impossible_residual.numeric.verdict == "physically_impossible"
    assert impossible_residual.numeric.value == Decimal("1")
    assert impossible_residual.status is ResidualStatus.MISMATCH

    def outside_band_predict(engine, observation, **_kwargs):
        return replace(predict(engine, observation), value=Decimal("1e-4"))

    outside_band_residual, _ = compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=ctx,
        predict=outside_band_predict,
    )
    assert outside_band_residual.numeric is not None
    assert outside_band_residual.numeric.verdict == "outside_literature_band"
    assert outside_band_residual.numeric.value == Decimal("-2")
    assert outside_band_residual.status is ResidualStatus.NO_BAND


@pytest.mark.parametrize("reported_basis", ["parent_oxide", None])
def test_zhang_alpha_gamma_bound_refuses_parent_oxide_coefficient(
    reported_basis: str | None,
) -> None:
    exp = F.kems_experiment()
    identity = replace(
        F.activity_identity(formula="NaO0.5"),
        quantity=Quantity.EVAPORATION_COEFFICIENT_ALPHA,
        species=Species("Na", Phase.L),
        subtype=State.of("langmuir_alpha"),
        per=State.of(PerBasis.DIMENSIONLESS),
        reference_state=State.not_applicable(
            "alpha row does not carry activity standard state"
        ),
        reservoir=State.of(Species("Na", Phase.G)),
        sweep_gas=State.of(
            SweepIdentity(
                species="N2",
                flow_sccm=State.of(Decimal("1")),
                partial_pressure_Pa=State.of(Decimal("1")),
            )
        ),
        exposure=State.of(
            Exposure(area_m2=State.of(Decimal("1")), duration_s=State.of(Decimal("1")))
        ),
    )
    reference = replace(
        F.observation(
            "zhang-parent-gamma",
            exp.experiment_id,
            identity,
            Decimal("1e-6"),
            evidence=EvidenceClass.MEASURED_REDUCED,
            source_id="work-1",
        ),
        provenance={
            "scoring": {
                "kind": "implied_alpha_from_alpha_times_Gamma",
                "oxide_formula": "Na2O",
            }
        },
    )
    ctx = _context(F.work(), exp, reference)

    def parent_predict(engine, observation, **_kwargs):
        return EnginePrediction(
            engine=engine,
            channel=engine.value,
            execution=Execution(
                state=ExecutionState.PRODUCED,
                call_evidence="test:parent-gamma",
            ),
            value=Decimal("1e-5"),
            unit="dimensionless",
            authority=Authority.CERTIFIED,
            coefficient_sources=("nasa-cea-thermo",),
            lineage_complete=True,
            identity=observation.identity,
            coefficient_basis=reported_basis,
        )

    residual, candidate = compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=ctx,
        predict=parent_predict,
    )
    assert candidate is None
    assert residual.status is ResidualStatus.REFUSED
    assert residual.numeric is None
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.COEFFICIENT_BASIS_MISMATCH


def test_derived_oxygen_condition_notice_reaches_residual() -> None:
    notice = Notice(
        kind=NoticeKind.PRESSURE_PROVENANCE_UNKNOWN,
        affected_quantities=(Quantity.P_PARTIAL,),
        reason="fO2_Pa is a DERIVED condition under congruent vaporization, not a measurement",
        origin="plante-row",
    )
    experiment = F.kems_experiment()
    reference = F.observation(
        "plante-derived-oxygen",
        experiment.experiment_id,
        _partial_identity(),
        Decimal("1"),
        source_id="plante-1979",
        notices=(notice,),
    )
    prediction = _partial_prediction(Engine.INTERNAL_ANALYTICAL, reference)
    residual, _ = _compile(reference, experiment, prediction)
    assert any(
        item.kind is NoticeKind.PRESSURE_PROVENANCE_UNKNOWN
        and "DERIVED condition" in item.reason
        for item in residual.notices
    )


def _two_phase_notice():
    return Notice(
        kind=NoticeKind.OUT_OF_CERTIFIED_BAND,
        affected_quantities=(Quantity.P_PARTIAL,),
        reason=(
            "Printed pressure retained; no equilibrium comparator claimed for bulk "
            "composition in the two-phase region."
        ),
        origin="plante1979",
        band="two_phase_bulk_composition_not_liquid_composition",
    )


@pytest.mark.parametrize("engine", sorted(SINGLE_LIQUID_ENGINES))
def test_two_phase_bulk_rows_are_refused_before_single_liquid_prediction(engine):
    exp = F.kems_experiment()
    ident = _partial_identity(
        phase_reason=(
            "phase string 'K2O-SiO2_bulk_composition_in_two_phase_region' "
            "is not in the closed automatic map"
        )
    )
    ref = F.observation(
        "plante-two-phase",
        exp.experiment_id,
        ident,
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        admission=AdmissionStatus.PENDING,
        source_id="work-1",
        notices=(_two_phase_notice(),),
    )
    calls = []
    ctx = _context(F.work(), exp, ref)

    def predict(*args, **kwargs):
        calls.append((args, kwargs))
        return _partial_prediction(*args, **kwargs)

    residual, candidate = compile_residual(
        ref,
        engine,
        context=ctx,
        comparison_ids={ref.observation_id},
        predict=predict,
    )

    assert candidate is None
    assert calls == []
    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.BULK_NOT_LIQUID_COMPOSITION
    assert residual.refusal.detail["reason"] == "bulk_not_liquid_composition"
    assert residual.execution.state is ExecutionState.NOT_PROBED
    assert ref.admission.status is AdmissionStatus.PENDING
    assert residual.notices == ref.notices


def test_two_phase_bulk_marker_refusal_preserves_homogeneous_row_count():
    exp = F.kems_experiment()
    work = F.work()
    observations = {}
    for index in range(162):
        ref = F.observation(
            f"plante-homogeneous-{index}",
            exp.experiment_id,
            _partial_identity(),
            Decimal("1"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id="work-1",
        )
        observations[ref.observation_id] = ref
    for index in range(59):
        ref = F.observation(
            f"plante-bulk-{index}",
            exp.experiment_id,
            _partial_identity(),
            Decimal("1"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            admission=AdmissionStatus.PENDING,
            source_id="work-1",
            notices=(_two_phase_notice(),),
        )
        observations[ref.observation_id] = ref
    ctx = ScoreContext(
        works={work.work_id: work},
        experiments={exp.experiment_id: exp},
        observations=observations,
        extract_review={"work-1": None},
        hostname="test",
    )

    for engine in sorted(SINGLE_LIQUID_ENGINES):
        residuals, _ = score_store(
            ctx,
            engines=(engine,),
            rail=Rail.VAPOUR,
            predict=_partial_prediction,
        )
        reasons = Counter(
            residual.refusal.reason.value
            for residual in residuals
            if residual.refusal is not None
        )
        assert len(residuals) == 221
        assert reasons["bulk_not_liquid_composition"] == 59
        assert sum(
            residual.refusal is None
            or residual.refusal.reason is not RefusalReason.BULK_NOT_LIQUID_COMPOSITION
            for residual in residuals
        ) == 162


def test_bulk_refusal_survives_validity_gate_validation():
    exp = F.kems_experiment(
        orifice_area=None,
        clausing=None,
        kn=None,
        calibrated=False,
    )
    work = F.work()
    ref = F.observation(
        "plante-two-phase-validation",
        exp.experiment_id,
        _partial_identity(),
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        admission=AdmissionStatus.PENDING,
        source_id="work-1",
        notices=(_two_phase_notice(),),
    )
    residual, _ = compile_residual(
        ref,
        Engine.INTERNAL_ANALYTICAL,
        context=_context(work, exp, ref),
        comparison_ids={ref.observation_id},
        predict=_partial_prediction,
    )

    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.BULK_NOT_LIQUID_COMPOSITION
    assert validate_residual(
        residual,
        {ref.observation_id: ref},
        {exp.experiment_id: exp},
        {work.work_id: work},
    ) == []


def test_green_thermo_residual_is_score_eligible() -> None:
    exp = F.tabulation_experiment()
    ident = F.o2_identity()
    ref = F.observation(
        "o2-ref",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="work-1",
    )
    residual, candidate = _compile(ref, exp, _predict(Decimal("0"), ident))
    assert residual.status is ResidualStatus.MATCH
    assert residual.score_eligible is True
    assert residual.numeric is not None
    assert residual.numeric.value == Decimal("0")
    assert candidate is not None
    report = validate_corpus(
        [F.work()],
        [exp],
        [ref, candidate],
        [residual],
    )
    assert report.ok, [i.detail for i in report.issues]


def test_refused_never_numeric() -> None:
    exp = F.tabulation_experiment()
    ident = F.o2_identity()
    ref = F.observation(
        "o2-unknown-value",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    from simulator.battery.enums import ValueKind
    from simulator.battery.records import Value

    ref = replace(ref, value=Value(kind=ValueKind.UNAVAILABLE, unavailable_reason="not printed"))
    residual, _ = _compile(ref, exp, _predict(Decimal("0"), ident))
    assert residual.status is ResidualStatus.REFUSED
    assert residual.numeric is None
    assert residual.score_eligible is False
    assert residual.refusal is not None


def test_zero_never_priced_as_value() -> None:
    """Absence/unavailable is a refusal, not a numeric zero."""

    exp = F.tabulation_experiment()
    ident = F.o2_identity()
    ref = F.observation(
        "o2-absent",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    from simulator.battery.enums import ValueKind
    from simulator.battery.records import Value

    ref = replace(ref, value=Value(kind=ValueKind.UNAVAILABLE, unavailable_reason="unknown quantity"))
    residual, _ = _compile(ref, exp, _predict(Decimal("0"), ident))
    assert residual.numeric is None
    assert residual.refusal is not None
    assert residual.refusal.detail.get("reason") in {
        "unknown quantity",
        "value_unavailable",
        "value_unknown",
    }


def test_species_formula_unparsed_never_string_matched() -> None:
    exp = F.tabulation_experiment()
    ident = F.o2_identity()
    ident = replace(ident, species=replace(ident.species, formula="unknown"))
    ref = F.observation(
        "bad-formula",
        exp.experiment_id,
        ident,
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    residual, _ = _compile(ref, exp, _predict(Decimal("1"), ident))
    assert residual.status is ResidualStatus.REFUSED
    assert residual.numeric is None
    assert residual.refusal.detail.get("reason") == "species_formula_unparsed"
    assert parse_species_formula("unknown") is None
    assert parse_species_formula("kems-007") is None
    assert parse_species_formula("O2") == (("O", 2.0),)


def test_compile_mutates_each_conjunct_off() -> None:
    exp = F.tabulation_experiment()
    ident = F.o2_identity()
    ref = F.observation(
        "o2-ref",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="work-1",
    )
    residual, _ = _compile(ref, exp, _predict(Decimal("0"), ident))
    assert residual.score_eligible is True

    pending = replace(ref, admission=replace(ref.admission, status=AdmissionStatus.PENDING))
    residual, _ = _compile(pending, exp, _predict(Decimal("0"), ident))
    assert residual.score_eligible is False
    assert "admission_admitted" in residual.exclusions

    compiled = F.observation(
        "o2-comp",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
    )
    residual, _ = _compile(compiled, exp, _predict(Decimal("0"), ident))
    assert residual.score_eligible is False

    residual, _ = _compile(
        ref,
        exp,
        _predict(
            Decimal("0"),
            ident,
            coefficient_sources=("unregistered-coefficient-source",),
            lineage_complete=False,
        ),
    )
    assert residual.score_eligible is False

    residual, _ = _compile(
        ref, exp, _predict(Decimal("0"), ident, authority=Authority.REFUSED)
    )
    assert residual.score_eligible is False

    other_T = replace(ident, temperature_K=replace(ident.temperature_K, value=Decimal("400")))
    residual, _ = _compile(ref, exp, _predict(Decimal("0"), other_T))
    assert residual.score_eligible is False
    assert residual.status is ResidualStatus.REFUSED
    assert residual.numeric is None

    residual, _ = _compile(ref, exp, _predict(Decimal("0"), ident), review="rejected")
    assert residual.score_eligible is False
    assert "extract_review_permits_use" in residual.exclusions

    kems = F.kems_experiment(orifice_area=None, clausing=None, kn=None, calibrated=False)
    ident_p = F.psat_identity("Na")
    ref_p = F.observation(
        "na-psat",
        kems.experiment_id,
        ident_p,
        Decimal("0.1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    residual, _ = _compile(
        ref_p,
        kems,
        _predict(Decimal("0.1"), ident_p),
    )
    assert residual.score_eligible is False
    assert residual.status is ResidualStatus.REFUSED
    assert residual.numeric is None
    assert "validity_gates_pass" in residual.exclusions

    parent = F.observation(
        "raw-parent",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="work-1",
    )
    child = F.observation(
        "derived-child",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_REDUCED,
        derived_from=("raw-parent",),
        source_id="work-1",
    )
    residual, _ = _compile(
        child,
        exp,
        _predict(Decimal("0"), ident),
        extra_obs=(parent,),
    )
    assert residual.score_eligible is False
    assert "selected_independent_lineage_level" in residual.exclusions

    ident_p = F.psat_identity("Na")
    ref_p = F.observation(
        "na-floor",
        exp.experiment_id,
        ident_p,
        Decimal("0.1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    floor = Notice(
        kind=NoticeKind.FLOOR_INVERSION,
        affected_quantities=(Quantity.P_SAT,),
        reason="floor",
        origin="catalog:Na",
        original=Decimal("1e-30"),
    )
    residual, _ = _compile(
        ref_p,
        exp,
        _predict(Decimal("0.1"), ident_p, notices=(floor,)),
    )
    assert residual.score_eligible is False
    assert residual.numeric is None or "no_blocking_qualification" in residual.exclusions or residual.status is ResidualStatus.REFUSED


def test_qualification_notice_carried() -> None:
    exp = F.tabulation_experiment()
    ident = F.activity_identity()
    ref = F.observation(
        "act-ref",
        exp.experiment_id,
        ident,
        Decimal("0.5"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    notice = Notice(
        kind=NoticeKind.OUT_OF_CERTIFIED_BAND,
        affected_quantities=(Quantity.ACTIVITY,),
        reason="MELTS qualification outside commissioning band",
        origin="engine:alphamelts",
        band="SiO2 40-80 wt%",
    )
    residual, candidate = _compile(
        ref,
        exp,
        _predict(
            Decimal("0.5"),
            ident,
            notices=(notice,),
            authority=Authority.EXTRAPOLATED,
        ),
    )
    kinds = {n.kind for n in residual.notices}
    assert NoticeKind.OUT_OF_CERTIFIED_BAND in kinds
    assert candidate is not None
    assert candidate.authority is Authority.EXTRAPOLATED


def test_metric_domain_refuses_zero_and_negative_dex() -> None:
    # Premise: dex = log10(C/R) requires C>0 and R>0. Zero is not a priceable dex.
    assert compute_metric(MetricOperation.DEX, Decimal("0"), Decimal("1")) is None
    assert compute_metric(MetricOperation.RELATIVE, Decimal("1"), Decimal("0")) is None
    value = compute_metric(MetricOperation.DEX, Decimal("10"), Decimal("1"))
    assert value == Decimal("1")
    abs_v = compute_metric(MetricOperation.ABSOLUTE, Decimal("2"), Decimal("5"))
    assert abs_v == Decimal("-3")


def test_determinism_two_dumps_byte_identical() -> None:
    exp = F.tabulation_experiment()
    ident = F.o2_identity()
    ref = F.observation(
        "o2-ref",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    residual, candidate = _compile(
        F.observation(
            "o2-ref",
            exp.experiment_id,
            ident,
            Decimal("0"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id="work-1",
        ),
        exp,
        _predict(Decimal("0"), ident),
    )
    a = dumps_residual_line(residual, candidate)
    b = dumps_residual_line(residual, candidate)
    assert a == b


def test_pins_never_widen() -> None:
    baseline = [
        PinBandRecord(
            key="a",
            expected_outcome="match",
            evidence="test",
            centre=Decimal("0"),
            metric_operation="absolute",
            metric_unit="kJ_per_declared_mol_basis",
            pin_band_value=Decimal("0.05"),
            pin_band_unit="kJ_per_declared_mol_basis",
        )
    ]
    wider = [
        replace(baseline[0], pin_band_value=Decimal("0.10")),
    ]
    with pytest.raises(PinWidenError, match="widened"):
        assert_never_widen(wider, baseline)
    narrower = [replace(baseline[0], pin_band_value=Decimal("0.02"))]
    assert_never_widen(narrower, baseline)


def test_changed_identity_preserves_tombstone() -> None:
    old = PinBandRecord(
        key="old-key",
        expected_outcome="mismatch",
        evidence="test",
        centre=Decimal("0.4"),
        metric_operation="absolute",
        metric_unit="kJ_per_declared_mol_basis",
        pin_band_value=Decimal("0.05"),
        pin_band_unit="kJ_per_declared_mol_basis",
    )
    tomb = tombstone_for_changed_identity(old, new_key="new-key")
    assert tomb.tombstone is True
    assert tomb.key == "old-key"
    assert tomb.centre == Decimal("0.4")
    assert "old-key" in tomb.aliases


def _unknown_method_experiment():
    return replace(
        F.tabulation_experiment(),
        method=State.unknown("source does not state method"),
    )


def test_thermo_unknown_method_is_not_underdetermined_apparatus() -> None:
    """JANAF melting-point / ΔfG class: schema does not require orifice geometry."""

    exp = _unknown_method_experiment()
    for quantity in (
        Quantity.DELTA_FG,
        Quantity.TRANSITION_TEMPERATURE,
        Quantity.CP,
        Quantity.S,
        Quantity.H_MINUS_H298,
    ):
        gate = underdetermined_apparatus(exp, quantity)
        assert gate.passed is True, (quantity, gate.reason, gate.primary_check)
        assert gate.reason is None

    ident = F.o2_identity()
    ref = F.observation(
        "janaf-4th::Cr_melting_point",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="work-1",
    )
    outcome = run_validity_gates(exp, ref)
    assert outcome.passed is True
    residual, _ = _compile(ref, exp, _predict(Decimal("0"), ident))
    assert residual.refusal is None or residual.refusal.reason is not (
        RefusalReason.UNDERDETERMINED_APPARATUS
    )
    assert residual.score_eligible is True


def test_unknown_method_on_vapour_is_typed_method_unknown() -> None:
    exp = _unknown_method_experiment()
    gate = underdetermined_apparatus(exp, Quantity.P_SAT)
    assert gate.passed is False
    assert gate.reason is RefusalReason.METHOD_UNKNOWN
    assert gate.primary_check == "method"
    assert gate.reason is not RefusalReason.UNDERDETERMINED_APPARATUS

    ident = F.psat_identity("Na")
    ref = F.observation(
        "yakovlev-psat",
        exp.experiment_id,
        ident,
        Decimal("0.1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="work-1",
    )
    residual, _ = _compile(ref, exp, _predict(Decimal("0.1"), ident))
    assert residual.status is ResidualStatus.REFUSED
    assert residual.numeric is None
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.METHOD_UNKNOWN
    assert residual.refusal.reason is not RefusalReason.UNDERDETERMINED_APPARATUS


def test_richter_langmuir_alpha_still_fails_exposed_area() -> None:
    geometry = ApparatusGeometry()
    exp = replace(
        F.tabulation_experiment(),
        method=State.of(MethodToken.LANGMUIR_FREE_EVAPORATION),
        apparatus=Apparatus(geometry=geometry),
    )
    gate = underdetermined_apparatus(exp, Quantity.EVAPORATION_COEFFICIENT_ALPHA)
    assert gate.passed is False
    assert gate.reason is RefusalReason.UNDERDETERMINED_APPARATUS
    assert gate.primary_check == "geometry_determinants"
    missing = next(
        c.detail["missing"] for c in gate.checks if c.name == "geometry_determinants"
    )
    assert "exposed_area_m2" in missing

    with_area = replace(
        exp,
        apparatus=Apparatus(
            geometry=ApparatusGeometry(exposed_area_m2=Located(State.of(Decimal("1e-4"))))
        ),
    )
    assert underdetermined_apparatus(
        with_area, Quantity.EVAPORATION_COEFFICIENT_ALPHA
    ).passed


@pytest.mark.parametrize("quantity", (Quantity.P_PARTIAL, Quantity.P_SAT))
def test_calibrated_kems_pressure_needs_no_effusion_geometry(quantity: Quantity) -> None:
    calibrated = F.kems_experiment(
        orifice_area=None, clausing=None, kn=None, calibrated=True
    )
    assert underdetermined_apparatus(calibrated, quantity).passed

    incomplete = F.kems_experiment(
        orifice_area=None, clausing=None, kn=None, calibrated=False
    )
    gate = underdetermined_apparatus(incomplete, quantity)
    assert gate.passed is False
    assert gate.reason is RefusalReason.UNDERDETERMINED_APPARATUS
    assert gate.primary_check == "kems_calibration"
    check = next(c for c in gate.checks if c.name == "kems_calibration")
    assert check.detail["missing"] == ["calibration"]
    assert "calibration" in check.detail["reason"]

    complete = F.kems_experiment()
    assert underdetermined_apparatus(complete, Quantity.P_PARTIAL).passed
    ident = F.psat_identity("Na")
    ident = replace(ident, quantity=Quantity.P_PARTIAL)
    ref = F.observation(
        "kems-pi",
        complete.experiment_id,
        ident,
        Decimal("0.8"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="work-1",
    )
    outcome = run_validity_gates(complete, ref)
    assert outcome.passed is True


def test_comparison_activity_cancels_cell_geometry_only_for_activity() -> None:
    provenance = {
        "comparison_method": {"kind": "ratio"},
        "common_knudsen_cell_constant": {"cancels": True},
        "melt_reference_pairing": {"kind": "same_effective_setup"},
    }
    experiment = F.kems_experiment(
        orifice_area=None, clausing=None, kn=None, calibrated=True
    )
    observation = replace(
        F.observation(
            "tsaplin-activity",
            experiment.experiment_id,
            F.activity_identity(),
            Decimal("0.2"),
            evidence=EvidenceClass.MEASURED_DIRECT,
        ),
        provenance=provenance,
    )
    gate = underdetermined_apparatus(
        experiment, Quantity.ACTIVITY, observation=observation
    )
    assert gate.passed is True
    assert any(
        check.name == "comparison_method_cell_constant_cancels" and check.passed
        for check in gate.checks
    )
    full_gate = run_validity_gates(experiment, observation)
    assert full_gate.reason is RefusalReason.EFFUSION_REGIME_UNVERIFIED

    without_provenance = replace(observation, provenance=None)
    missing = underdetermined_apparatus(
        experiment, Quantity.ACTIVITY, observation=without_provenance
    )
    assert missing.reason is RefusalReason.UNDERDETERMINED_APPARATUS
    assert missing.primary_check == "geometry_determinants"

    uncalibrated = F.kems_experiment(
        orifice_area=None, clausing=None, kn=None, calibrated=False
    )
    partial_identity = replace(F.psat_identity("Na"), quantity=Quantity.P_PARTIAL)
    partial = replace(
        F.observation(
            "tsaplin-partial-pressure",
            uncalibrated.experiment_id,
            partial_identity,
            Decimal("0.2"),
            evidence=EvidenceClass.MEASURED_DIRECT,
        ),
        provenance=provenance,
    )
    partial_gate = underdetermined_apparatus(
        uncalibrated, Quantity.P_PARTIAL, observation=partial
    )
    assert partial_gate.reason is RefusalReason.UNDERDETERMINED_APPARATUS
    assert partial_gate.primary_check == "kems_calibration"


def test_unverified_kems_value_is_numeric_flagged_but_missing_value_refuses() -> None:
    experiment = F.kems_experiment(calibrated=True, kn=None)
    experiment = replace(
        experiment,
        pressure_environment=replace(
            experiment.pressure_environment,
            total_pressure_Pa=Located(State.unknown("not printed")),
        ),
    )
    identity = replace(
        _partial_identity(),
        total_pressure_Pa=State.of(Decimal("1e-6")),
    )
    reference = F.observation(
        "unverified-pressure",
        experiment.experiment_id,
        identity,
        Decimal("0.2"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="kems-053-stolyarova-1991",
    )
    reference = replace(
        reference,
        evidence=replace(
            reference.evidence,
            original_method_class="measured_direct",
        ),
    )
    prediction = _partial_prediction(Engine.INTERNAL_ANALYTICAL, reference)
    context = _context(F.work(), experiment, reference, review="reviewed")
    residual, _ = compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        prediction=prediction,
    )
    assert residual.status is ResidualStatus.NO_BAND
    assert residual.numeric is not None
    assert residual.numeric.decision_band is None
    assert residual.score_eligible is False
    assert "not_flagged_stratum" in residual.exclusions
    assert any(
        notice.kind is NoticeKind.UNVERIFIED_APPARATUS
        and notice.reason == "apparatus_unverified:background_pressure"
        for notice in residual.notices
    )

    missing = replace(
        reference,
        value=Value(kind=ValueKind.UNAVAILABLE, unavailable_reason="printed dash"),
    )
    refused, _ = compile_residual(
        missing,
        Engine.INTERNAL_ANALYTICAL,
        context=_context(F.work(), experiment, missing, review="reviewed"),
        prediction=prediction,
    )
    assert refused.status is ResidualStatus.REFUSED
    assert refused.numeric is None


def test_catalogue_composition_is_flagged_and_excluded_from_headline() -> None:
    experiment = F.kems_experiment()
    composition = Composition(
        basis="sample_catalog_proxy",
        components=(("SiO2", Decimal("0.5")), ("Na2O", Decimal("0.5"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
        proxy_flag="composition_from_sample_catalog",
        proxy_source="catalogue-10017",
        analysis_selection_rule="first complete whole-sample analysis",
    )
    identity = F.activity_identity(composition=composition)
    reference = F.observation(
        "catalogue-composition",
        experiment.experiment_id,
        identity,
        Decimal("0.2"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="work-1",
    )
    prediction = _predict(Decimal("0.3"), identity)
    context = _context(F.work(), experiment, reference, review="reviewed")
    residual, _ = compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        prediction=prediction,
    )
    assert residual.status is ResidualStatus.NO_BAND
    assert residual.numeric is not None
    assert residual.score_eligible is False
    assert "not_flagged_stratum" in residual.exclusions
    assert any(
        notice.kind is NoticeKind.COMPOSITION_FROM_SAMPLE_CATALOG
        for notice in residual.notices
    )

    from simulator.battery.score import flagged_stratum_rows, headline_rows

    assert headline_rows((residual,), context=context, engines=(Engine.INTERNAL_ANALYTICAL,))[0][
        "n"
    ] == 0
    rows = flagged_stratum_rows((residual,), engines=(Engine.INTERNAL_ANALYTICAL,))
    assert [(row["stratum"], row["n"]) for row in rows] == [
        ("catalogue-composition", 1)
    ]
    payload = residual_to_plain(residual)
    for tier in ("measured", "compilation"):
        assert headline_payloads(
            (payload,), (Engine.INTERNAL_ANALYTICAL,), tier=tier
        )[0]["n"] == 0
    assert [(row["stratum"], row["n"]) for row in flagged_stratum_payloads(
        (payload,), (Engine.INTERNAL_ANALYTICAL,)
    )] == [("catalogue-composition", 1)]


def test_source_internal_inconsistency_is_flagged_reported_and_excluded() -> None:
    from simulator.battery.score import (
        FLAGGED_STRATUM_SOURCE_INTERNALLY_INCONSISTENT,
        flagged_strata,
        flagged_stratum_rows,
        headline_rows,
    )

    notice = Notice(
        kind=NoticeKind.SOURCE_DISAGREEMENT,
        affected_quantities=(Quantity.P_PARTIAL,),
        reason=(
            "source_internally_inconsistent: JANAF equilibrium check of "
            "Ca(g) + 1/2 O2(g) = CaO(g) predicts P_CaO 3.70 dex below "
            "printed; reconciling requires about 141 kJ/mol."
        ),
        origin="stolyarova-cao-row",
    )
    experiment = F.kems_experiment()
    identity = replace(
        _partial_identity(),
        total_pressure_Pa=State.of(Decimal("1e-6")),
    )
    reference = F.observation(
        "source-inconsistent-kems-row",
        experiment.experiment_id,
        identity,
        Decimal("0.2"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="kems-053-stolyarova-1991",
        notices=(notice,),
    )
    reference = replace(
        reference,
        evidence=replace(
            reference.evidence,
            original_method_class="measured_direct",
        ),
    )
    prediction = _partial_prediction(Engine.INTERNAL_ANALYTICAL, reference)
    context = _context(F.work(), experiment, reference, review="reviewed")
    residual, _ = compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        prediction=prediction,
    )
    assert residual.score_eligible is False
    assert "not_flagged_stratum" in residual.exclusions
    assert flagged_strata(residual.notices) == (
        FLAGGED_STRATUM_SOURCE_INTERNALLY_INCONSISTENT,
    )

    banded = F.residual(
        f"source-inconsistent-banded::{Engine.INTERNAL_ANALYTICAL.value}",
        reference.observation_id,
        candidate="engine-row",
        status=ResidualStatus.MISMATCH,
        rail=Rail.VAPOUR,
        score_eligible=True,
        notices=(notice,),
        numeric=ResidualNumeric(
            operation=MetricOperation.DEX,
            unit="dimensionless",
            value=Decimal("3.70"),
            decision_band=DecisionBand(Decimal("0.1461"), "dimensionless", "kems"),
        ),
        quantity=Quantity.P_PARTIAL,
    )
    work = F.work()
    issues = validate_residual(
        banded,
        {reference.observation_id: reference},
        {experiment.experiment_id: experiment},
        {work.work_id: work},
    )
    assert any(
        issue.path == "residual.score_eligible"
        and "flagged stratum" in issue.detail
        for issue in issues
    )
    vapour_headline = next(
        row
        for row in headline_rows((banded,), engines=(Engine.INTERNAL_ANALYTICAL,))
        if row["rail"] == Rail.VAPOUR.value
    )
    assert vapour_headline["n"] == 0
    assert [(row["stratum"], row["n"]) for row in flagged_stratum_rows(
        (banded,), engines=(Engine.INTERNAL_ANALYTICAL,)
    )] == [(FLAGGED_STRATUM_SOURCE_INTERNALLY_INCONSISTENT, 1)]
    payload = residual_to_plain(banded)
    for tier in ("measured", "compilation"):
        assert headline_payloads(
            (payload,), (Engine.INTERNAL_ANALYTICAL,), tier=tier
        )[0]["n"] == 0
    assert [(row["stratum"], row["n"]) for row in flagged_stratum_payloads(
        (payload,), (Engine.INTERNAL_ANALYTICAL,)
    )] == [(FLAGGED_STRATUM_SOURCE_INTERNALLY_INCONSISTENT, 1)]

    ordinary_disagreement = replace(notice, reason="independent source values differ")
    assert flagged_strata((ordinary_disagreement,)) == ()


def test_knudsen_absolute_flux_requires_orifice_area() -> None:
    exp = F.kems_experiment(orifice_area=None, clausing=None)
    assert exp.apparatus is not None
    exp = replace(
        exp,
        apparatus=replace(
            exp.apparatus,
            geometry=ApparatusGeometry(
                exposed_area_m2=Located(State.of(Decimal("1e-4"))),
            ),
        ),
    )
    gate = underdetermined_apparatus(exp, Quantity.MASS_LOSS_RATE)
    assert gate.passed is False
    assert gate.reason is RefusalReason.UNDERDETERMINED_APPARATUS
    missing = next(
        c.detail["missing"] for c in gate.checks if c.name == "geometry_determinants"
    )
    assert "orifice_area_m2" in missing


@pytest.mark.parametrize(
    "calibration",
    (
        {"standard": Located(State.of("Ag"))},
        {
            "standard": Located(
                State.of("Ag"),
                locator=F.loc(page=271),
                inference=Derivation(
                    "extract_inference", ("inferred=true",), (), "as_published"
                ),
            )
        },
    ),
)
def test_kems_calibration_requires_located_recorded_provenance(calibration) -> None:
    experiment = F.kems_experiment(
        orifice_area=None,
        clausing=None,
        kn=None,
        calibrated=False,
    )
    assert experiment.apparatus is not None
    experiment = replace(
        experiment,
        apparatus=replace(experiment.apparatus, calibration=calibration),
    )

    gate = underdetermined_apparatus(experiment, Quantity.P_PARTIAL)

    assert gate.passed is False
    assert gate.reason is RefusalReason.UNDERDETERMINED_APPARATUS
    assert gate.primary_check == "kems_calibration"
    check = next(item for item in gate.checks if item.name == "kems_calibration")
    assert check.detail["missing"] == ["calibration"]


def test_battery_score_script_runs_status_diff() -> None:
    src = Path("scripts/battery_score.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "status_diff_rows" in called


def test_battery_score_wrapper_matches_direct_score_store(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    literature = tmp_path / "data" / "literature"
    works = literature / "works"
    observations = literature / "observations-v2"
    battery = tmp_path / "data" / "battery"
    works.mkdir(parents=True)
    observations.mkdir()
    battery.mkdir()
    work_name = (
        "94aef07ca77601ab38121f5c1f9ae3d4dad4a6404dab4bbcf595c56a9a049471.yaml"
    )
    shutil.copy2(
        repo_root / "data" / "literature" / "works" / work_name,
        works / work_name,
    )
    observation_name = "vacuum_pyrolysis_measurements.yaml"
    shutil.copy2(
        repo_root / "data" / "literature" / "observations-v2" / observation_name,
        observations / observation_name,
    )
    (battery / "migration-report.md").write_text(
        "\n".join(
            (
                "rows in: 1",
                "records out (observations): 1",
                "works: 1",
                "experiments: 1",
                "queue size: 0",
                "hard issues: 0",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    (battery / "migration-queue.yaml").write_text("\n", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "data"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Battery score test",
            "-c",
            "user.email=battery-score-test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "test battery store",
        ],
        check=True,
    )

    source_id = "pomeroy_cardiff_2006_measurements"
    completed = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "battery_score.py"),
            "--root",
            str(tmp_path),
            "--engines",
            "internal-analytical",
            "--work",
            source_id,
            "--limit",
            "1",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

    wrapper_rows = tuple(
        json.loads(line)
        for line in (tmp_path / "data" / "battery" / "residuals.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    )
    wrapper_records = tuple(
        row for row in wrapper_rows if row.get("kind") != "battery_store_stamp"
    )

    engines = engines_from_names(["internal-analytical"])
    context = load_score_context(tmp_path)
    residuals, candidates = score_store(
        context,
        engines=engines,
        work_id=source_id,
        limit=1,
    )
    direct_records = tuple(
        json.loads(
            dumps_residual_line(residual, candidates.get(residual.candidate or ""))
        )
        for residual in residuals
    )
    assert wrapper_records == direct_records

    wrapper_digest = hashlib.sha256(
        "\n".join(
            json.dumps(
                record, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
            for record in wrapper_records
        ).encode("utf-8")
    ).hexdigest()
    direct_digest = hashlib.sha256(
        "\n".join(
            json.dumps(
                record, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
            for record in direct_records
        ).encode("utf-8")
    ).hexdigest()
    assert wrapper_digest == direct_digest


def test_status_diff_names_schema_axis_on_outcome_change() -> None:
    from simulator.battery.score import status_diff_rows

    diffs, unmapped = status_diff_rows(
        old_rows=[{"key": "old-a", "status": "match"}],
        new_rows=[{"key": "new-a", "status": "refused", "score_eligible": False}],
        key_map={"old-a": "new-a"},
    )
    assert unmapped == []
    assert len(diffs) == 1
    assert diffs[0]["old"] == "scored"
    assert diffs[0]["new"] == "refused"
    assert diffs[0]["axis"] == "score_eligible/admission/evidence/gate"

    match_flip, _ = status_diff_rows(
        old_rows=[{"key": "old-b", "status": "match"}],
        new_rows=[{"key": "new-b", "status": "mismatch", "score_eligible": True}],
        key_map={"old-b": "new-b"},
    )
    assert match_flip[0]["axis"] == "decision_band"
    assert match_flip[0]["old"] == "match"
    assert match_flip[0]["new"] == "mismatch"


def test_report_states_admission_alone_deaths_without_changing_the_rule() -> None:
    exp = F.tabulation_experiment()
    ident = F.o2_identity()
    admitted = F.observation(
        "o2-admitted",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="work-1",
    )
    pending = replace(
        admitted,
        observation_id="o2-pending",
        admission=replace(admitted.admission, status=AdmissionStatus.PENDING),
    )
    residual_admitted, _ = _compile(admitted, exp, _predict(Decimal("0"), ident))
    residual_pending, _ = _compile(pending, exp, _predict(Decimal("0"), ident))
    assert residual_admitted.score_eligible is True
    assert residual_pending.score_eligible is False
    assert residual_pending.status is ResidualStatus.MATCH
    assert residual_pending.numeric is not None
    assert residual_pending.exclusions == ("admission_admitted",)

    from simulator.battery.score import admission_census, render_score_report

    ctx = _context(F.work(), exp, admitted, pending)
    census = admission_census(
        (residual_admitted, residual_pending),
        context=ctx,
    )
    assert census["comparison_candidates_pending"] == 1
    assert census["comparison_candidates_admitted"] == 1
    assert census["residuals_admission_alone"] == 1
    assert census["unique_obs_admission_alone"] == 1
    report = render_score_report(
        (residual_admitted, residual_pending),
        context=ctx,
        engines=(Engine.INTERNAL_ANALYTICAL,),
    )
    assert "## Admission" in report
    assert "die on admission alone" in report
    assert "| residuals that die on admission alone | 1 |" in report
    assert "stay in the comparison set as diagnostics" in report
    assert "The admission rule is unchanged." in report


def test_non_thermo_quantities_keep_numeric_residual_without_invented_band() -> None:
    """Vapour / activity / alpha / yield keep residuals without a band."""

    from simulator.battery.score import populate_numeric

    for quantity in (
        Quantity.P_SAT,
        Quantity.P_PARTIAL,
        Quantity.ACTIVITY,
        Quantity.ACTIVITY_COEFFICIENT,
        Quantity.EVAPORATION_COEFFICIENT_ALPHA,
        Quantity.YIELD_FRACTION,
        Quantity.MASS_LOSS_FRACTION,
        Quantity.O2_YIELD,
    ):
        numeric, reason, detail = populate_numeric(
            quantity=quantity,
            candidate=Decimal("1"),
            reference=Decimal("1"),
            source_relation=SourceRelation.INDEPENDENT,
        )
        assert numeric is not None
        assert numeric.decision_band is None
        assert reason is None
        assert detail == {}

    thermo, reason, _ = populate_numeric(
        quantity=Quantity.DELTA_FG,
        candidate=Decimal("0"),
        reference=Decimal("0"),
        source_relation=SourceRelation.INDEPENDENT,
    )
    assert reason is None
    assert thermo is not None
    assert thermo.decision_band.rule.startswith("gibbs_battery_residual_ledger.yaml")

    exp = F.tabulation_experiment()
    ident = F.psat_identity("Na")
    ref = F.observation(
        "na-psat-perfect",
        exp.experiment_id,
        ident,
        Decimal("0.1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="work-1",
    )
    residual, _ = _compile(ref, exp, _predict(Decimal("0.1"), ident))
    assert residual.status is ResidualStatus.NO_BAND
    assert residual.score_eligible is True
    assert residual.numeric is not None
    assert residual.numeric.decision_band is None
    assert residual.refusal is None


def test_no_band_residual_carries_printed_uncertainty() -> None:
    exp = F.tabulation_experiment()
    ident = F.psat_identity("Na")
    ref = replace(
        F.observation(
            "na-psat-sigma",
            exp.experiment_id,
            ident,
            Decimal("0.1"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id="work-1",
        ),
        uncertainty=Uncertainty(
            kind=UncertaintyKind.PRINTED,
            verbatim={"sigma": "0.02"},
        ),
    )
    residual, _ = _compile(ref, exp, _predict(Decimal("0.2"), ident))
    assert residual.status is ResidualStatus.NO_BAND
    assert residual.numeric is not None
    assert residual.numeric.metric_uncertainty == ref.uncertainty
    assert residual.numeric.value == Decimal(str(math.log10(2)))


def test_headline_records_keep_tiers_separate_and_count_no_band() -> None:
    from simulator.battery.score import headline_records

    exp = F.tabulation_experiment()
    measured_obs = F.observation(
        "headline-measured",
        exp.experiment_id,
        F.psat_identity("Na"),
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="work-1",
    )
    compilation_obs = F.observation(
        "headline-compilation",
        exp.experiment_id,
        F.psat_identity("Na"),
        Decimal("1"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    ctx = replace(
        _context(F.work(), exp, measured_obs, compilation_obs),
        origins={compilation_obs.observation_id: "compilations-janaf/Na.yaml"},
    )
    band = DecisionBand(Decimal("0.1"), "dimensionless", "test")
    measured = F.residual(
        "headline-measured::p_sat::vapour::internal-analytical",
        measured_obs.observation_id,
        candidate="engine-measured",
        status=ResidualStatus.MATCH,
        rail=Rail.VAPOUR,
        score_eligible=True,
        numeric=ResidualNumeric(
            operation=MetricOperation.DEX,
            unit="dimensionless",
            value=Decimal("0.1"),
            decision_band=band,
        ),
    )
    no_band = replace(
        measured,
        key="headline-no-band::p_sat::vapour::internal-analytical",
        reference=measured_obs.observation_id,
        status=ResidualStatus.NO_BAND,
        score_eligible=False,
        numeric=ResidualNumeric(
            operation=MetricOperation.DEX,
            unit="dimensionless",
            value=Decimal("0.2"),
            decision_band=None,
        ),
    )
    compilation = F.residual(
        "headline-compilation::p_sat::vapour::internal-analytical",
        compilation_obs.observation_id,
        candidate="engine-compilation",
        status=ResidualStatus.MISMATCH,
        rail=Rail.VAPOUR,
        score_eligible=False,
        numeric=ResidualNumeric(
            operation=MetricOperation.DEX,
            unit="dimensionless",
            value=Decimal("0.3"),
            decision_band=band,
        ),
    )
    records = headline_records(
        (measured, no_band, compilation),
        context=ctx,
        engines=(Engine.INTERNAL_ANALYTICAL,),
    )
    by_tier = {
        row["tier"]: row
        for row in records
        if row["rail"] == Rail.VAPOUR.value
        and row["engine"] == Engine.INTERNAL_ANALYTICAL.value
    }
    assert by_tier["measured"]["n"] == 2
    assert by_tier["measured"]["n_score_eligible"] == 1
    assert by_tier["measured"]["n_inside_band"] == 1
    assert by_tier["measured"]["band_width_dex"] == "0.1"
    assert by_tier["measured"]["n_no_band"] == 1
    assert by_tier["measured"]["match_rate"] == 1.0
    assert by_tier["compilation"]["n"] == 1
    assert by_tier["compilation"]["n_no_band"] == 0
    assert by_tier["compilation"]["rms_dex"] == "0.3"


def test_pyrolysis_yield_keeps_residual_without_robinot_floor() -> None:
    """n=2 same-rig O2 scatter is not a sourced agreement band."""

    from simulator.battery.score import decision_band_for, populate_numeric

    for quantity in (
        Quantity.YIELD_FRACTION,
        Quantity.O2_YIELD,
        Quantity.MASS_LOSS_FRACTION,
        Quantity.MASS_LOSS_FRACTION_VS_T,
        Quantity.EVOLVED_GAS_YIELD,
    ):
        assert decision_band_for(quantity, SourceRelation.INDEPENDENT) is None
        numeric, reason, detail = populate_numeric(
            quantity=quantity,
            candidate=Decimal("0.0105"),
            reference=Decimal("0.0105"),
            source_relation=SourceRelation.INDEPENDENT,
        )
        assert numeric is not None
        assert numeric.decision_band is None
        assert reason is None
        assert detail == {}


def test_vapour_rail_is_only_vapour_pressures() -> None:
    """Isotope and ion ratios, and Zn/Cu/Mg alphas, do not borrow a rail."""

    from simulator.battery.score import no_headline_rail_reason, rail_for_quantity

    for quantity in (Quantity.P_SAT, Quantity.P_PARTIAL, Quantity.P_REFERENCE):
        assert rail_for_quantity(quantity, species_formula="Na") is Rail.VAPOUR
        assert rail_for_quantity(quantity, species_formula="SiO") is Rail.SIO_EVOLUTION
        assert rail_for_quantity(quantity, species_formula="SiO2") is Rail.SIO_EVOLUTION
    assert rail_for_quantity(None) is None
    assert no_headline_rail_reason(None) == "quantity_unknown"
    for quantity in (
        Quantity.ISOTOPE_DELTA,
        Quantity.ION_INTENSITY_RATIO,
        Quantity.ION_INTENSITY,
    ):
        assert rail_for_quantity(quantity, species_formula="Si") is None
        assert no_headline_rail_reason(quantity) == "not_a_vapour_quantity"
    for formula in ("Zn", "Cu", "Mg"):
        rail = rail_for_quantity(
            Quantity.EVAPORATION_COEFFICIENT_ALPHA, species_formula=formula
        )
        assert rail is None
        assert rail is not Rail.SIO_EVOLUTION
        assert rail is not Rail.VAPOUR
    assert (
        no_headline_rail_reason(Quantity.EVAPORATION_COEFFICIENT_ALPHA)
        == "non_alkali_kinetic"
    )
    assert (
        rail_for_quantity(
            Quantity.EVAPORATION_COEFFICIENT_ALPHA, species_formula="SiO"
        )
        is Rail.SIO_EVOLUTION
    )
    assert (
        rail_for_quantity(Quantity.MASS_LOSS_RATE, species_formula="Na")
        is Rail.ALKALI_SHUTTLE
    )
    assert rail_for_quantity(Quantity.MASS_LOSS_RATE, species_formula="Zn") is None
    assert rail_for_quantity(Quantity.VISCOSITY) is None
    assert no_headline_rail_reason(Quantity.VISCOSITY) == "no_headline_rail:viscosity"


def test_non_alkali_alpha_is_refused_with_no_rail() -> None:
    ident = replace(
        F.psat_identity("Zn"), quantity=Quantity.EVAPORATION_COEFFICIENT_ALPHA
    )
    exp = F.tabulation_experiment()
    ref = F.observation(
        "zn-alpha",
        exp.experiment_id,
        ident,
        Decimal("0.2"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="work-1",
    )
    residual, _ = _compile(ref, exp, _predict(Decimal("0.2"), ident))
    assert residual.rail is None
    assert residual.status is ResidualStatus.REFUSED
    assert residual.numeric is None
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.UNSUPPORTED
    assert residual.refusal.detail.get("reason") == "non_alkali_kinetic"
    assert "::none::" in residual.key
    assert "SiO_evolution" not in residual.key
    assert "::vapour::" not in residual.key


def test_gibbs_band_applies_only_to_formation_energies() -> None:
    """Only formation Gibbs energy uses the sourced 1.0 kJ/mol band."""

    from simulator.battery.score import decision_band_for, populate_numeric

    for quantity in (
        Quantity.CP,
        Quantity.S,
        Quantity.H_MINUS_H298,
        Quantity.LOG10_KF,
    ):
        assert decision_band_for(quantity, SourceRelation.INDEPENDENT) is None
        numeric, reason, detail = populate_numeric(
            quantity=quantity,
            candidate=Decimal("10"),
            reference=Decimal("9"),
            source_relation=SourceRelation.INDEPENDENT,
        )
        assert numeric is not None
        assert numeric.decision_band is None
        assert reason is None
        assert detail == {}
    assert decision_band_for(Quantity.DELTA_FH, SourceRelation.INDEPENDENT) is None
    for quantity in (Quantity.DELTA_FG,):
        band = decision_band_for(quantity, SourceRelation.INDEPENDENT)
        assert band is not None
        assert band.value == Decimal("1.0")
        assert band.unit == "kJ_per_declared_mol_basis"
    numeric, reason, _detail = populate_numeric(
        quantity=Quantity.DELTA_FG,
        candidate=Decimal("1"),
        reference=Decimal("1"),
        source_relation=SourceRelation.INDEPENDENT,
    )
    assert reason is None
    assert numeric is not None
    assert numeric.decision_band.unit == "kJ_per_declared_mol_basis"


def test_applying_kj_band_to_cp_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forcing the Gibbs kJ band onto cp is a dimension refusal, not a match."""

    from simulator.battery import score as score_mod

    band = score_mod.THERMOCHEMISTRY_DECISION_BANDS[SourceRelation.INDEPENDENT]
    assert score_mod.band_dimension_matches(Quantity.CP, band) is False
    assert score_mod.band_dimension_matches(Quantity.S, band) is False
    assert score_mod.band_dimension_matches(Quantity.LOG10_KF, band) is False
    assert score_mod.band_dimension_matches(Quantity.DELTA_FG, band) is True
    assert score_mod.band_dimension_matches(Quantity.DELTA_FH, band) is True
    # H-H298 shares the kJ/mol dimension and is still not a sourced band.
    assert score_mod.band_dimension_matches(Quantity.H_MINUS_H298, band) is True
    assert (
        score_mod.decision_band_for(Quantity.H_MINUS_H298, SourceRelation.INDEPENDENT)
        is None
    )
    monkeypatch.setattr(
        score_mod,
        "decision_band_for",
        lambda quantity, source_relation: band,
    )
    numeric, reason, detail = score_mod.populate_numeric(
        quantity=Quantity.CP,
        candidate=Decimal("50"),
        reference=Decimal("40"),
        source_relation=SourceRelation.INDEPENDENT,
    )
    assert numeric is None
    assert reason is RefusalReason.DECISION_RULE_MISSING
    assert detail.get("reason") == "band_dimension_mismatch:cp"
    assert detail.get("quantity_unit") == "J_per_declared_mol_basis_per_K"
    assert detail.get("band_unit") == "kJ_per_declared_mol_basis"


def test_candidate_census_splits_admitted_from_pending() -> None:
    from simulator.battery.score import candidate_rail_census, render_score_report

    exp = F.tabulation_experiment()
    sio = replace(F.psat_identity("SiO"), quantity=Quantity.P_PARTIAL)
    potassium = F.psat_identity("K")
    zinc = replace(
        F.psat_identity("Zn"), quantity=Quantity.EVAPORATION_COEFFICIENT_ALPHA
    )
    admitted = F.observation(
        "sio-p",
        exp.experiment_id,
        sio,
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="work-1",
    )
    pending = F.observation(
        "k-psat",
        exp.experiment_id,
        potassium,
        Decimal("2"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        admission=AdmissionStatus.PENDING,
        source_id="work-1",
    )
    alpha = F.observation(
        "zn-alpha",
        exp.experiment_id,
        zinc,
        Decimal("0.2"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        admission=AdmissionStatus.PENDING,
        source_id="work-1",
    )
    ctx = _context(F.work(), exp, admitted, pending, alpha)
    rails, off = candidate_rail_census(ctx)
    by_rail = {row["rail"]: row for row in rails}
    assert by_rail["SiO_evolution"]["candidates"] == 1
    assert by_rail["SiO_evolution"]["admitted"] == 1
    assert by_rail["SiO_evolution"]["pending"] == 0
    assert by_rail["SiO_evolution"]["points"] == 1
    assert by_rail["vapour"]["candidates"] == 1
    assert by_rail["vapour"]["admitted"] == 0
    assert by_rail["vapour"]["pending"] == 1
    assert by_rail["vapour"]["points"] == 1
    assert len(off) == 1
    assert off[0]["reason"] == "non_alkali_kinetic"
    assert off[0]["candidates"] == 1
    assert off[0]["pending"] == 1
    report = render_score_report(
        (),
        context=ctx,
        engines=(Engine.INTERNAL_ANALYTICAL,),
    )
    assert "## Live candidate census" in report
    assert "| SiO_evolution | 1 | 1 | 0 | 1 |" in report
    assert "| vapour | 1 | 0 | 1 | 1 |" in report
    assert "| `non_alkali_kinetic` | 1 | 0 | 1 | 1 |" in report
    assert "Admitted is split from pending" in report
    assert "score_eligible is 0" in report
    assert "Pins were not compared" in report


def test_predict_success_path_does_not_hardcode_lineage_complete_false() -> None:
    from simulator.battery import score as score_mod

    tree = ast.parse(inspect.getsource(score_mod.predict_with_engine))
    produced_with_value = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Name) or func.id != "EnginePrediction":
            continue
        kws = {k.arg: k.value for k in node.keywords if k.arg}
        if "value" not in kws:
            continue
        value_node = kws["value"]
        if isinstance(value_node, ast.Constant) and value_node.value is None:
            continue
        produced_with_value += 1
        flag = kws.get("lineage_complete")
        hardcoded_false = isinstance(flag, ast.Constant) and flag.value is False
        assert not hardcoded_false, "production success path hard-codes lineage_complete=False"
    assert produced_with_value >= 1


def test_mapped_coefficient_sources_decide_circularity() -> None:
    from simulator.battery.score import (
        ENGINE_COEFFICIENT_SOURCES,
        expand_coefficient_sources,
        lineage_complete_for,
    )

    w = F.work()
    exp = F.tabulation_experiment()
    ident = F.o2_identity()
    ref = F.observation(
        "o2-ref",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="janaf-4th",
    )
    observations = {ref.observation_id: ref}
    experiments = {exp.experiment_id: exp}
    works = {w.work_id: w}
    vaporock_sources = expand_coefficient_sources(
        ENGINE_COEFFICIENT_SOURCES[Engine.VAPOROCK]
    )
    assert "janaf-4th" in vaporock_sources
    assert lineage_complete_for(
        vaporock_sources,
        works=works,
        observations=observations,
        experiments=experiments,
    ) is True
    same = resolve_source_relation(
        ref,
        vaporock_sources,
        True,
        works=works,
        observations=observations,
        experiments=experiments,
    )
    assert same is SourceRelation.SAME_INPUT

    independent_work = replace(F.work("kems-work"), source_ids=("kems-work",))
    independent_exp = F.tabulation_experiment(
        experiment_id="kems-exp", work_id="kems-work"
    )
    independent_ref = F.observation(
        "kems-ref",
        independent_exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="kems-work",
    )
    independent = resolve_source_relation(
        independent_ref,
        vaporock_sources,
        True,
        works={"kems-work": independent_work, w.work_id: w},
        observations={
            independent_ref.observation_id: independent_ref,
            ref.observation_id: ref,
        },
        experiments={
            independent_exp.experiment_id: independent_exp,
            exp.experiment_id: exp,
        },
    )
    assert independent is SourceRelation.INDEPENDENT


def test_openimcc_candidate_alias_has_complete_lineage_mapping() -> None:
    from simulator.battery.score import (
        ENGINE_COEFFICIENT_SOURCES,
        expand_coefficient_sources,
        lineage_complete_for,
    )

    candidate_sources = (
        *ENGINE_COEFFICIENT_SOURCES[Engine.OPENIMCC],
        "openimcc-pack-version:1.0.2",
        "openimcc-pack-digest:sha256:test",
    )
    expanded = expand_coefficient_sources(candidate_sources)
    assert "openimcc-v1.0.2" in candidate_sources
    assert "sf04-magma-companion-workbook" in expanded
    assert lineage_complete_for(candidate_sources) is True


@pytest.mark.parametrize(
    "candidate_sources",
    [
        pytest.param(
            ("openimcc-pack-version:1.0.2",),
            id="metadata-only",
        ),
        pytest.param(
            ("openimcc-gas-table:sf04-magma-companion-workbook",),
            id="prefix-concealed",
        ),
        pytest.param(
            (
                "openimcc-pack-version:1.0.2",
                "openimcc-pack-digest:sha256:test",
                "openimcc-gas-table:gas.csv",
            ),
            id="empty-after-strip",
        ),
    ],
)
def test_openimcc_metadata_lineage_fails_closed(
    candidate_sources: tuple[str, ...],
) -> None:
    from simulator.battery.score import lineage_complete_for

    reference = F.observation(
        "metadata-lineage-reference",
        "metadata-lineage-exp",
        F.o2_identity(),
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    observations = {reference.observation_id: reference}
    experiments = {
        reference.experiment_id: F.tabulation_experiment(
            experiment_id=reference.experiment_id,
        )
    }
    assert lineage_complete_for(candidate_sources) is False
    assert (
        resolve_source_relation(
            reference,
            candidate_sources,
            True,
            works={},
            observations=observations,
            experiments=experiments,
        )
        is SourceRelation.UNKNOWN
    )


def _openimcc_lineage_validation_case(coefficient_sources: tuple[str, ...]):
    reference_work = F.work("reference-work")
    reference_experiment = F.tabulation_experiment(
        experiment_id="reference-exp", work_id=reference_work.work_id
    )
    identity = F.o2_identity()
    reference = F.observation(
        "lineage-reference",
        reference_experiment.experiment_id,
        identity,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id=reference_work.work_id,
    )
    source_work = replace(
        F.work("sf04-work"),
        source_ids=("sf04-work", "sf04-magma-companion-workbook"),
    )
    source_experiment = F.tabulation_experiment(
        experiment_id="sf04-exp", work_id=source_work.work_id
    )
    source_observation = F.observation(
        "sf04-input",
        source_experiment.experiment_id,
        identity,
        Decimal("0"),
        source_id=source_work.work_id,
    )
    candidate = F.observation(
        "engine:openimcc:lineage-reference",
        reference_experiment.experiment_id,
        identity,
        Decimal("0"),
        evidence=EvidenceClass.ENGINE_PREDICTION,
        source_id=reference.source_id or "reference-work",
        engine=EngineTrace(
            name=Engine.OPENIMCC,
            channel="openimcc",
            run_id="openimcc:test",
            coefficient_sources=coefficient_sources,
            lineage_complete=True,
        ),
        authority=Authority.CERTIFIED,
    )
    numeric = ResidualNumeric(
        operation=MetricOperation.ABSOLUTE,
        unit="kJ_per_declared_mol_basis",
        value=Decimal("0"),
        decision_band=DecisionBand(Decimal("1"), "kJ_per_declared_mol_basis", "test"),
    )
    residual = F.residual(
        "lineage-validation",
        reference.observation_id,
        candidate=candidate.observation_id,
        status=ResidualStatus.MATCH,
        rail=Rail.THERMOCHEMISTRY,
        score_eligible=True,
        numeric=numeric,
        source_relation=SourceRelation.INDEPENDENT,
        experiment_id=reference_experiment.experiment_id,
        quantity=Quantity.DELTA_FG,
    )
    return (
        residual,
        {
            reference.observation_id: reference,
            candidate.observation_id: candidate,
            source_observation.observation_id: source_observation,
        },
        {
            reference_experiment.experiment_id: reference_experiment,
            source_experiment.experiment_id: source_experiment,
        },
        {reference_work.work_id: reference_work, source_work.work_id: source_work},
    )


def test_openimcc_lineage_metadata_passes_residual_validation() -> None:
    residual, observations, experiments, works = _openimcc_lineage_validation_case(
        (
            "sf04-magma-companion-workbook",
            "openimcc-pack-version:1.0.2",
            "openimcc-pack-digest:sha256:test",
            "openimcc-gas-table:test.csv",
        )
    )
    assert validate_residual(
        residual,
        observations,
        experiments,
        works,
    ) == []


def test_openimcc_metadata_only_lineage_fails_residual_validation() -> None:
    residual, observations, experiments, works = _openimcc_lineage_validation_case(
        (
            "openimcc-pack-version:1.0.2",
            "openimcc-pack-digest:sha256:test",
            "openimcc-gas-table:test.csv",
        )
    )
    issues = validate_residual(residual, observations, experiments, works)
    assert any(
        issue.path == "residual.source_relation"
        and issue.reason is RefusalReason.LINEAGE_UNKNOWN
        for issue in issues
    )


def test_missing_live_result_is_coverage_failure() -> None:
    record = PinBandRecord(
        key="missing",
        expected_outcome="match",
        evidence="test",
        centre=Decimal("0"),
        metric_operation="dex",
        metric_unit="dimensionless",
        pin_band_value=Decimal("0.01"),
        pin_band_unit="dimensionless",
    )
    failures = pin_failures([], [record])
    assert failures
    assert failures[0]["reason"] == "coverage_failure"


def _stamp(**overrides) -> dict:
    payload = {
        "kind": "battery_store_stamp",
        "revision": "aaa111ccc",
        "rows_in": 10,
        "observations": 20,
        "works": 2,
        "experiments": 3,
        "queue": 4,
        "hard_issues": 5,
    }
    payload.update(overrides)
    return payload


def test_store_stamp_mismatch_warns() -> None:
    from simulator.battery.score import store_stamp_mismatch_warning

    live = _stamp()
    recorded = _stamp(revision="bbb222ddd")
    warning = store_stamp_mismatch_warning(recorded, live)
    assert warning is not None
    assert "bbb222ddd" in warning
    assert "aaa111ccc" in warning


def test_store_stamp_match_is_silent() -> None:
    from simulator.battery.score import store_stamp_mismatch_warning

    live = _stamp()
    assert store_stamp_mismatch_warning(live, live) is None
    assert store_stamp_mismatch_warning(_stamp(), _stamp()) is None


def test_unstamped_residuals_warn() -> None:
    from simulator.battery.score import store_stamp_mismatch_warning

    live = _stamp()
    warning = store_stamp_mismatch_warning(None, live)
    assert warning is not None
    assert "no store revision" in warning
    assert "aaa111ccc" in warning


def test_emit_store_stamp_mismatch_is_reachable() -> None:
    from simulator.battery.score import emit_store_stamp_mismatch_warning

    live = _stamp()
    with pytest.warns(UserWarning, match="recorded store `bbb222ddd`"):
        warning = emit_store_stamp_mismatch_warning(_stamp(revision="bbb222ddd"), live)
    assert warning is not None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert emit_store_stamp_mismatch_warning(live, live) is None
        assert caught == []


def test_write_residuals_stamps_derived_store_and_load_skips_it(tmp_path: Path) -> None:
    import json

    from simulator.battery.score import (
        STORE_STAMP_KIND,
        derive_store_stamp,
        load_residuals_jsonl,
        load_residuals_stamp,
        write_residuals_jsonl,
    )

    exp = F.tabulation_experiment()
    ident = F.o2_identity()
    residual, candidate = _compile(
        F.observation(
            "o2-ref",
            exp.experiment_id,
            ident,
            Decimal("0"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id="work-1",
        ),
        exp,
        _predict(Decimal("0"), ident),
    )
    path = tmp_path / "residuals.jsonl"
    candidates = {}
    if candidate is not None:
        candidates[candidate.observation_id] = candidate
    write_residuals_jsonl((residual,), candidates, path)
    live = derive_store_stamp()
    stamp = load_residuals_stamp(path)
    assert stamp is not None
    assert stamp["kind"] == STORE_STAMP_KIND
    assert stamp["revision"] == live["revision"]
    assert stamp["observations"] == live["observations"]
    first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert first["kind"] == STORE_STAMP_KIND
    rows = load_residuals_jsonl(path)
    assert len(rows) == 1
    assert rows[0]["key"] == residual.key
    assert "kind" not in rows[0] or rows[0].get("kind") != STORE_STAMP_KIND


def test_write_headline_summary_has_tier_and_null_data_scatter_slot(tmp_path: Path) -> None:
    import json

    from simulator.battery.score import write_headline_summary_json

    exp = F.tabulation_experiment()
    ident = F.psat_identity("Na")
    reference = F.observation(
        "summary-no-band",
        exp.experiment_id,
        ident,
        Decimal("0.1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="work-1",
    )
    residual, _ = _compile(reference, exp, _predict(Decimal("0.2"), ident))
    ctx = _context(F.work(), exp, reference)
    path = tmp_path / "score-summary.json"
    write_headline_summary_json(
        (residual,),
        path,
        context=ctx,
        engines=(Engine.INTERNAL_ANALYTICAL,),
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    row = next(
        row
        for row in payload["records"]
        if row["tier"] == "measured" and row["rail"] == Rail.VAPOUR.value
    )
    assert row["n"] == 1
    assert row["n_no_band"] == 1
    assert row["rms_dex"] is not None
    assert row["data_scatter_ratio"] is None


def test_score_report_names_the_measured_store() -> None:
    from simulator.battery.score import derive_store_stamp, render_score_report

    exp = F.tabulation_experiment()
    ident = F.o2_identity()
    residual, _ = _compile(
        F.observation(
            "o2-ref",
            exp.experiment_id,
            ident,
            Decimal("0"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id="work-1",
        ),
        exp,
        _predict(Decimal("0"), ident),
    )
    ctx = _context(F.work(), exp)
    report = render_score_report((residual,), context=ctx, engines=(Engine.INTERNAL_ANALYTICAL,))
    stamp = derive_store_stamp()
    assert f"This report measured store `{stamp['revision']}`" in report
    assert f"{stamp['rows_in']} rows in" in report
    assert f"{stamp['observations']} observations" in report
    assert f"{stamp['works']} works" in report
    assert f"{stamp['experiments']} experiments" in report
    assert f"queue {stamp['queue']}" in report
    assert f"{stamp['hard_issues']} hard issues" in report
    assert "RMS dex" in report
    assert "median abs dex" in report
    assert "n score eligible" in report
    assert "n inside band" in report
    assert "n no band" in report
    assert "Warning:" not in report


def test_score_report_from_payloads_surfaces_mismatch_warning() -> None:
    from simulator.battery.score import render_score_report_from_payloads

    live = _stamp()
    recorded = _stamp(revision="bbb222ddd")
    from simulator.battery.score import store_stamp_mismatch_warning

    warning = store_stamp_mismatch_warning(recorded, live)
    report = render_score_report_from_payloads(
        [],
        engines=(Engine.INTERNAL_ANALYTICAL,),
        hostname="test",
        store_stamp=recorded,
        mismatch_warning=warning,
    )
    assert "This report measured store `bbb222ddd`" in report
    assert "Warning:" in report
    assert "bbb222ddd" in report
    assert "aaa111ccc" in report
    silent = render_score_report_from_payloads(
        [],
        engines=(Engine.INTERNAL_ANALYTICAL,),
        hostname="test",
        store_stamp=live,
        mismatch_warning=None,
    )
    assert "This report measured store `aaa111ccc`" in silent
    assert "Warning:" not in silent


def test_payload_headline_skips_missing_rail() -> None:
    from simulator.battery.score import headline_payloads

    rows = headline_payloads(
        [{"key": "off-rail::none::internal-analytical", "status": "refused"}],
        (Engine.INTERNAL_ANALYTICAL,),
    )

    assert all(row["n_candidates"] == 0 for row in rows)


def test_unstamped_ledger_report_is_unknown_provenance() -> None:
    from simulator.battery.score import (
        render_score_report_from_payloads,
        store_stamp_mismatch_warning,
    )

    live = _stamp()
    warning = store_stamp_mismatch_warning(None, live)
    report = render_score_report_from_payloads(
        [],
        engines=(Engine.INTERNAL_ANALYTICAL,),
        hostname="test",
        store_stamp=None,
        mismatch_warning=warning,
    )
    assert "The measuring store for this ledger is unknown" in report
    assert "This report measured store" not in report
    assert "Warning:" in report
    assert "no store revision" in report
    assert "aaa111ccc" in report
    quiet = render_score_report_from_payloads(
        [],
        engines=(Engine.INTERNAL_ANALYTICAL,),
        hostname="test",
        store_stamp=None,
        mismatch_warning=None,
    )
    assert "The measuring store for this ledger is unknown" in quiet
    assert "This report measured store" not in quiet


def test_unknown_provenance_mismatch_is_as_loud_as_disagreement() -> None:
    from simulator.battery.score import (
        emit_store_stamp_mismatch_warning,
        store_stamp_mismatch_warning,
    )

    live = _stamp()
    unknown = store_stamp_mismatch_warning(None, live)
    disagreed = store_stamp_mismatch_warning(_stamp(revision="bbb222ddd"), live)
    assert unknown is not None
    assert disagreed is not None
    assert "no store revision" in unknown
    assert "aaa111ccc" in unknown
    assert "bbb222ddd" in disagreed
    assert store_stamp_mismatch_warning(live, live) is None
    with pytest.warns(UserWarning, match="no store revision"):
        emitted = emit_store_stamp_mismatch_warning(None, live)
    assert emitted == unknown
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert emit_store_stamp_mismatch_warning(live, live) is None
        assert caught == []


def test_stamp_existing_residuals_refuses_unregenerated_body(tmp_path: Path) -> None:
    from simulator.battery.score import (
        UnregeneratedLedgerStampError,
        stamp_existing_residuals_jsonl,
    )

    path = tmp_path / "residuals.jsonl"
    body = '{"key":"existing-row"}\n'
    path.write_text(body, encoding="utf-8")
    with pytest.raises(UnregeneratedLedgerStampError, match="not regenerated"):
        stamp_existing_residuals_jsonl(path, _stamp())
    assert path.read_text(encoding="utf-8") == body


def test_battery_score_script_does_not_take_a_hand_stamp() -> None:
    src = Path("scripts/battery_score.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    flags = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "add_argument":
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                flags.append(arg.value)
    assert "--revision" not in flags
    assert "--store-revision" not in flags
    assert "--stamp" not in flags
    assert "recorded if recorded is not None else live" not in src
    assert "derive_store_stamp" in src
    from simulator.battery import score as score_mod

    tree = ast.parse(inspect.getsource(score_mod.write_residuals_jsonl))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "derive_store_stamp" in called


def test_imcc_complex_saturation_routes_only_own_prediction() -> None:
    from types import SimpleNamespace

    from simulator.battery.score import (
        cell_notices,
        flagged_stratum_rows,
        headline_rows,
    )

    experiment = F.kems_experiment()
    identity = F.activity_identity()
    reference = F.observation(
        "ts1985-complex-saturation",
        experiment.experiment_id,
        identity,
        Decimal("0.2"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="work-1",
    )
    notice = cell_notices(
        Quantity.ACTIVITY,
        Engine.IMCC_SF04,
        SimpleNamespace(
            notices=(
                {
                    "kind": "imcc_complex_saturation",
                    "flag": "species-coverage-edge:Na2O",
                    "reason": "species-coverage-edge:Na2O",
                    "acid_sink_ratio": 0.0,
                },
            )
        ),
    )
    context = _context(F.work(), experiment, reference, review="reviewed")
    saturated_prediction = replace(
        _predict(Decimal("0.3"), identity),
        engine=Engine.IMCC_SF04,
        channel=Engine.IMCC_SF04.value,
        unit="dimensionless",
        notices=notice,
    )
    unaffected_prediction = replace(
        _predict(Decimal("0.3"), identity),
        engine=Engine.OPENIMCC,
        channel=Engine.OPENIMCC.value,
        unit="dimensionless",
    )
    saturated, _ = compile_residual(
        reference,
        Engine.IMCC_SF04,
        context=context,
        prediction=saturated_prediction,
    )
    unaffected, _ = compile_residual(
        reference,
        Engine.OPENIMCC,
        context=context,
        prediction=unaffected_prediction,
    )

    assert saturated.numeric is not None
    assert saturated.score_eligible is False
    assert "not_flagged_stratum" in saturated.exclusions
    assert any(
        item.kind is NoticeKind.IMCC_COMPLEX_SATURATION
        and item.reason == "species-coverage-edge:Na2O"
        for item in saturated.notices
    )
    assert unaffected.numeric is not None
    assert unaffected.score_eligible is True

    headline = headline_rows(
        (saturated, unaffected),
        context=context,
        engines=(Engine.IMCC_SF04, Engine.OPENIMCC),
    )
    by_engine = {
        row["engine"]: row for row in headline if row["rail"] == saturated.rail.value
    }
    assert by_engine[Engine.IMCC_SF04.value]["n"] == 0
    assert by_engine[Engine.OPENIMCC.value]["n"] == 1
    assert by_engine[Engine.OPENIMCC.value]["n_score_eligible"] == 1

    flagged = flagged_stratum_rows((saturated,), engines=(Engine.IMCC_SF04,))
    assert [(row["stratum"], row["n"]) for row in flagged] == [
        ("imcc_complex_saturation", 1)
    ]
    payload = residual_to_plain(saturated)
    assert any(
        row["kind"] == "imcc_complex_saturation" for row in payload["notices"]
    )
    assert [(row["stratum"], row["n"]) for row in flagged_stratum_payloads(
        (payload,), (Engine.IMCC_SF04,)
    )] == [("imcc_complex_saturation", 1)]


def test_non_allibert_typed_solid_activity_uses_fusion_conversion() -> None:
    from simulator.battery.generators.janaf import (
        JANAF_R_J_PER_MOL_K,
        janaf_fusion_energy,
    )
    from simulator.battery.score import _fusion_comparison_reference

    temperature = Decimal("2000")
    composition = Composition(
        basis="printed_mole_fraction",
        components=(("CaO", Decimal("0.8")), ("Al2O3", Decimal("0.2"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    experiment = F.kems_experiment()
    identity = F.activity_identity(
        formula="CaO",
        T_K=temperature,
        endmember_phase=Phase.CR,
        component_basis="CaO",
        composition=composition,
    )
    reference = F.observation(
        "other-source-cao-solid-activity",
        experiment.experiment_id,
        identity,
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="another-source",
    )

    converted = _fusion_comparison_reference(reference)
    delta_g = janaf_fusion_energy("CaO", temperature).delta_g_fus_kJ_per_mol
    expected = (-delta_g * Decimal(1000) / (JANAF_R_J_PER_MOL_K * temperature)).exp()
    assert converted.value.point == expected
    assert converted.identity.reference_state.value.endmember.phase.value is Phase.L
    assert converted.source_id == "another-source"


def test_fusion_conversion_without_janaf_pair_returns_typed_notice() -> None:
    from simulator.battery.score import _fusion_comparison_reference

    experiment = F.kems_experiment()
    identity = F.activity_identity(
        formula="NaO0.5",
        T_K=Decimal("1000"),
        endmember_phase=Phase.CR,
        component_basis="NaO0.5",
    )
    reference = F.observation(
        "unsupported-solid-activity",
        experiment.experiment_id,
        identity,
        Decimal("0.5"),
        source_id="another-source",
    )

    result = _fusion_comparison_reference(reference)

    assert result.value.point == reference.value.point
    assert result.identity.reference_state.value.endmember.phase.value is Phase.CR
    notice = next(n for n in result.notices if n.kind is NoticeKind.OUT_OF_GAMMA_DOMAIN)
    assert "NaO0.5" in notice.reason
    assert "no JANAF fusion table pair" in notice.reason
    assert notice.band


def test_fusion_conversion_without_unique_janaf_crossing_returns_typed_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.battery.generators import janaf
    from simulator.battery.score import _fusion_comparison_reference

    experiment = F.kems_experiment()
    identity = F.activity_identity(
        formula="CaO",
        T_K=Decimal("2000"),
        endmember_phase=Phase.CR,
        component_basis="CaO",
    )
    reference = F.observation(
        "ambiguous-crossing-cao-activity",
        experiment.experiment_id,
        identity,
        Decimal("0.5"),
        source_id="another-source",
    )

    def no_unique_crossing(formula: str, temperature_K: Decimal):
        raise ValueError(f"{formula}: expected one JANAF cr/l crossing, got []")

    monkeypatch.setattr(janaf, "janaf_fusion_energy", no_unique_crossing)
    result = _fusion_comparison_reference(reference)

    assert result.value.point == reference.value.point
    assert result.identity.reference_state.value.endmember.phase.value is Phase.CR
    notice = next(n for n in result.notices if n.kind is NoticeKind.OUT_OF_GAMMA_DOMAIN)
    assert "CaO" in notice.reason
    assert "expected one JANAF cr/l crossing" in notice.reason


def test_out_of_janaf_range_activity_notice_does_not_abort_score_store() -> None:
    from simulator.battery.score import (
        FLAGGED_STRATUM_REFERENCE_CONVERTED_VIA_FUSION,
        _fusion_comparison_reference,
    )

    experiment = F.kems_experiment()
    identity = F.activity_identity(
        formula="CaO",
        T_K=Decimal("100"),
        endmember_phase=Phase.CR,
        component_basis="CaO",
    )
    reference = F.observation(
        "out-of-janaf-range-cao-activity",
        experiment.experiment_id,
        identity,
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="another-source",
    )

    unconverted = _fusion_comparison_reference(reference)
    assert unconverted.value.point == reference.value.point
    assert unconverted.identity.reference_state.value.endmember.phase.value is Phase.CR
    assert not any(
        notice.reason.startswith(f"{FLAGGED_STRATUM_REFERENCE_CONVERTED_VIA_FUSION};")
        for notice in unconverted.notices
    )

    residuals, _ = score_store(
        _context(F.work(), experiment, reference, review="reviewed"),
        engines=(Engine.INTERNAL_ANALYTICAL,),
    )
    assert len(residuals) == 1
    notice = next(
        notice
        for notice in residuals[0].notices
        if notice.kind is NoticeKind.OUT_OF_GAMMA_DOMAIN
    )
    assert "CaO" in notice.reason
    assert "outside the JANAF table range" in notice.reason
    assert "JANAF table ranges" in notice.band


def test_allibert_solid_activity_fusion_conversion_is_diagnostic_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.battery.generators.janaf import (
        JANAF_R_J_PER_MOL_K,
        janaf_fusion_energy,
    )
    from simulator.battery.score import (
        FLAGGED_STRATUM_REFERENCE_CONVERTED_VIA_FUSION,
        _fusion_comparison_reference,
        flagged_stratum_rows,
        headline_rows,
    )

    temperature = Decimal("2000")
    composition = Composition(
        basis="printed_mole_fraction",
        components=(("CaO", Decimal("0.8")), ("Al2O3", Decimal("0.2"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    experiment = F.kems_experiment()
    identity = F.activity_identity(
        formula="CaO",
        T_K=temperature,
        endmember_phase=Phase.CR,
        component_basis="CaO",
        composition=composition,
    )
    reference = F.observation(
        "allibert-cao-solid-activity",
        experiment.experiment_id,
        identity,
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="kems-051-allibert-1981",
    )

    # JANAF CaO(l)-CaO(cr) is 29.537 kJ/mol at 2000 K; at its 3200 K
    # crossing it is zero, so the conversion is exactly unity there.
    fusion = janaf_fusion_energy("CaO", temperature)
    assert fusion.delta_g_fus_kJ_per_mol == Decimal("29.537")
    assert fusion.melting_temperature_K == Decimal("3200.0")
    at_melting = janaf_fusion_energy("CaO", fusion.melting_temperature_K)
    assert at_melting.delta_g_fus_kJ_per_mol == 0
    at_melting_reference = replace(
        reference,
        observation_id="allibert-cao-at-janaf-melting",
        identity=replace(
            identity, temperature_K=State.of(fusion.melting_temperature_K)
        ),
    )
    assert (
        _fusion_comparison_reference(at_melting_reference).value.point
        == Decimal("1")
    )
    alumina_fusion = janaf_fusion_energy("Al2O3", Decimal("2060"))
    assert alumina_fusion.delta_g_fus_kJ_per_mol == Decimal("11.8498")
    assert Decimal("2325.9") < alumina_fusion.melting_temperature_K < Decimal("2326.0")
    assert alumina_fusion.accepted_melting_temperature_K == Decimal("2327")
    alumina_reference = replace(
        reference,
        observation_id="allibert-alumina-solid-activity",
        identity=F.activity_identity(
            formula="Al2O3",
            T_K=Decimal("2060"),
            endmember_phase=Phase.CR,
            component_basis="Al2O3",
            composition=composition,
        ),
    )
    converted_alumina = _fusion_comparison_reference(alumina_reference)
    alumina_notice = next(
        notice
        for notice in converted_alumina.notices
        if notice.reason.startswith(
            f"{FLAGGED_STRATUM_REFERENCE_CONVERTED_VIA_FUSION};"
        )
    )
    assert "distance_below_JANAF_Tm=265.931928687" in alumina_notice.reason
    assert "accepted_Tm~2327 K" in alumina_notice.reason
    silica_fusion = janaf_fusion_energy("SiO2", Decimal("1933"))
    assert silica_fusion.delta_g_fus_kJ_per_mol == Decimal("0.27784")
    assert Decimal("1994.4") < silica_fusion.melting_temperature_K < Decimal("1994.5")
    assert silica_fusion.accepted_melting_temperature_K == Decimal("1986")
    converted = _fusion_comparison_reference(reference)
    expected = (
        -fusion.delta_g_fus_kJ_per_mol
        * Decimal(1000)
        / (JANAF_R_J_PER_MOL_K * temperature)
    ).exp()
    assert abs(converted.value.point - expected) < Decimal("1e-26")
    assert converted.identity.reference_state.value.endmember.phase.value is Phase.L
    assert converted.evidence.class_.is_unknown
    assert reference.value.point == Decimal("1")
    assert reference.identity.reference_state.value.endmember.phase.value is Phase.CR

    prediction = EnginePrediction(
        engine=Engine.INTERNAL_ANALYTICAL,
        channel="internal-analytical",
        execution=Execution(
            state=ExecutionState.PRODUCED,
            call_evidence="test:liquid-reference-activity",
        ),
        value=converted.value.point,
        unit="dimensionless",
        authority=Authority.CERTIFIED,
        coefficient_sources=("nasa-cea-thermo",),
        lineage_complete=True,
        identity=converted.identity,
    )
    context = _context(F.work(), experiment, reference, review="reviewed")
    residual, candidate = compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        prediction=prediction,
    )

    assert candidate is not None
    assert candidate.identity.reference_state.value.endmember.phase.value is Phase.L
    assert residual.status is ResidualStatus.NO_BAND
    assert residual.numeric is not None and residual.numeric.decision_band is None
    assert residual.score_eligible is False
    assert "not_flagged_stratum" in residual.exclusions
    assert "reference_measured_evidence" in residual.exclusions
    assert any(
        notice.reason.startswith(
            f"{FLAGGED_STRATUM_REFERENCE_CONVERTED_VIA_FUSION};"
        )
        and "distance_below_JANAF_Tm=1200" in notice.reason
        and "accepted_Tm~2886 K" in notice.reason
        for notice in residual.notices
    )
    assert headline_rows(
        (residual,), context=context, engines=(Engine.INTERNAL_ANALYTICAL,)
    )[0]["n"] == 0
    assert [
        (row["stratum"], row["n"])
        for row in flagged_stratum_rows(
            (residual,), engines=(Engine.INTERNAL_ANALYTICAL,)
        )
    ] == [(FLAGGED_STRATUM_REFERENCE_CONVERTED_VIA_FUSION, 1)]

    source_point_reference = replace(
        reference,
        identity=replace(
            identity,
            species=replace(
                identity.species, phase=State.unknown("unmapped printed melt phase")
            ),
            composition=State.unknown("no composition mapped from source"),
        ),
        point_conditions={"composition": Located(State.of(composition))},
    )
    converted_source_point = _fusion_comparison_reference(
        source_point_reference
    )
    assert converted_source_point.identity.composition.is_value
    assert converted_source_point.identity.composition.value == composition
    assert converted_source_point.identity.species.phase.value is Phase.L
    assert (
        converted_source_point.identity.reference_state.value.component_basis
        == "oxide"
    )
    alumina_source_point = replace(
        alumina_reference,
        identity=replace(
            alumina_reference.identity,
            species=replace(
                alumina_reference.identity.species,
                phase=State.unknown("unmapped printed melt phase"),
            ),
            composition=State.unknown("no composition mapped from source"),
        ),
        point_conditions={"composition": Located(State.of(composition))},
    )
    converted_alumina_source_point = _fusion_comparison_reference(
        alumina_source_point
    )
    assert (
        converted_alumina_source_point.identity.reference_state.value.component_basis
        == "oxide"
    )
    assert converted_alumina_source_point.identity.species.phase.value is Phase.L
    from simulator.battery.generators.bench import activity_request_for_engine

    activity_request = activity_request_for_engine(
        experiment, converted_alumina_source_point, Engine.ALPHAMELTS.value
    )
    assert activity_request is not None and activity_request.payload is not None

    model_derived_reference = replace(
        source_point_reference,
        observation_id="allibert-cao-model-derived-diagnostic",
        evidence=replace(
            reference.evidence,
            class_=State.of(EvidenceClass.MODEL_DERIVED),
        ),
    )
    diagnostic_context = _context(
        F.work(), experiment, model_derived_reference, review="reviewed"
    )

    dispatched_points = []

    def predict_diagnostic_activity(engine, point, *, handles, experiment):
        dispatched_points.append(point)
        assert point.identity.species.phase.value is Phase.L
        assert point.identity.reference_state.value.endmember.phase.value is Phase.L
        assert point.identity.composition.is_value
        assert point.identity.composition.value == composition
        return EnginePrediction(
            engine=engine,
            channel="internal-analytical",
            execution=Execution(
                state=ExecutionState.PRODUCED,
                call_evidence="test:liquid-reference-activity",
            ),
            value=point.value.point,
            unit="dimensionless",
            authority=Authority.CERTIFIED,
            coefficient_sources=("nasa-cea-thermo",),
            lineage_complete=True,
            identity=point.identity,
        )

    import simulator.battery.score as score_module

    monkeypatch.setattr(score_module, "predict_with_engine", predict_diagnostic_activity)
    diagnostic_residuals, diagnostic_candidates = score_store(
        diagnostic_context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
    )
    assert len(diagnostic_residuals) == 1
    assert len(dispatched_points) == 1
    diagnostic_residual = diagnostic_residuals[0]
    assert diagnostic_residual.reference == model_derived_reference.observation_id
    assert diagnostic_residual.numeric is not None
    assert diagnostic_residual.score_eligible is False
    assert diagnostic_residual.status is ResidualStatus.NO_BAND
    assert len(diagnostic_candidates) == 1
    candidate = next(iter(diagnostic_candidates.values()))
    assert candidate.evidence.class_.value is EvidenceClass.ENGINE_PREDICTION
    assert headline_rows(
        diagnostic_residuals,
        context=diagnostic_context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
    )[0]["n"] == 0
    assert [
        (row["stratum"], row["n"])
        for row in flagged_stratum_rows(
            diagnostic_residuals, engines=(Engine.INTERNAL_ANALYTICAL,)
        )
    ] == [(FLAGGED_STRATUM_REFERENCE_CONVERTED_VIA_FUSION, 1)]

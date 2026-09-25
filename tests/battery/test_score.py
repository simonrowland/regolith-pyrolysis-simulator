"""v2.1 scorer: score_eligible conjuncts, refusals, IMCC set, pins, determinism.

Expected values come from the schema contract, never from the code under test.
"""

from __future__ import annotations

import ast
import inspect
import warnings
from collections import Counter
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import (
    AdmissionStatus,
    Authority,
    Engine,
    EvidenceClass,
    ExecutionState,
    MethodToken,
    MetricOperation,
    NoticeKind,
    Quantity,
    Rail,
    RefusalReason,
    ResidualStatus,
    SourceRelation,
)
from simulator.battery.pins import (
    PinBandRecord,
    PinWidenError,
    assert_never_widen,
    pin_failures,
    tombstone_for_changed_identity,
)
from simulator.battery.records import (
    Apparatus,
    ApparatusGeometry,
    Execution,
    Located,
    Notice,
    ResidualNumeric,
    DecisionBand,
    Species,
    State,
)
from simulator.battery.validity import run_validity_gates, underdetermined_apparatus
from simulator.battery.score import (
    SCORE_ELIGIBLE_CONJUNCTS,
    SCORE_ENGINE_SET,
    EligibleConjuncts,
    EnginePrediction,
    ScoreContext,
    SINGLE_LIQUID_ENGINES,
    compile_residual,
    compute_metric,
    dumps_residual_line,
    engines_from_names,
    parse_species_formula,
    resolve_source_relation,
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


@pytest.mark.parametrize("engine", tuple(SINGLE_LIQUID_ENGINES))
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

    for engine in tuple(SINGLE_LIQUID_ENGINES):
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


def test_kems_partial_pressure_still_requires_effusion_packet() -> None:
    incomplete = F.kems_experiment(
        orifice_area=None, clausing=None, kn=None, calibrated=False
    )
    gate = underdetermined_apparatus(incomplete, Quantity.P_PARTIAL)
    assert gate.passed is False
    assert gate.reason is RefusalReason.UNDERDETERMINED_APPARATUS
    assert gate.primary_check == "geometry_determinants"
    missing = next(
        c.detail["missing"] for c in gate.checks if c.name == "geometry_determinants"
    )
    assert "orifice_area_m2" in missing
    assert "clausing_factor" in missing
    assert "calibration" in missing

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


def test_battery_score_script_runs_status_diff() -> None:
    src = Path("scripts/battery_score.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "status_diff_rows" in called


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


def test_non_thermo_quantities_refuse_without_invented_band() -> None:
    """Vapour / activity / alpha / yield have no sourced agreement band."""

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
        assert numeric is None
        assert reason is RefusalReason.DECISION_RULE_MISSING
        assert detail.get("reason") == f"no_sourced_decision_band:{quantity.value}"
        assert detail.get("quantity") == quantity.value

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
    assert residual.status is ResidualStatus.REFUSED
    assert residual.numeric is None
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.DECISION_RULE_MISSING
    assert residual.refusal.detail.get("reason") == "no_sourced_decision_band:p_sat"


def test_pyrolysis_yield_does_not_use_robinot_eleven_percent_floor() -> None:
    """n=2 same-rig O2 scatter is not a sourced yield agreement band."""

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
        assert numeric is None
        assert reason is RefusalReason.DECISION_RULE_MISSING
        assert detail.get("reason") == f"no_sourced_decision_band:{quantity.value}"
        assert "0.11" not in str(detail)
        assert "11" not in str(detail.get("reason") or "")


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
        assert numeric is None
        assert reason is RefusalReason.DECISION_RULE_MISSING
        assert detail.get("reason") == f"no_sourced_decision_band:{quantity.value}"
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

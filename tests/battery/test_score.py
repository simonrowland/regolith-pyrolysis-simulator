"""v2.1 scorer: score_eligible conjuncts, refusals, IMCC set, pins, determinism.

Expected values come from the schema contract, never from the code under test.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import math
import os
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
    IdentityEqualKind,
    MethodToken,
    MetricOperation,
    NoticeKind,
    PerBasis,
    Phase,
    Quantity,
    QUANTITY_UNITS,
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
from simulator.battery.identity import (
    Exposure,
    Identity,
    SweepIdentity,
    identity_equal,
    profile_for,
    quantity_token,
    validate_quantity_profile,
)
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


def test_retired_imcc_engines_are_excluded_from_score_set() -> None:
    assert Engine.OPENIMCC in SCORE_ENGINE_SET
    assert {engine.value for engine in SCORE_ENGINE_SET}.isdisjoint(
        {"imcc_sf04", "imcc_sf04_ext"}
    )
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


@pytest.mark.parametrize("name", ("imcc_sf04", "imcc_sf04_ext"))
def test_retired_imcc_engines_cannot_be_selected_for_scoring(name: str) -> None:
    from simulator.battery.score import engines_from_names

    with pytest.raises(ValueError, match="retired; use 'openimcc'"):
        engines_from_names((name,))


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


def _with_activity_reference_polymorph(
    identity: Identity, polymorph: str | None
) -> Identity:
    reference_state = identity.reference_state
    assert reference_state is not None and reference_state.is_value
    assert reference_state.value is not None
    endmember = reference_state.value.endmember
    polymorph_state = (
        State.unknown("solid-reference polymorph is not established")
        if polymorph is None
        else State.of(polymorph)
    )
    return replace(
        identity,
        reference_state=State.of(
            replace(
                reference_state.value,
                endmember=replace(endmember, polymorph=polymorph_state),
            )
        ),
    )


def _predict(value: Decimal, identity, **kwargs) -> EnginePrediction:
    quantity = quantity_token(identity)
    assert quantity is not None
    return EnginePrediction(
        engine=Engine.INTERNAL_ANALYTICAL,
        channel="internal-analytical",
        execution=Execution(state=ExecutionState.PRODUCED, call_evidence="test:predict"),
        value=value,
        unit=QUANTITY_UNITS[quantity],
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


def _uncalibrated_kems_partial_row(
    experiment, observation_id: str, pressure_Pa: Decimal
):
    identity = replace(
        _partial_identity(),
        total_pressure_Pa=State.of(Decimal("1e-6")),
    )
    observation = F.observation(
        observation_id,
        experiment.experiment_id,
        identity,
        pressure_Pa,
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="halwax_sergeev_mueller_schenk_2024",
    )
    return observation


def test_compile_residual_refuses_prediction_unit_mismatch() -> None:
    experiment = F.kems_experiment()
    reference = F.observation(
        "unit-mismatch-reference",
        experiment.experiment_id,
        _partial_identity(),
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="plante-1979",
    )
    prediction = replace(
        _partial_prediction(Engine.INTERNAL_ANALYTICAL, reference),
        unit="kJ_per_declared_mol_basis",
    )

    residual, candidate = _compile(reference, experiment, prediction)

    assert candidate is None
    assert residual.status is ResidualStatus.REFUSED
    assert residual.numeric is None
    assert residual.score_eligible is False
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.UNSUPPORTED
    assert residual.refusal.detail["reason"] == "engine_prediction_unit_mismatch"
    assert residual.refusal.detail["expected_unit"] == "Pa"
    assert residual.refusal.detail["prediction_unit"] == "kJ_per_declared_mol_basis"


def test_condensed_activity_axes_follow_composition_and_pressure() -> None:
    experiment = F.tabulation_experiment()
    ca_alumina = Composition(
        basis="ordered_complete_mole_inventory",
        components=(("CaO", Decimal("0.5")), ("Al2O3", Decimal("0.5"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )

    def ca_al_identity(oxygen: State, pressure: State):
        identity = F.activity_identity(
            formula="Al2O3",
            component_basis="Al2O3",
            composition=ca_alumina,
        )
        return replace(identity, fO2_Pa=oxygen, total_pressure_Pa=pressure)

    unknown_axes = ca_al_identity(
        State.unknown("not printed"), State.unknown("not printed")
    )
    reference = F.observation(
        "ca-al-activity-unknown-axes",
        experiment.experiment_id,
        unknown_axes,
        Decimal("0.2"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="work-1",
    )
    prediction = replace(
        _predict(Decimal("0.4"), unknown_axes), unit="dimensionless"
    )
    residual, _ = _compile(reference, experiment, prediction)
    assert residual.numeric is not None

    low_left = ca_al_identity(State.of(Decimal("1e-8")), State.of(Decimal("100000")))
    low_right = ca_al_identity(State.of(Decimal("1e-9")), State.of(Decimal("101325")))
    assert identity_equal(low_left, low_right).kind is IdentityEqualKind.EQUAL

    high_pressure = ca_al_identity(
        State.of(Decimal("1e-8")), State.of(Decimal("2000000"))
    )
    high_vs_unknown = identity_equal(
        high_pressure,
        ca_al_identity(State.of(Decimal("1e-8")), State.unknown("not printed")),
    )
    assert high_vs_unknown.kind is IdentityEqualKind.IDENTITY_UNKNOWN
    assert "total_pressure_Pa" in high_vs_unknown.fields

    feo_bearing = Composition(
        basis="ordered_complete_mole_inventory",
        components=(
            ("CaO", Decimal("0.4")),
            ("Al2O3", Decimal("0.5")),
            ("FeO", Decimal("0.1")),
        ),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    feo_unknown_oxygen = replace(
        unknown_axes,
        composition=State.of(feo_bearing),
    )
    feo_outcome = identity_equal(feo_unknown_oxygen, feo_unknown_oxygen)
    assert feo_outcome.kind is IdentityEqualKind.IDENTITY_UNKNOWN
    assert "fO2_Pa" in feo_outcome.fields

    unknown_composition = replace(
        unknown_axes,
        composition=State.unknown("no complete composition"),
    )
    unknown_composition_outcome = identity_equal(
        unknown_composition, unknown_composition
    )
    assert unknown_composition_outcome.kind is IdentityEqualKind.IDENTITY_UNKNOWN
    assert "fO2_Pa" in unknown_composition_outcome.fields

    partial_composition = Composition(
        basis="sample_catalog_proxy",
        components=(("CaO", Decimal("0.5")), ("Al2O3", Decimal("0.5"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
        proxy_flag="composition_from_sample_catalog",
    )
    partial_identity = replace(
        unknown_axes, composition=State.of(partial_composition)
    )
    partial_outcome = identity_equal(partial_identity, partial_identity)
    assert partial_outcome.kind is IdentityEqualKind.IDENTITY_UNKNOWN
    assert "fO2_Pa" in partial_outcome.fields

    vapour = replace(
        _partial_identity(),
        fO2_Pa=State.unknown("not printed"),
        total_pressure_Pa=State.unknown("not printed"),
    )
    vapour_outcome = identity_equal(vapour, vapour)
    assert vapour_outcome.kind is IdentityEqualKind.IDENTITY_UNKNOWN
    assert {"fO2_Pa", "total_pressure_Pa"}.issubset(vapour_outcome.fields)


@pytest.mark.parametrize("axis", ("reaction", "reference_state", "reservoir"))
def test_p_partial_optional_axes_do_not_block_identity_or_score(axis: str) -> None:
    experiment = F.kems_experiment()
    base = replace(
        _partial_identity(),
        fO2_Pa=State.of(Decimal("100000")),
        total_pressure_Pa=State.of(Decimal("100000")),
    )
    profile = profile_for(base)
    assert axis not in profile.required
    assert axis not in profile.permitted_not_applicable
    assert {
        "temperature_K",
        "composition",
        "fO2_Pa",
        "total_pressure_Pa",
    }.issubset(profile.required)
    assert len(base.composition.value.components) > 1

    missing_metadata = replace(base, **{axis: State.unknown("not printed")})
    assert validate_quantity_profile(missing_metadata).kind is IdentityEqualKind.EQUAL
    assert identity_equal(base, missing_metadata).kind is IdentityEqualKind.EQUAL

    reference = F.observation(
        f"p-partial-optional-{axis}",
        experiment.experiment_id,
        missing_metadata,
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="plante-1979",
    )
    residual, candidate = _compile(
        reference,
        experiment,
        _partial_prediction(Engine.INTERNAL_ANALYTICAL, reference),
    )
    assert candidate is not None
    assert residual.numeric is not None
    assert residual.refusal is None


def test_migrated_p_partial_optional_axes_are_retained_without_changing_score(
    tmp_path: Path,
) -> None:
    """Source-printed optional axes survive migration and do not affect scoring."""

    from simulator.battery.migrate import migrate
    from tests.battery.test_migrate import _copy_extract, _write_min_tree

    root = _write_min_tree(tmp_path)
    _copy_extract(root, "kems-042-plante-1979.yaml")
    _copy_extract(root, "stolyarova-1995-cao-alumina-kems.yaml")
    migrated = migrate(root, write=False)
    rows = tuple(migrated.observations.values())

    def first_partial_pressure(source_id: str):
        return next(
            observation
            for observation in rows
            if observation.source_id == source_id
            and quantity_token(observation.identity) is Quantity.P_PARTIAL
        )

    plante = first_partial_pressure("kems-042-plante-1979")
    stolyarova = first_partial_pressure("stolyarova-1995-cao-alumina-kems")
    assert all(
        state is not None and state.is_value
        for state in (
            plante.identity.reaction,
            plante.identity.reference_state,
            plante.identity.reservoir,
        )
    )
    assert all(
        state is not None and state.is_unknown
        for state in (
            stolyarova.identity.reaction,
            stolyarova.identity.reference_state,
            stolyarova.identity.reservoir,
        )
    )

    # Apply only the migrated metadata to a shared complete fixture so the
    # identity difference is what each source did or did not print. The real
    # Stolyarova observations remain model-derived and are not score-eligible.
    experiment = F.kems_experiment()
    base = replace(
        _partial_identity(),
        fO2_Pa=State.of(Decimal("100000")),
        total_pressure_Pa=State.of(Decimal("100000")),
    )
    plante_identity = replace(
        base,
        reaction=plante.identity.reaction,
        reference_state=plante.identity.reference_state,
        reservoir=plante.identity.reservoir,
    )
    stolyarova_identity = replace(
        base,
        reaction=stolyarova.identity.reaction,
        reference_state=stolyarova.identity.reference_state,
        reservoir=stolyarova.identity.reservoir,
    )
    assert (
        identity_equal(plante_identity, stolyarova_identity).kind
        is IdentityEqualKind.EQUAL
    )

    for source, identity in (
        ("plante", plante_identity),
        ("stolyarova-1995", stolyarova_identity),
    ):
        reference = F.observation(
            f"migrated-p-partial-{source}",
            experiment.experiment_id,
            identity,
            Decimal("1"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id=f"p-partial-{source}",
        )
        residual, candidate = _compile(
            reference,
            experiment,
            _partial_prediction(Engine.INTERNAL_ANALYTICAL, reference),
        )
        assert candidate is not None
        assert residual.numeric is not None
        assert residual.refusal is None


def test_p_partial_without_composition_still_refuses_identity() -> None:
    base = replace(
        _partial_identity(),
        fO2_Pa=State.of(Decimal("100000")),
        total_pressure_Pa=State.of(Decimal("100000")),
        composition=State.unknown("composition not printed"),
    )
    outcome = identity_equal(base, base)
    assert outcome.kind is IdentityEqualKind.IDENTITY_UNKNOWN
    assert "composition" in outcome.fields


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


@pytest.mark.parametrize(
    ("typed_not_printed", "expected_reason"),
    (
        (
            False,
            "calibration_not_grounded: No calibration entry has been recorded yet. "
            "(KEMS pressure requires a recorded calibration)",
        ),
        (
            True,
            "calibration_not_grounded: The source record says the calibration was "
            "not printed. (KEMS pressure requires a recorded calibration)",
        ),
    ),
)
def test_uncalibrated_kems_partial_pressure_scores_with_calibration_notice(
    typed_not_printed: bool,
    expected_reason: str,
) -> None:
    from simulator.battery.validity import run_validity_gates
    from simulator.battery.score import (
        FLAGGED_STRATUM_CALIBRATION_NOT_GROUNDED,
        flagged_strata,
        flagged_stratum_rows,
        render_score_report_from_payloads,
    )

    experiment = F.kems_experiment(kn=None, calibrated=False)
    if typed_not_printed:
        assert experiment.apparatus is not None
        experiment = replace(
            experiment,
            apparatus=replace(
                experiment.apparatus,
                calibration={
                    "standard": Located(
                        State.unknown("not printed"), locator=F.loc(page=271)
                    )
                },
            ),
        )
    reference = _uncalibrated_kems_partial_row(
        experiment, "uncalibrated-kems-pressure", Decimal("10")
    )
    gates = run_validity_gates(
        experiment, reference, point_observations=(reference,)
    )
    pressure_check = next(
        check for check in gates.checks if check.name == "in_cell_partial_pressure_sum"
    )
    assert gates.passed
    assert pressure_check.passed
    assert pressure_check.detail["flag"] == "calibration_not_grounded"
    assert pressure_check.detail["reason"] == (
        "calibration is not grounded for the in-cell fallback"
    )
    assert pressure_check.detail["calibration_record_status"] == (
        "not_printed" if typed_not_printed else "not_recorded"
    )
    residual, _ = compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=_context(F.work(), experiment, reference, review="reviewed"),
        prediction=_partial_prediction(Engine.INTERNAL_ANALYTICAL, reference),
    )

    assert residual.status is ResidualStatus.NO_BAND
    assert residual.numeric is not None
    assert residual.score_eligible is False
    notice = next(
        item for item in residual.notices
        if item.kind is NoticeKind.UNVERIFIED_APPARATUS
    )
    assert notice.reason == expected_reason
    assert flagged_strata(residual.notices) == (
        FLAGGED_STRATUM_CALIBRATION_NOT_GROUNDED,
    )

    diagnostic = flagged_stratum_rows(
        (residual,), engines=(Engine.INTERNAL_ANALYTICAL,)
    )
    assert diagnostic[0]["stratum"] == FLAGGED_STRATUM_CALIBRATION_NOT_GROUNDED
    assert diagnostic[0]["n"] == 1
    assert diagnostic[0]["median_dex"] is not None
    assert diagnostic[0]["rms_dex"] is not None

    payload = residual_to_plain(residual)
    summary_rows = flagged_stratum_payloads(
        (payload,), (Engine.INTERNAL_ANALYTICAL,)
    )
    assert summary_rows[0]["stratum"] == FLAGGED_STRATUM_CALIBRATION_NOT_GROUNDED
    assert summary_rows[0]["n"] == 1
    assert summary_rows[0]["median_dex"] is not None
    assert summary_rows[0]["rms_dex"] is not None
    report = render_score_report_from_payloads(
        (payload,), engines=(Engine.INTERNAL_ANALYTICAL,), hostname="test"
    )
    assert "calibration-not-grounded | vapour | internal-analytical | 1" in report


def test_uncalibrated_kems_residual_matches_between_oxygen_balance_engines() -> None:
    from simulator.battery.score import FLAGGED_STRATUM_CALIBRATION_NOT_GROUNDED
    from simulator.battery.score import flagged_strata

    experiment = F.kems_experiment(kn=None, calibrated=False)
    reference = _uncalibrated_kems_partial_row(
        experiment, "uncalibrated-kems-engine-parity", Decimal("10")
    )
    context = _context(F.work(), experiment, reference, review="reviewed")
    residuals = {
        engine: compile_residual(
            reference,
            engine,
            context=context,
            prediction=_partial_prediction(engine, reference),
        )[0]
        for engine in (Engine.OPENIMCC, Engine.INTERNAL_ANALYTICAL)
    }

    openimcc = residuals[Engine.OPENIMCC]
    internal_analytical = residuals[Engine.INTERNAL_ANALYTICAL]
    assert replace(
        openimcc,
        key=internal_analytical.key,
        candidate=internal_analytical.candidate,
    ) == internal_analytical
    assert openimcc.notices == internal_analytical.notices
    assert openimcc.numeric == internal_analytical.numeric
    assert openimcc.status is internal_analytical.status is ResidualStatus.NO_BAND
    assert openimcc.score_eligible is internal_analytical.score_eligible is False
    assert openimcc.numeric is not None
    assert openimcc.numeric.decision_band is None
    assert internal_analytical.numeric is not None
    assert internal_analytical.numeric.decision_band is None
    assert flagged_strata(openimcc.notices) == (
        FLAGGED_STRATUM_CALIBRATION_NOT_GROUNDED,
    )
    assert flagged_strata(internal_analytical.notices) == flagged_strata(
        openimcc.notices
    )


def test_uncalibrated_kems_partial_pressure_is_excluded_from_band_population() -> None:
    experiment = F.kems_experiment(kn=None, calibrated=False)
    first = _uncalibrated_kems_partial_row(
        experiment, "uncalibrated-kems-replicate-1", Decimal("10")
    )
    second = _uncalibrated_kems_partial_row(
        experiment, "uncalibrated-kems-replicate-2", Decimal("100")
    )
    printed_pressure_error = Uncertainty(
        kind=UncertaintyKind.PRINTED,
        verbatim="10% pressure error",
    )
    first = replace(first, uncertainty=printed_pressure_error)
    second = replace(second, uncertainty=printed_pressure_error)

    band = derive_kems_partial_pressure_band(
        {first.observation_id: first, second.observation_id: second},
        {experiment.experiment_id: experiment},
    )

    assert band is None


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


def test_reference_phase_convention_notice_is_reported_without_blocking_score() -> None:
    from simulator.battery.score import residual_to_plain

    notice = Notice(
        kind=NoticeKind.REFERENCE_PHASE_BY_CONVENTION,
        affected_quantities=(Quantity.DELTA_FG,),
        reason=(
            "JANAF web table prints no phase; ideal-gas reference state per the "
            "monograph convention, page not held"
        ),
        origin="O-029 (O) reference-element table",
        authority=Authority.CONVENTION,
        certification="fetch the JANAF 4th ed. printed page for the table",
    )
    experiment = F.tabulation_experiment()
    reference = F.observation(
        "janaf-reference-phase-convention",
        experiment.experiment_id,
        F.o2_identity(),
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="nist-janaf-4th",
        notices=(notice,),
    )
    prediction = _predict(Decimal("0"), reference.identity)
    residual, _ = _compile(reference, experiment, prediction)
    baseline = replace(
        reference,
        observation_id="janaf-reference-phase-baseline",
        notices=(),
    )
    baseline_residual, _ = _compile(
        baseline, experiment, _predict(Decimal("0"), baseline.identity)
    )
    assert residual.status is baseline_residual.status
    assert residual.score_eligible is baseline_residual.score_eligible
    assert residual.numeric is not None
    assert notice in residual.notices
    report_row = residual_to_plain(residual)
    assert any(
        row["kind"] == "reference_phase_by_convention"
        and row["authority"] == "convention"
        and row["certification"] == "fetch the JANAF 4th ed. printed page for the table"
        for row in report_row["notices"]
    )


def test_default_admission_notice_is_visible_without_changing_score() -> None:
    notice = Notice(
        kind=NoticeKind.ADMISSION_DEFAULTED,
        affected_quantities=(Quantity.DELTA_FG,),
        reason=(
            "admission_defaulted: no observation admission_status mapped from source; "
            "owner ruling d-056; not a reviewer decision"
        ),
        origin="janaf-defaulted-admission",
    )
    experiment = F.tabulation_experiment()
    reference = F.observation(
        "janaf-defaulted-admission",
        experiment.experiment_id,
        F.o2_identity(),
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="nist-janaf-4th",
        notices=(notice,),
    )
    residual, _ = _compile(
        reference, experiment, _predict(Decimal("0"), reference.identity)
    )
    baseline = replace(
        reference,
        observation_id="janaf-defaulted-admission-baseline",
        notices=(),
    )
    baseline_residual, _ = _compile(
        baseline, experiment, _predict(Decimal("0"), baseline.identity)
    )

    assert residual.status is baseline_residual.status
    assert residual.score_eligible is baseline_residual.score_eligible
    assert notice in residual.notices
    assert any(
        row["kind"] == "admission_defaulted"
        for row in residual_to_plain(residual)["notices"]
    )


def test_solved_effusion_lifts_only_derived_oxygen_provenance_blocker() -> None:
    experiment = replace(
        F.kems_experiment(),
        conditions={"temperature_K": F.located(Decimal("1156"))},
    )
    ident = replace(
        _partial_identity(),
        fO2_Pa=State.of(Decimal("1e-6")),
        total_pressure_Pa=State.of(Decimal("1e-6")),
    )
    derived = Notice(
        kind=NoticeKind.PRESSURE_PROVENANCE_UNKNOWN,
        affected_quantities=(Quantity.P_PARTIAL,),
        reason=(
            "fO2_Pa is a DERIVED condition, not a measurement: "
            "P_O2 derived from measured pK"
        ),
        origin="plante-row",
    )
    reference = F.observation(
        "plante-derived-pressure",
        experiment.experiment_id,
        ident,
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="work-1",
        notices=(derived,),
    )
    solved = Notice(
        kind=NoticeKind.SOURCE_DISAGREEMENT,
        affected_quantities=(Quantity.P_PARTIAL,),
        reason='fo2_oxygen_balance_effusion_solved: {"pO2_bar":0.12}',
        origin="engine:openimcc",
    )
    from simulator.battery.oxygen_balance import (
        IMCC_ENGINES,
        has_own_engine_solved_oxygen_balance,
    )

    assert IMCC_ENGINES == frozenset({Engine.OPENIMCC})
    assert has_own_engine_solved_oxygen_balance(Engine.OPENIMCC, (solved,))
    assert not has_own_engine_solved_oxygen_balance(Engine.ALPHAMELTS, (solved,))
    foreign_origin = replace(solved, origin="engine:imcc_sf04")
    assert not has_own_engine_solved_oxygen_balance(
        Engine.OPENIMCC, (foreign_origin,)
    )
    unsolved_prediction = replace(
        _partial_prediction(Engine.OPENIMCC, reference),
        notices=(),
    )
    unsolved, unsolved_candidate = compile_residual(
        reference,
        Engine.OPENIMCC,
        context=_context(F.work(), experiment, reference),
        prediction=unsolved_prediction,
        comparison_ids={reference.observation_id},
    )
    assert unsolved_candidate is not None
    assert unsolved.score_eligible is False
    assert "no_blocking_qualification" in unsolved.exclusions

    foreign_origin_prediction = replace(
        _partial_prediction(Engine.OPENIMCC, reference),
        notices=(foreign_origin,),
    )
    foreign_origin_residual, foreign_origin_candidate = compile_residual(
        reference,
        Engine.OPENIMCC,
        context=_context(F.work(), experiment, reference),
        prediction=foreign_origin_prediction,
        comparison_ids={reference.observation_id},
    )
    assert foreign_origin_candidate is not None
    assert foreign_origin_residual.score_eligible is False
    assert "no_blocking_qualification" in foreign_origin_residual.exclusions

    prediction = replace(
        _partial_prediction(Engine.OPENIMCC, reference),
        notices=(solved,),
    )
    residual, candidate = compile_residual(
        reference,
        Engine.OPENIMCC,
        context=_context(F.work(), experiment, reference),
        prediction=prediction,
        comparison_ids={reference.observation_id},
    )

    assert candidate is not None
    assert residual.score_eligible is True
    assert residual.exclusions == ()
    assert derived in residual.notices
    assert validate_corpus(
        [F.work()], [experiment], [reference, candidate], [residual]
    ).ok

    unrelated = replace(derived, reason="total pressure provenance unknown")
    reference_with_other_blocker = replace(
        reference,
        observation_id="plante-derived-pressure-with-other-blocker",
        notices=(derived, unrelated),
    )
    blocked, _ = compile_residual(
        reference_with_other_blocker,
        Engine.OPENIMCC,
        context=_context(F.work(), experiment, reference_with_other_blocker),
        prediction=prediction,
        comparison_ids={reference_with_other_blocker.observation_id},
    )
    assert blocked.score_eligible is False
    assert "no_blocking_qualification" in blocked.exclusions


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


@pytest.mark.parametrize(
    "quantity", (Quantity.ACTIVITY, Quantity.ACTIVITY_COEFFICIENT)
)
def test_published_typed_activity_unknown_method_scores_flagged_and_pressure_unknown_method_still_refuses(
    quantity: Quantity,
) -> None:
    from simulator.battery.score import (
        FLAGGED_STRATUM_UNVERIFIED_APPARATUS,
        flagged_strata,
    )

    experiment = _unknown_method_experiment()
    identity = replace(
        F.activity_identity(
            formula="CaO",
            T_K=Decimal("1823"),
            endmember_phase=Phase.L,
            component_basis="CaO",
        ),
        quantity=quantity,
    )
    activity = F.observation(
        "published-cao-activity-unknown-method",
        experiment.experiment_id,
        identity,
        Decimal("0.4"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="published-activity-work",
    )
    residual, _candidate = _compile(
        activity,
        experiment,
        _predict(Decimal("0.3"), identity),
        review="reviewed",
    )

    assert residual.status is not ResidualStatus.REFUSED
    assert residual.numeric is not None
    assert residual.score_eligible is False
    notice = next(
        item
        for item in residual.notices
        if item.kind is NoticeKind.UNVERIFIED_APPARATUS
    )
    assert "method_unknown" in notice.reason
    assert flagged_strata(residual.notices) == (
        FLAGGED_STRATUM_UNVERIFIED_APPARATUS,
    )

    pressure_identities = (
        (F.psat_identity("Na"), Decimal("0.1")),
        (_partial_identity(), Decimal("1")),
    )
    for index, (pressure_identity, value) in enumerate(pressure_identities):
        pressure = F.observation(
            f"unknown-method-pressure-{index}",
            experiment.experiment_id,
            pressure_identity,
            value,
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id="published-pressure-work",
        )
        refused, _candidate = _compile(
            pressure,
            experiment,
            _predict(value, pressure_identity),
            review="reviewed",
        )
        assert refused.status is ResidualStatus.REFUSED
        assert refused.refusal is not None
        assert refused.refusal.reason is RefusalReason.METHOD_UNKNOWN

    untyped_identity = replace(
        identity,
        reference_state=State.not_applicable("published reference state is absent"),
    )
    untyped_activity = F.observation(
        "published-cao-activity-unknown-method-untyped-reference",
        experiment.experiment_id,
        untyped_identity,
        Decimal("0.4"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="published-activity-work",
    )
    untyped_refused, _candidate = _compile(
        untyped_activity,
        experiment,
        _predict(Decimal("0.3"), untyped_identity),
        review="reviewed",
    )
    assert untyped_refused.status is ResidualStatus.REFUSED
    assert untyped_refused.refusal is not None
    assert untyped_refused.refusal.reason is RefusalReason.METHOD_UNKNOWN


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
    check = next(c for c in gate.checks if c.name == "kems_calibration")
    assert check.detail["missing"] == ["calibration"]
    assert "calibration" in check.detail["reason"]
    if quantity is Quantity.P_PARTIAL:
        assert gate.passed
        assert check.detail["flag"] == "calibration_not_grounded"
        assert check.detail["calibration_record_status"] == "not_recorded"
    else:
        assert gate.passed is False
        assert gate.reason is RefusalReason.UNDERDETERMINED_APPARATUS
        assert gate.primary_check == "kems_calibration"

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
    assert partial_gate.passed
    partial_check = next(
        check for check in partial_gate.checks if check.name == "kems_calibration"
    )
    assert partial_check.detail["flag"] == "calibration_not_grounded"


def _comparison_activity_provenance(pairing: str) -> dict[str, object]:
    return {
        "comparison_method": {"kind": "comparison_ratio"},
        "common_knudsen_cell_constant": {"cancels": True},
        "melt_reference_pairing": {"kind": pairing},
    }


def test_assumed_comparison_activity_scores_with_cancellation_notice() -> None:
    composition = Composition(
        "published_mole_fraction",
        (
            ("CaO", Decimal("0.335")),
            ("Al2O3", Decimal("0.335")),
            ("SiO2", Decimal("0.33")),
        ),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    identity = replace(
        F.activity_identity(formula="NaO0.5"),
        composition=State.unknown("composition is recorded on the source point"),
        fO2_Pa=State.unknown("fO2 is not printed"),
        total_pressure_Pa=State.unknown("total pressure is not printed"),
    )
    experiment = F.kems_experiment(
        orifice_area=None, clausing=None, kn=Decimal("20"), calibrated=True
    )
    reference = F.observation(
        "stolyarova-assumed-comparison-activity",
        experiment.experiment_id,
        identity,
        Decimal("0.2"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="stolyarova-1996-cao-alumina-silica-kems",
    )
    reference = replace(
        reference,
        provenance=_comparison_activity_provenance("same_effective_setup_assumed"),
        point_conditions={"composition": Located(State.of(composition))},
        notices=(
            Notice(
                kind=NoticeKind.COMPARISON_METHOD_CELL_CONSTANT_CANCELS,
                affected_quantities=(Quantity.ACTIVITY,),
                reason=(
                    "normalized comparison-method activity assumes cancellation "
                    "of the common Knudsen-cell constant (same instrument; "
                    "same-cell pairing not printed)"
                ),
                origin=reference.observation_id,
            ),
        ),
    )
    gates = run_validity_gates(experiment, reference)
    assert gates.passed
    geometry_check = next(
        check for check in gates.checks if check.name == "comparison_method_cell_constant_cancels"
    )
    assert geometry_check.detail["pairing"] == "same_effective_setup_assumed"
    assert geometry_check.detail["geometry"] == "not_required_for_normalized_activity"

    residual, _ = _compile(
        reference,
        experiment,
        _predict(Decimal("0.2"), identity),
        review="reviewed",
    )
    assert residual.status is not ResidualStatus.REFUSED
    assert residual.numeric is not None
    assert any(
        notice.kind is NoticeKind.COMPARISON_METHOD_CELL_CONSTANT_CANCELS
        and "assumes cancellation" in notice.reason
        and "same-cell pairing not printed" in notice.reason
        for notice in residual.notices
    )


def test_different_cell_comparison_activity_still_refuses_geometry() -> None:
    identity = F.activity_identity(formula="NaO0.5")
    experiment = F.kems_experiment(
        orifice_area=Decimal("3.14e-7"),
        clausing=Decimal("0.9"),
        kn=Decimal("20"),
        calibrated=True,
    )
    reference = replace(
        F.observation(
            "different-cell-comparison-activity",
            experiment.experiment_id,
            identity,
            Decimal("0.2"),
            evidence=EvidenceClass.MEASURED_DIRECT,
        ),
        provenance=_comparison_activity_provenance("different_cells_or_geometry"),
    )
    gate = underdetermined_apparatus(
        experiment, Quantity.ACTIVITY, observation=reference
    )
    assert gate.passed is False
    assert gate.reason is RefusalReason.UNDERDETERMINED_APPARATUS
    assert gate.primary_check == "geometry_determinants"
    geometry_check = next(
        check for check in gate.checks if check.name == "geometry_determinants"
    )
    assert "comparison_pairing_different_cells_or_geometry" in geometry_check.detail[
        "missing"
    ]

    residual, _ = _compile(
        reference,
        experiment,
        _predict(Decimal("0.2"), identity),
        review="reviewed",
    )
    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.UNDERDETERMINED_APPARATUS


@pytest.mark.parametrize(
    "quantity", (Quantity.ACTIVITY, Quantity.ACTIVITY_COEFFICIENT)
)
def test_uncalibrated_kems_activity_uses_calibration_not_grounded_stratum(
    quantity: Quantity,
) -> None:
    from simulator.battery.score import (
        FLAGGED_STRATUM_CALIBRATION_NOT_GROUNDED,
        flagged_strata,
    )

    identity = replace(
        F.activity_identity(formula="NaO0.5"), quantity=quantity
    )
    experiment = F.kems_experiment(
        orifice_area=None, clausing=None, kn=Decimal("20"), calibrated=False
    )
    reference = replace(
        F.observation(
            "uncalibrated-comparison-activity",
            experiment.experiment_id,
            identity,
            Decimal("0.2"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id="stolyarova-1996-cao-alumina-silica-kems",
        ),
        provenance=_comparison_activity_provenance(
            "same_effective_setup_assumed"
        ),
    )
    gates = run_validity_gates(experiment, reference)
    calibration_check = next(
        check for check in gates.checks if check.name == "kems_calibration"
    )
    assert gates.passed
    assert calibration_check.passed
    assert calibration_check.detail["flag"] == "calibration_not_grounded"

    residual, _ = _compile(
        reference,
        experiment,
        _predict(Decimal("0.2"), identity),
        review="reviewed",
    )
    assert residual.status is not ResidualStatus.REFUSED
    assert residual.numeric is not None
    assert residual.score_eligible is False
    notice = next(
        notice
        for notice in residual.notices
        if notice.kind is NoticeKind.UNVERIFIED_APPARATUS
    )
    assert notice.reason == (
        "calibration_not_grounded: No calibration entry has been recorded yet. "
        "(KEMS activity requires a recorded calibration)"
    )
    assert flagged_strata(residual.notices) == (
        FLAGGED_STRATUM_CALIBRATION_NOT_GROUNDED,
    )


def test_uncalibrated_kems_activity_pressure_sum_over_limit_still_refuses() -> None:
    identity = F.activity_identity(formula="NaO0.5")
    experiment = F.kems_experiment(
        orifice_area=None, clausing=None, kn=None, calibrated=False
    )
    activity = F.observation(
        "uncalibrated-over-limit-activity",
        experiment.experiment_id,
        identity,
        Decimal("0.2"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="stolyarova-1996-cao-alumina-silica-kems",
    )
    activity = replace(
        activity,
        provenance=_comparison_activity_provenance(
            "same_effective_setup_assumed"
        ),
    )
    partial_identity = replace(
        _partial_identity(),
        temperature_K=identity.temperature_K,
        fO2_Pa=identity.fO2_Pa,
        total_pressure_Pa=identity.total_pressure_Pa,
        composition=identity.composition,
    )
    sodium = F.observation(
        "over-limit-Na-pressure",
        experiment.experiment_id,
        partial_identity,
        Decimal("6"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="stolyarova-1996-cao-alumina-silica-kems",
    )
    silicon = F.observation(
        "over-limit-Si-pressure",
        experiment.experiment_id,
        replace(partial_identity, species=Species("Si", Phase.G)),
        Decimal("5"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="stolyarova-1996-cao-alumina-silica-kems",
    )
    gates = run_validity_gates(
        experiment,
        activity,
        point_observations=(activity, sodium, silicon),
    )
    assert gates.passed is False
    assert gates.reason is RefusalReason.EFFUSION_REGIME_UNVERIFIED
    pressure_check = next(
        check for check in gates.checks if check.name == "in_cell_partial_pressure_sum"
    )
    assert pressure_check.passed is False
    assert pressure_check.detail["printed_partial_pressure_sum_Pa"] == "11"

    context = _context(
        F.work(), experiment, activity, sodium, silicon, review="reviewed"
    )
    residual, _ = compile_residual(
        activity,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        comparison_ids={activity.observation_id, sodium.observation_id, silicon.observation_id},
        predict=lambda _engine, _reference, **_kwargs: _predict(
            Decimal("0.2"), identity
        ),
        point_observations=(activity, sodium, silicon),
    )
    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.EFFUSION_REGIME_UNVERIFIED


def test_unverified_kems_value_refuses_without_printed_in_cell_pressures() -> None:
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
    assert residual.status is ResidualStatus.REFUSED
    assert residual.numeric is None
    assert residual.score_eligible is False
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.EFFUSION_REGIME_UNVERIFIED
    assert residual.refusal.detail["primary_check"] == "in_cell_partial_pressure_sum"

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


def test_flagged_stratum_classifiers_agree_for_each_stratum() -> None:
    from simulator.battery.score import (
        FLAGGED_STRATUM_CATALOGUE_COMPOSITION,
        FLAGGED_STRATUM_IMCC_COMPLEX_SATURATION,
        FLAGGED_STRATUM_REFERENCE_CONVERTED_VIA_FUSION,
        FLAGGED_STRATUM_SOURCE_INTERNALLY_INCONSISTENT,
        FLAGGED_STRATUM_UNVERIFIED_APPARATUS,
        _flagged_payload_strata,
        _is_flagged_stratum_notice,
        flagged_strata,
    )

    cases = (
        (
            FLAGGED_STRATUM_UNVERIFIED_APPARATUS,
            NoticeKind.UNVERIFIED_APPARATUS,
            "apparatus_unverified:probe",
        ),
        (
            FLAGGED_STRATUM_CATALOGUE_COMPOSITION,
            NoticeKind.COMPOSITION_FROM_SAMPLE_CATALOG,
            "catalogue_composition:probe",
        ),
        (
            FLAGGED_STRATUM_SOURCE_INTERNALLY_INCONSISTENT,
            NoticeKind.SOURCE_DISAGREEMENT,
            "source_internally_inconsistent: probe",
        ),
        (
            FLAGGED_STRATUM_IMCC_COMPLEX_SATURATION,
            NoticeKind.IMCC_COMPLEX_SATURATION,
            "imcc_complex_saturation:probe",
        ),
        (
            FLAGGED_STRATUM_REFERENCE_CONVERTED_VIA_FUSION,
            NoticeKind.DERIVATION_USES_COMPILATION,
            "reference_converted_via_fusion;probe",
        ),
    )
    for stratum, kind, reason in cases:
        notice = Notice(
            kind=kind,
            affected_quantities=(Quantity.P_PARTIAL,),
            reason=reason,
            origin=f"probe:{stratum}",
        )
        payload = {"notices": [{"kind": kind.value, "reason": reason}]}

        assert flagged_strata((notice,)) == (stratum,)
        assert _is_flagged_stratum_notice(notice) is True
        assert _flagged_payload_strata(payload) == (stratum,)


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
    assert thermo.decision_band.rule.startswith("LEGACY fallback: gibbs_battery_residual_ledger.yaml")

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
    from simulator.battery.score import (
        headline_payload_records,
        headline_records,
        residual_to_plain,
    )

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
    derived_band = DecisionBand(
        Decimal("0.1"),
        "dimensionless",
        "test residual distribution: 2x median absolute deviation; derived_n=10",
    )
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
            decision_band=derived_band,
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
    stratum = by_tier["compilation"]["decision_strata"][0]
    assert stratum["band_kind"] == "derived_2xMAD"
    assert stratum["band_derived_n"] == 10
    assert stratum["tail_out_count"] == 1
    assert stratum["tail_in_count"] == 0
    assert "tail membership, not accuracy" in by_tier["compilation"]["match_rate_label"]
    payload_record = next(
        row
        for row in headline_payload_records(
            [residual_to_plain(compilation)],
            engines=(Engine.INTERNAL_ANALYTICAL,),
            observations=ctx.observations,
            origins=ctx.origins,
        )
        if row["tier"] == "compilation"
        and row["rail"] == Rail.VAPOUR.value
        and row["engine"] == Engine.INTERNAL_ANALYTICAL.value
    )
    payload_stratum = payload_record["decision_strata"][0]
    assert payload_stratum["band_kind"] == "derived_2xMAD"
    assert payload_stratum["tail_out_count"] == 1
    assert "tail membership, not accuracy" in payload_record["match_rate_label"]


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


def test_residue_composition_has_its_own_rail_and_typed_engine_refusal() -> None:
    from simulator.battery.score import (
        ENGINE_CHANNELS,
        metric_operation,
        predict_with_engine,
        rail_for_quantity,
    )

    identity = replace(
        F.psat_identity("Gd"),
        quantity=Quantity.RESIDUE_COMPONENT_COMPOSITION,
    )
    observation = F.observation(
        "gd-residue-composition",
        "sossi-residue-composition",
        identity,
        Decimal("759.6"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="kems-012-sossi-2019",
    )

    assert rail_for_quantity(Quantity.RESIDUE_COMPONENT_COMPOSITION) is Rail.RESIDUE_COMPOSITION
    assert metric_operation(Quantity.RESIDUE_COMPONENT_COMPOSITION) is MetricOperation.ABSOLUTE
    for engine in ENGINE_CHANNELS:
        prediction = predict_with_engine(engine, observation)
        assert prediction.execution.state is ExecutionState.UNSUPPORTED
        assert prediction.refusal_reason is RefusalReason.OUTSIDE_SUPPORTED_SPECIES
        assert prediction.refusal_detail["reason"] == "channel_missing"
        assert prediction.refusal_detail["quantity"] == "residue_component_composition"


def test_residue_metric_uses_dex_for_element_ppm_and_absolute_for_oxide() -> None:
    from simulator.battery.score import (
        metric_operation_for_identity,
        populate_numeric,
    )

    base = F.psat_identity("Mn")
    element = replace(
        base,
        quantity=Quantity.RESIDUE_COMPONENT_COMPOSITION,
        subtype=State.of("element_ppm_by_mass"),
    )
    oxide = replace(
        element,
        species=Species("FeO", Phase.L),
        subtype=State.of("oxide_wt_percent"),
    )

    assert metric_operation_for_identity(element) is MetricOperation.DEX
    assert metric_operation_for_identity(oxide) is MetricOperation.ABSOLUTE
    numeric, reason, _detail = populate_numeric(
        quantity=Quantity.RESIDUE_COMPONENT_COMPOSITION,
        candidate=Decimal("10"),
        reference=Decimal("5"),
        source_relation=SourceRelation.INDEPENDENT,
        operation_override=metric_operation_for_identity(element),
    )
    assert reason is None
    assert numeric is not None
    assert numeric.operation is MetricOperation.DEX
    assert numeric.value == Decimal("0.3010299956639812")


def test_hashimoto_scoring_projects_by_experiment_and_carries_residue_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from simulator.battery import residue, score

    context = load_score_context(sources=("kems-015-hashimoto-1983",))
    references = sorted(
        (
            observation
            for observation in context.observations.values()
            if observation.source_id == "kems-015-hashimoto-1983"
            and "::hashimoto_1983_table3_residue_composition_series::"
            in observation.observation_id
            and quantity_token(observation.identity)
            is Quantity.RESIDUE_COMPONENT_COMPOSITION
            and observation.identity.species.formula == "FeO"
        ),
        key=lambda observation: observation.experiment_id,
    )
    assert len(references) == 24
    refused_experiment: str | None = None

    def fake_cohort(experiments, _catalog, *, engine, **_kwargs):
        rows = []
        for index, experiment in enumerate(experiments):
            experiment_id = experiment["experiment_id"]
            provenance = {
                "experiment_id": experiment_id,
                "engine": engine,
                "oxygen_model": f"own oxygen balance: {engine}",
                "integration": {
                    "steps": 256,
                    "refinement_status": "unconverged_at_cap",
                    "refinement_notices": ({"reason": "residue_time_refinement_unconverged"},),
                },
                "geometry_refusal_by_geometry": (
                    {"sphere_shrinking": {"reason": "injected_refusal"}}
                    if experiment_id == refused_experiment
                    else {"sphere_shrinking": None}
                ),
            }
            values = {"FeO": float(index + 10), "MgO": 20.0, "SiO2": 30.0, "CaO": 4.0, "Al2O3": 5.0}
            rows.extend(
                (
                    SimpleNamespace(
                        experiment_id=experiment_id,
                        alpha_arm="alpha_common_unity_sensitivity",
                        primary_geometry_policy_id="sphere_shrinking",
                        primary_oxide_wt_pct=values,
                        sensitivity_band_wt_pct={name: (value - 1.0, value + 1.0) for name, value in values.items()},
                        provenance=provenance,
                    ),
                    SimpleNamespace(
                        experiment_id=experiment_id,
                        alpha_arm="alpha_runtime_catalog",
                        primary_geometry_policy_id="sphere_shrinking",
                        primary_oxide_wt_pct={**values, "FeO": values["FeO"] + 100.0},
                        geometry_oxide_wt_pct={"sphere_shrinking": values},
                        sensitivity_band_wt_pct={name: (value - 2.0, value + 2.0) for name, value in values.items()},
                        provenance={"engine": engine, "alpha_arm": "alpha_runtime_catalog"},
                    ),
                )
            )
        return tuple(rows)

    monkeypatch.setattr(residue, "_predict_hashimoto_residue_cohort", fake_cohort)
    for engine in (Engine.OPENIMCC, Engine.INTERNAL_ANALYTICAL):
        refused_experiment = None
        cache = {}
        first, second = references[0], references[-1]
        first_prediction = score._hashimoto_residue_prediction(context, first, engine, cache)
        second_prediction = score._hashimoto_residue_prediction(context, second, engine, cache)
        assert first_prediction.execution.state is ExecutionState.PRODUCED, first_prediction.refusal_detail
        assert second_prediction.execution.state is ExecutionState.PRODUCED
        assert first_prediction.value != second_prediction.value
        assert first_prediction.refusal_detail["experiment_id"] == first.experiment_id
        assert first_prediction.refusal_detail["consumed_row"]["observation_id"] == first.observation_id
        assert first_prediction.refusal_detail["production_alpha_arm"]["alpha_arm"] == "alpha_runtime_catalog"
        assert any("feo_melt_redox_stack2_pending" in notice.reason for notice in first_prediction.notices)
        assert any("residue_time_refinement_unconverged" in notice.reason for notice in first_prediction.notices)
        candidate = score.candidate_observation(first, first_prediction)
        assert candidate.identity.species.phase == first.identity.species.phase
        assert candidate.identity.exposure.value.area_m2 == first.identity.exposure.value.area_m2
        assert candidate.identity.exposure.value.area_m2.is_unknown
        assert identity_equal(candidate.identity, first.identity).kind is IdentityEqualKind.EQUAL
        wrong_phase_prediction = replace(
            first_prediction,
            identity=replace(
                first.identity,
                species=replace(first.identity.species, phase=State.of(Phase.GLASS)),
            ),
        )
        wrong_phase_candidate = score.candidate_observation(
            first, wrong_phase_prediction
        )
        assert (
            identity_equal(first.identity, wrong_phase_candidate.identity).kind
            is IdentityEqualKind.IDENTITY_MISMATCH
        )
        assert candidate.provenance["oxygen_model"] == f"own oxygen balance: {engine.value}"

        refused_experiment = first.experiment_id
        refusal = score._hashimoto_residue_prediction(context, first, engine, {})
        assert refusal.value is None
        assert refusal.refusal_reason is RefusalReason.UNSUPPORTED
        assert "injected_refusal" in refusal.refusal_detail["reason"]


def test_residue_area_is_not_identity_but_per_area_rate_still_requires_it() -> None:
    context = load_score_context(sources=("kems-015-hashimoto-1983",))
    residue_identity = next(
        observation.identity
        for observation in context.observations.values()
        if observation.source_id == "kems-015-hashimoto-1983"
        and "::hashimoto_1983_table3_residue_composition_series::"
        in observation.observation_id
        and quantity_token(observation.identity)
        is Quantity.RESIDUE_COMPONENT_COMPOSITION
    )
    assert identity_equal(residue_identity, residue_identity).kind is IdentityEqualKind.EQUAL

    rate_identity = replace(
        F.psat_identity("FeO"),
        quantity=Quantity.EVAPORATION_RATE,
        exposure=State.of(
            Exposure(
                area_m2=State.unknown("area required by rate"),
                duration_s=State.of(Decimal("1")),
            )
        ),
        per=State.unknown("test rate basis"),
        composition=State.unknown("test starting composition"),
        fO2_Pa=State.unknown("test oxygen condition"),
        total_pressure_Pa=State.unknown("test total pressure"),
        sweep_gas=State.unknown("test sweep gas"),
        sample_mass_kg=State.unknown("test sample mass"),
    )
    rate_outcome = identity_equal(rate_identity, rate_identity)
    assert rate_outcome.kind is IdentityEqualKind.IDENTITY_UNKNOWN
    assert "exposure.area_m2" in rate_outcome.fields


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


def test_legacy_gibbs_band_is_relation_agnostic_and_derived_band_wins() -> None:
    """Legacy ΔfG fallback ignores lineage; a supplied quantity band wins."""

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
    for relation in SourceRelation:
        band = decision_band_for(Quantity.DELTA_FG, relation)
        assert band is not None
        assert band.value == Decimal("1.0")
        assert band.unit == "kJ_per_declared_mol_basis"
        assert band.rule.startswith("LEGACY fallback:")
    family_band = DecisionBand(
        Decimal("0.25"), "J_per_declared_mol_basis_per_K", "family residuals"
    )
    assert (
        decision_band_for(
            Quantity.CP, SourceRelation.UNKNOWN, derived_band=family_band
        )
        == family_band
    )
    numeric, reason, _detail = populate_numeric(
        quantity=Quantity.DELTA_FG,
        candidate=Decimal("1"),
        reference=Decimal("1"),
        source_relation=SourceRelation.INDEPENDENT,
    )
    assert reason is None
    assert numeric is not None
    assert numeric.decision_band.unit == "kJ_per_declared_mol_basis"


def test_printed_cell_uncertainty_and_family_mad_are_quantity_scaled() -> None:
    from types import SimpleNamespace

    from simulator.battery.enums import QUANTITY_UNITS
    from simulator.battery.score import (
        _printed_uncertainty_band,
        _residual_distribution_band,
    )

    def source(quantity: Quantity, unit: str):
        return SimpleNamespace(
            locator=SimpleNamespace(note=f"printed unit='{unit}'"),
            derivation=SimpleNamespace(output_unit=QUANTITY_UNITS[quantity]),
        )

    printed = _printed_uncertainty_band(
        Quantity.CP,
        Uncertainty(kind=UncertaintyKind.PRINTED, verbatim="±0.24"),
        Decimal("300"),
        source_observation=source(Quantity.CP, "J/mol·K"),
    )
    assert printed == DecisionBand(
        Decimal("0.24"),
        "J_per_declared_mol_basis_per_K",
        "source-printed per-cell uncertainty",
    )
    relative_printed = _printed_uncertainty_band(
        Quantity.O2_YIELD,
        Uncertainty(kind=UncertaintyKind.PRINTED, verbatim="2%"),
        Decimal("0.5"),
    )
    assert relative_printed is not None
    assert relative_printed.value == Decimal("0.02")
    assert relative_printed.unit == "dimensionless"

    kcal_printed = _printed_uncertainty_band(
        Quantity.DELTA_FG,
        Uncertainty(kind=UncertaintyKind.PRINTED, verbatim="0.137"),
        Decimal("-1"),
        source_observation=source(Quantity.DELTA_FG, "kcal gfw^-1"),
    )
    assert kcal_printed is not None
    assert kcal_printed.value == Decimal("0.573208")

    joule_printed = _printed_uncertainty_band(
        Quantity.DELTA_FG,
        Uncertainty(kind=UncertaintyKind.PRINTED, verbatim="100"),
        Decimal("77.077"),
        source_observation=source(Quantity.DELTA_FG, "J/mol"),
    )
    assert joule_printed is not None
    assert joule_printed.value == Decimal("0.100")

    parenthetical_printed = _printed_uncertainty_band(
        Quantity.DELTA_FG,
        Uncertainty(kind=UncertaintyKind.PRINTED, verbatim="(0.389)"),
        Decimal("-2095.071"),
        source_observation=source(Quantity.DELTA_FG, "kJ/mol"),
    )
    assert parenthetical_printed is not None
    assert parenthetical_printed.value == Decimal("0.389")

    log_k_printed = _printed_uncertainty_band(
        Quantity.LOG10_KF,
        Uncertainty(kind=UncertaintyKind.PRINTED, verbatim="(0.068)"),
        Decimal("109.436"),
        source_observation=source(Quantity.LOG10_KF, "dimensionless"),
    )
    assert log_k_printed is not None
    assert log_k_printed.value == Decimal("0.068")

    family = _residual_distribution_band(
        [Decimal(value) for value in ("-10", "-1", "0", "1", "10") * 2],
        unit="J_per_declared_mol_basis_per_K",
        family="USGS",
        quantity=Quantity.CP,
    )
    assert family is not None
    assert family.value == Decimal("2")
    assert family.unit == "J_per_declared_mol_basis_per_K"
    assert "2x median absolute deviation" in family.rule
    assert "derived_n=10" in family.rule
    assert _residual_distribution_band(
        [Decimal(value) for value in ("-10", "-1", "0", "1", "10")],
        unit="J_per_declared_mol_basis_per_K",
        family="USGS",
        quantity=Quantity.CP,
    ) is None


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
    assert failures[0]["reason"] == "no_live_residual_for_reference"


def _pin_test_record(channel: str) -> PinBandRecord:
    return PinBandRecord(
        key=f"pin-ref::delta_fG::thermochemistry::{channel}",
        expected_outcome=ResidualStatus.MATCH.value,
        evidence="test",
        centre=Decimal("1"),
        metric_operation=MetricOperation.ABSOLUTE.value,
        metric_unit="kJ_per_declared_mol_basis",
        pin_band_value=Decimal("0.05"),
        pin_band_unit="kJ_per_declared_mol_basis",
    )


def _pin_test_residual(value: Decimal, *, rail: Rail = Rail.THERMOCHEMISTRY):
    return F.residual(
        key=f"pin-ref::delta_fG::{rail.value}::internal-analytical",
        reference="pin-ref",
        status=ResidualStatus.MATCH,
        rail=rail,
        numeric=ResidualNumeric(
            operation=MetricOperation.ABSOLUTE,
            unit="kJ_per_declared_mol_basis",
            value=value,
            decision_band=None,
        ),
    )


def test_pin_failures_matches_legacy_channel_by_identity() -> None:
    failures = pin_failures(
        [_pin_test_residual(Decimal("1.05"))],
        [_pin_test_record("ellingham")],
    )

    assert failures == []


def test_pin_and_legacy_bucket_share_residual_status_tokens() -> None:
    from simulator.battery.enums import residual_status_token
    from simulator.battery.pins import _pin_live_comparison
    from simulator.battery.score import _legacy_bucket

    assert residual_status_token("typed-refusal") is ResidualStatus.REFUSED
    assert residual_status_token("typed_refusal") is ResidualStatus.REFUSED
    assert residual_status_token("unknown") is None
    assert _legacy_bucket({"status": "typed-refusal"}) == "refused"
    assert _legacy_bucket({"status": "failed-to-run"}) == "refused"
    assert _legacy_bucket({"status": "unknown"}) == "excluded"
    live = _pin_live_comparison(
        {
            "reference_id": "reference",
            "comparison_key": "janaf::record:T=1100::nasa_cea_9::delta_fG_kJ_mol",
            "quantity": "delta_fG",
            "comparison_channel": "nasa_cea_9",
            "status": "typed_refusal",
        }
    )
    assert live is not None and live.status is ResidualStatus.REFUSED


def test_pin_payloads_match_legacy_channel_by_identity() -> None:
    from simulator.battery.pins import _pin_failures_from_payloads

    comparisons: list[dict[str, str]] = []
    failures = _pin_failures_from_payloads(
        [
            {
                "key": "pin-ref::delta_fG::thermochemistry::internal-analytical",
                "reference": "pin-ref",
                "status": ResidualStatus.MATCH.value,
                "numeric": {"value": "1.01"},
                "source_relation": "derived",
                "execution": {"call_evidence": "fixture-call"},
            }
        ],
        [_pin_test_record("ellingham")],
        comparisons=comparisons,
    )

    assert failures == []
    assert comparisons == [
        {
            "key": "pin-ref::delta_fG::thermochemistry::ellingham",
            "centre": "1",
            "pin_band": "0.05",
            "live": "1.01",
            "source": "internal-analytical",
            "source_relation": "derived",
            "call_evidence": "fixture-call",
        }
    ]


def test_pin_failures_deduplicate_identical_pin_keys() -> None:
    pin = _pin_test_record("internal-analytical")
    pin = replace(pin, aliases=(pin.key,), old_key=pin.key)

    failures = pin_failures([_pin_test_residual(Decimal("1.01"))], [pin])

    assert failures == []


def test_compilation_pin_join_uses_sidecar_not_engine_residuals() -> None:
    comparisons: list[dict[str, str]] = []
    failures = pin_failures(
        [
            {
                "key": "pin-ref::delta_fG::thermochemistry::internal-analytical",
                "reference": "pin-ref",
                "status": ResidualStatus.MATCH.value,
                "numeric": {"value": "100"},
            }
        ],
        [_pin_test_record("nasa_cea_9")],
        compilation_comparisons=[
            {
                "reference_id": "pin-ref",
                "quantity": "delta_fG",
                "comparison_channel": "nasa_cea_9",
                "comparison_key": "janaf::record:T=1100::nasa_cea_9::delta_fG_kJ_mol",
                "status": ResidualStatus.MATCH.value,
                "value": "1.03",
                "band": "0.05",
            }
        ],
        comparisons=comparisons,
    )

    assert failures == []
    assert comparisons[0]["live"] == "1.03"
    assert comparisons[0]["source"] == "nasa_cea_9"
    assert comparisons[0]["within_pin_band"] == "true"


def test_pin_failures_reports_unmapped_channel() -> None:
    from simulator.battery.pins import _pin_failures_from_payloads

    failures = pin_failures(
        [_pin_test_residual(Decimal("1"))],
        [_pin_test_record("nasa_cea_vs_ellingham")],
    )

    assert failures[0]["reason"] == "unmapped_pin_channel"
    assert failures[0]["channel"] == "nasa_cea_vs_ellingham"

    absent_unmapped_failures = pin_failures(
        [], [_pin_test_record("nasa_cea_vs_ellingham")]
    )
    assert absent_unmapped_failures[0]["reason"] == "unmapped_pin_channel"

    exact_key_failures = _pin_failures_from_payloads(
        [
            {
                "key": "pin-ref::delta_fG::thermochemistry::nasa_cea_vs_ellingham",
                "reference": "pin-ref",
                "status": ResidualStatus.MATCH.value,
                "numeric": {"value": "1"},
            }
        ],
        [_pin_test_record("nasa_cea_vs_ellingham")],
    )
    assert exact_key_failures[0]["reason"] == "unmapped_pin_channel"


def test_pin_failures_reports_ambiguous_matches() -> None:
    sidecar_rows = [
        {
            "reference_id": "pin-ref",
            "quantity": "delta_fG",
            "comparison_channel": "ellingham",
            "comparison_key": f"janaf::record-{index}:T=1100::ellingham::delta_fG_kJ_per_mol_O2",
            "status": ResidualStatus.MATCH.value,
            "value": "1",
            "band": "0.05",
        }
        for index in (1, 2)
    ]
    failures = pin_failures(
        [],
        [_pin_test_record("ellingham")],
        compilation_comparisons=sidecar_rows,
    )

    assert failures[0]["reason"] == "ambiguous_compilation_comparison"
    assert len(failures[0]["candidate_keys"]) == 2


def test_pin_failures_rejects_outside_band_live_value() -> None:
    failures = pin_failures(
        [_pin_test_residual(Decimal("1.051"))],
        [_pin_test_record("ellingham")],
    )

    assert failures[0]["reason"] == "outside_pin_band"
    assert failures[0]["source"] == "internal-analytical"


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


def _streaming_score_fixture():
    from simulator.battery.records import Residual, ResidualRefusal
    from simulator.battery.score import rail_for_quantity

    work = F.work()
    experiment = F.tabulation_experiment(work_id=work.work_id)
    engine = Engine.INTERNAL_ANALYTICAL
    thermo_rail = rail_for_quantity(Quantity.DELTA_FG, species_formula="O2")
    unit = QUANTITY_UNITS[Quantity.DELTA_FG]
    observations = []
    origins: dict[str, str] = {}
    residuals: dict[str, Residual] = {}
    candidates: dict[str, object] = {}

    def add_residual(
        reference,
        *,
        rail,
        status,
        numeric=None,
        refusal=None,
        candidate_id=None,
        eligible=False,
    ):
        residual = Residual(
            key=f"fixture::{reference.observation_id}::{engine.value}",
            reference=reference.observation_id,
            execution=Execution(state=ExecutionState.NOT_PROBED),
            rail=rail,
            status=status,
            source_relation=SourceRelation.UNKNOWN,
            score_eligible=eligible,
            exclusions=(),
            notices=(),
            candidate=candidate_id,
            numeric=numeric,
            refusal=refusal,
        )
        residuals[reference.observation_id] = residual

    measured = F.observation(
        "fixture-measured-vapour",
        experiment.experiment_id,
        F.psat_identity("Na"),
        Decimal("0.5"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id=work.work_id,
    )
    candidate = F.observation(
        "fixture-vapour-candidate",
        experiment.experiment_id,
        F.psat_identity("Na"),
        Decimal("0.6"),
        evidence=EvidenceClass.MODEL_DERIVED,
        source_id=work.work_id,
    )
    observations.append(measured)
    measured_numeric = ResidualNumeric(
        MetricOperation.DEX,
        "dimensionless",
        Decimal("0.5"),
        DecisionBand(Decimal("1"), "dimensionless", "measured fixture band"),
    )
    add_residual(
        measured,
        rail=Rail.VAPOUR,
        status=ResidualStatus.MATCH,
        numeric=measured_numeric,
        candidate_id=candidate.observation_id,
        eligible=True,
    )
    candidates[candidate.observation_id] = candidate

    refusal_reference = F.observation(
        "fixture-measured-refusal",
        experiment.experiment_id,
        F.psat_identity("Mg"),
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id=work.work_id,
    )
    observations.append(refusal_reference)
    add_residual(
        refusal_reference,
        rail=Rail.VAPOUR,
        status=ResidualStatus.REFUSED,
        refusal=ResidualRefusal(
            RefusalReason.UNSUPPORTED,
            {"reason": "fixture_unavailable"},
        ),
    )

    derived_values = ("0", "0", "0", "1", "1", "2", "2", "3", "4", "50")
    for index, raw_value in enumerate(derived_values):
        reference = F.observation(
            f"fixture-derived-{index:02d}",
            experiment.experiment_id,
            F.o2_identity(),
            Decimal(raw_value),
            evidence=EvidenceClass.COMPILATION_ASSESSED,
            admission=AdmissionStatus.PENDING,
            source_id="nist-janaf-4th",
        )
        observations.append(reference)
        origins[reference.observation_id] = (
            f"compilations-janaf/{reference.observation_id}.yaml"
        )
        add_residual(
            reference,
            rail=thermo_rail,
            status=ResidualStatus.NO_BAND,
            numeric=ResidualNumeric(
                MetricOperation.ABSOLUTE,
                unit,
                Decimal(raw_value),
                None,
            ),
        )

    no_band_reference = F.observation(
        "fixture-no-band",
        experiment.experiment_id,
        F.o2_identity(),
        Decimal("7"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        admission=AdmissionStatus.PENDING,
        source_id="other-compilation",
    )
    observations.append(no_band_reference)
    origins[no_band_reference.observation_id] = "compilations-other/no-band.yaml"
    add_residual(
        no_band_reference,
        rail=thermo_rail,
        status=ResidualStatus.NO_BAND,
        numeric=ResidualNumeric(
            MetricOperation.ABSOLUTE,
            unit,
            Decimal("7"),
            None,
        ),
    )

    printed_reference = F.observation(
        "fixture-printed",
        experiment.experiment_id,
        F.o2_identity(),
        Decimal("1"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        admission=AdmissionStatus.PENDING,
        source_id="nist-janaf-4th",
    )
    observations.append(printed_reference)
    origins[printed_reference.observation_id] = "compilations-janaf/printed.yaml"
    add_residual(
        printed_reference,
        rail=thermo_rail,
        status=ResidualStatus.MATCH,
        numeric=ResidualNumeric(
            MetricOperation.ABSOLUTE,
            unit,
            Decimal("1"),
            DecisionBand(
                Decimal("2"),
                unit,
                "source-printed per-cell uncertainty",
            ),
        ),
    )
    non_dex_dimensionless_reference = F.observation(
        "fixture-non-dex-dimensionless-band",
        experiment.experiment_id,
        F.o2_identity(),
        Decimal("3"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        admission=AdmissionStatus.PENDING,
        source_id="nist-janaf-4th",
    )
    observations.append(non_dex_dimensionless_reference)
    origins[non_dex_dimensionless_reference.observation_id] = (
        "compilations-janaf/fixture-log10-kf.yaml"
    )
    add_residual(
        non_dex_dimensionless_reference,
        rail=thermo_rail,
        status=ResidualStatus.MATCH,
        numeric=ResidualNumeric(
            MetricOperation.ABSOLUTE,
            "dimensionless",
            Decimal("3"),
            DecisionBand(
                Decimal("0.231"),
                "dimensionless",
                "source-printed per-cell uncertainty",
            ),
        ),
    )
    for reference_id, raw_value in (
        ("fixture-scaled-max-first", "0.240"),
        ("fixture-scaled-max-second", "-0.24"),
    ):
        reference = F.observation(
            reference_id,
            experiment.experiment_id,
            replace(F.o2_identity(), quantity=Quantity.S),
            Decimal(raw_value),
            evidence=EvidenceClass.COMPILATION_ASSESSED,
            admission=AdmissionStatus.PENDING,
            source_id="nist-janaf-4th",
        )
        observations.append(reference)
        origins[reference.observation_id] = "compilations-janaf/scale-check.yaml"
        add_residual(
            reference,
            rail=Rail.THERMOCHEMISTRY,
            status=ResidualStatus.NO_BAND,
            numeric=ResidualNumeric(
                MetricOperation.ABSOLUTE,
                "J_per_declared_mol_basis_per_K",
                Decimal(raw_value),
                None,
            ),
        )
    context = replace(_context(work, experiment, *observations), origins=origins)
    return context, engine, residuals, candidates


def test_streamed_scoring_is_byte_identical_to_legacy_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.battery.score as score_mod
    from simulator.battery.pins import _pin_failures_from_payloads

    context, engine, fixture_residuals, fixture_candidates = _streaming_score_fixture()
    monkeypatch.setattr(
        score_mod,
        "compile_residual",
        lambda reference, engine, **_kwargs: (
            fixture_residuals[reference.observation_id],
            fixture_candidates.get(
                fixture_residuals[reference.observation_id].candidate or ""
            ),
        ),
    )
    engines = (engine,)
    legacy_residuals, candidates = score_mod.score_store(context, engines=engines)
    legacy_path = tmp_path / "legacy-residuals.jsonl"
    score_mod.write_residuals_jsonl(
        legacy_residuals,
        candidates,
        legacy_path,
        root=score_mod.REPO_ROOT,
    )
    streamed_path = tmp_path / "residuals.jsonl"
    written, metadata = score_mod._score_store_to_jsonl(
        context,
        streamed_path,
        root=score_mod.REPO_ROOT,
        engines=engines,
    )
    partial_path = tmp_path / "residuals.jsonl.partial"
    assert written == len(legacy_residuals)
    assert partial_path.read_bytes() == legacy_path.read_bytes()

    payloads = score_mod._ResidualJsonlRows(
        partial_path,
        metadata=metadata,
    )
    pin = PinBandRecord(
        key=next(r.key for r in legacy_residuals if r.reference == "fixture-measured-vapour"),
        expected_outcome=ResidualStatus.MATCH.value,
        evidence="fixture",
        centre=Decimal("0"),
        pin_band_value=Decimal("0.1"),
    )
    old_pin_failures = pin_failures(legacy_residuals, (pin,))
    assert old_pin_failures[0]["reason"] == "outside_pin_band"
    assert _pin_failures_from_payloads(payloads, (pin,)) == old_pin_failures

    legacy_summary = tmp_path / "legacy-summary.json"
    streamed_summary = tmp_path / "streamed-summary.json"
    score_mod.write_headline_summary_json(
        legacy_residuals,
        legacy_summary,
        context=context,
        engines=engines,
        root=score_mod.REPO_ROOT,
    )
    stamp = score_mod.derive_store_stamp(score_mod.REPO_ROOT)
    assert metadata.aggregate is not None
    score_mod._write_headline_summary_from_accumulator_json(
        metadata.aggregate,
        streamed_summary,
        store_stamp=stamp,
    )
    assert streamed_summary.read_bytes() == legacy_summary.read_bytes()
    import json

    summary = json.loads(streamed_summary.read_text(encoding="utf-8"))
    compilation = next(
        row
        for row in summary["records"]
        if row["tier"] == "compilation"
        and row["rail"] == "thermochemistry"
        and row["engine"] == engine.value
    )
    assert compilation["band_width_dex"] is None
    scale_stratum = next(
        stratum
        for row in summary["records"]
        for stratum in row.get("decision_strata", [])
        if stratum["quantity"] == Quantity.S.value
    )
    assert scale_stratum["max_abs_residual"] == "0.240"

    old_report = score_mod.render_score_report(
        legacy_residuals,
        context=context,
        engines=engines,
        pin_failures=old_pin_failures,
        root=score_mod.REPO_ROOT,
    )
    # Parity is the claim: the streamed render must equal the legacy render
    # byte for byte. The legacy render itself legitimately changes with every
    # store landing, so its absolute hash is not pinned here.
    streamed_report = score_mod._render_score_report_from_payloads_legacy(
        payloads,
        context=context,
        engines=engines,
        pin_failures=old_pin_failures,
        store_stamp=stamp,
        _aggregate=metadata.aggregate,
    )
    assert streamed_report.encode("utf-8") == old_report.encode("utf-8")

    rows = score_mod.load_residuals_jsonl(partial_path)
    assert any(
        row.get("rail") == Rail.VAPOUR.value
        and isinstance(row.get("numeric"), dict)
        and row["numeric"].get("decision_band")
        for row in rows
    )
    assert any(
        isinstance(row.get("numeric"), dict)
        and isinstance(row["numeric"].get("decision_band"), dict)
        and row["numeric"]["decision_band"].get("rule")
        == "source-printed per-cell uncertainty"
        for row in rows
    )
    assert any(
        isinstance(row.get("numeric"), dict)
        and isinstance(row["numeric"].get("decision_band"), dict)
        and "residual distribution" in row["numeric"]["decision_band"].get("rule", "")
        for row in rows
    )
    assert any(row.get("status") == ResidualStatus.NO_BAND.value for row in rows)
    assert any(row.get("status") == ResidualStatus.REFUSED.value for row in rows)


def test_streamed_unrailed_measured_tier_matches_legacy_zero_grid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.battery.score as score_mod

    context, engine, fixture_residuals, _fixture_candidates = _streaming_score_fixture()
    measured_ids = {"fixture-measured-vapour"}
    context = replace(
        context,
        observations={
            key: observation
            for key, observation in context.observations.items()
            if key not in measured_ids
        },
        origins={
            key: origin for key, origin in context.origins.items() if key not in measured_ids
        },
    )
    fixture_residuals = {
        key: residual
        for key, residual in fixture_residuals.items()
        if key not in measured_ids
    }
    fixture_residuals["fixture-measured-refusal"] = replace(
        fixture_residuals["fixture-measured-refusal"], rail=None
    )
    monkeypatch.setattr(
        score_mod,
        "compile_residual",
        lambda reference, engine, **_kwargs: (
            fixture_residuals[reference.observation_id],
            None,
        ),
    )
    engines = (engine,)
    legacy_residuals, candidates = score_mod.score_store(context, engines=engines)
    path = tmp_path / "residuals.jsonl"
    _written, metadata = score_mod._score_store_to_jsonl(
        context,
        path,
        root=score_mod.REPO_ROOT,
        engines=engines,
    )
    stamp = score_mod.derive_store_stamp(score_mod.REPO_ROOT)
    legacy_report = score_mod.render_score_report(
        legacy_residuals,
        context=context,
        engines=engines,
        root=score_mod.REPO_ROOT,
    )
    assert metadata.aggregate is not None
    streamed_report = score_mod._render_score_report_from_payloads_legacy(
        score_mod._ResidualJsonlRows(path.with_name(path.name + ".partial"), metadata=metadata),
        context=context,
        engines=engines,
        store_stamp=stamp,
        _aggregate=metadata.aggregate,
    )
    assert streamed_report == legacy_report
    from simulator.battery.score import SCORE_ENGINE_SET

    for rail in Rail:
        for report_engine in SCORE_ENGINE_SET:
            assert f"| {rail.value} | {report_engine.value} | 0 |" in streamed_report


def test_streamed_measured_grid_omits_selected_engine_without_rail_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.battery.score as score_mod

    context, engine, fixture_residuals, fixture_candidates = _streaming_score_fixture()
    monkeypatch.setattr(
        score_mod,
        "compile_residual",
        lambda reference, _engine, **_kwargs: (
            fixture_residuals[reference.observation_id],
            fixture_candidates.get(
                fixture_residuals[reference.observation_id].candidate or ""
            ),
        ),
    )
    engines = (engine, Engine.VAPOROCK)
    legacy_residuals, _candidates = score_mod.score_store(context, engines=(engine,))
    path = tmp_path / "residuals.jsonl"
    _written, metadata = score_mod._score_store_to_jsonl(
        context,
        path,
        root=score_mod.REPO_ROOT,
        engines=(engine,),
    )
    stamp = score_mod.derive_store_stamp(score_mod.REPO_ROOT)
    legacy_report = score_mod.render_score_report(
        legacy_residuals,
        context=context,
        engines=engines,
        root=score_mod.REPO_ROOT,
    )
    assert metadata.aggregate is not None
    streamed_report = score_mod._render_score_report_from_payloads_legacy(
        score_mod._ResidualJsonlRows(path.with_name(path.name + ".partial"), metadata=metadata),
        context=context,
        engines=engines,
        store_stamp=stamp,
        _aggregate=metadata.aggregate,
    )
    measured_table = streamed_report.split("## Measured tier", 1)[1].split(
        "## Flagged strata", 1
    )[0]
    assert f"| {Rail.VAPOUR.value} | {Engine.VAPOROCK.value} |" not in measured_table
    assert streamed_report == legacy_report


def test_streaming_interruption_preserves_previous_complete_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.battery.score as score_mod
    from simulator.battery.records import Residual

    work = F.work()
    experiment = F.tabulation_experiment(work_id=work.work_id)
    references = tuple(
        F.observation(
            f"fixture-partial-{index}",
            experiment.experiment_id,
            F.psat_identity("Na"),
            Decimal("1"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id=work.work_id,
        )
        for index in range(3)
    )
    context = _context(work, experiment, *references)
    emitted: list[str] = []
    calls = 0

    def interrupt_after_two(reference, engine, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("injected scorer stop")
        residual = Residual(
            key=f"fixture::{reference.observation_id}::{engine.value}",
            reference=reference.observation_id,
            execution=Execution(state=ExecutionState.NOT_PROBED),
            rail=Rail.VAPOUR,
            status=ResidualStatus.NO_BAND,
            source_relation=SourceRelation.UNKNOWN,
            score_eligible=False,
            exclusions=(),
            notices=(),
        )
        emitted.append(residual.key)
        return residual, None

    monkeypatch.setattr(score_mod, "compile_residual", interrupt_after_two)
    target = tmp_path / "residuals.jsonl"
    previous = b"previous complete result\n"
    target.write_bytes(previous)
    with pytest.raises(RuntimeError, match="injected scorer stop"):
        score_mod._score_store_to_jsonl(
            context,
            target,
            root=score_mod.REPO_ROOT,
            engines=(Engine.INTERNAL_ANALYTICAL,),
            include_diagnostics=False,
        )
    partial = tmp_path / "residuals.jsonl.partial"
    assert partial.is_file()
    assert target.read_bytes() == previous
    rows = score_mod.load_residuals_jsonl(partial)
    assert [str(row["key"]) for row in rows] == emitted


@pytest.mark.parametrize("failure_stage", ("scoring", "summary", "report"))
def test_battery_failed_rerun_preserves_published_output_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    import simulator.battery.score as score_mod
    from scripts import battery_score
    from simulator.battery.records import Residual

    work = F.work()
    experiment = F.tabulation_experiment(work_id=work.work_id)
    references = tuple(
        F.observation(
            f"fixture-preserved-{index}",
            experiment.experiment_id,
            F.psat_identity("Na"),
            Decimal("1"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id=work.work_id,
        )
        for index in range(3)
    )
    context = _context(work, experiment, *references)
    root = tmp_path / "rerun-root"
    battery = root / "data" / "battery"
    battery.mkdir(parents=True)
    (battery / "pins.yaml").touch()
    paths = {
        "residuals": battery / "residuals.jsonl",
        "summary": battery / "score-summary.json",
        "report": battery / "score-report.md",
    }
    previous = {
        "residuals": b"previous residuals\n",
        "summary": b"previous summary\n",
        "report": b"previous report\n",
    }
    for name, path in paths.items():
        path.write_bytes(previous[name])

    stamp = score_mod.derive_store_stamp(score_mod.REPO_ROOT)
    monkeypatch.setattr(score_mod, "derive_store_stamp", lambda _root: stamp)
    monkeypatch.setattr(battery_score, "derive_store_stamp", lambda _root: stamp)
    monkeypatch.setattr(battery_score, "load_score_context", lambda _root: context)
    monkeypatch.setattr(battery_score, "emit_store_stamp_mismatch_warning", lambda *_: None)
    monkeypatch.setattr(battery_score, "load_legacy_score_rows", lambda _root: [])
    monkeypatch.setattr(
        battery_score,
        "load_pins",
        lambda _path: {"key_map": {}, "pin_band_records": []},
    )
    calls = 0

    def compile_stub(reference, engine, **_kwargs):
        nonlocal calls
        calls += 1
        if failure_stage == "scoring" and calls == 3:
            raise RuntimeError("injected scorer stop")
        return (
            Residual(
                key=f"fixture::{reference.observation_id}::{engine.value}",
                reference=reference.observation_id,
                execution=Execution(state=ExecutionState.NOT_PROBED),
                rail=Rail.VAPOUR,
                status=ResidualStatus.NO_BAND,
                source_relation=SourceRelation.UNKNOWN,
                score_eligible=False,
                exclusions=(),
                notices=(),
            ),
            None,
        )

    monkeypatch.setattr(score_mod, "compile_residual", compile_stub)
    if failure_stage == "summary":
        original_write_summary = battery_score._write_headline_summary_from_accumulator_json

        def write_then_fail(aggregate, path, *, store_stamp):
            original_write_summary(aggregate, path, store_stamp=store_stamp)
            raise RuntimeError("injected summary stop")

        monkeypatch.setattr(
            battery_score,
            "_write_headline_summary_from_accumulator_json",
            write_then_fail,
        )
    elif failure_stage == "report":
        original_write_text = Path.write_text

        def write_report_then_fail(path, data, *args, **kwargs):
            written = original_write_text(path, data, *args, **kwargs)
            if path.name.startswith("score-report.md"):
                raise RuntimeError("injected report stop")
            return written

        monkeypatch.setattr(Path, "write_text", write_report_then_fail)

    message = {
        "scoring": "injected scorer stop",
        "summary": "injected summary stop",
        "report": "injected report stop",
    }[failure_stage]
    with pytest.raises(RuntimeError, match=message):
        battery_score.main(
            ["--root", str(root), "--engines", Engine.INTERNAL_ANALYTICAL.value]
        )
    assert {name: path.read_bytes() for name, path in paths.items()} == previous


def test_battery_report_only_streams_residual_jsonl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import simulator.battery.score as score_mod
    from scripts import battery_score
    from simulator.battery.records import Residual, ResidualNumeric
    from simulator.battery.score import MetricOperation

    work = F.work()
    experiment = F.tabulation_experiment(work_id=work.work_id)
    reference = F.observation(
        "report-only-stream-row",
        experiment.experiment_id,
        F.psat_identity("Na"),
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id=work.work_id,
    )
    context = _context(work, experiment, reference)
    residual = Residual(
        key=f"fixture::{reference.observation_id}::{Engine.INTERNAL_ANALYTICAL.value}",
        reference=reference.observation_id,
        execution=Execution(state=ExecutionState.NOT_PROBED),
        rail=Rail.VAPOUR,
        status=ResidualStatus.MATCH,
        source_relation=SourceRelation.UNKNOWN,
        score_eligible=False,
        exclusions=(),
        notices=(),
        numeric=ResidualNumeric(
            MetricOperation.ABSOLUTE,
            "dimensionless",
            Decimal("1"),
            DecisionBand(Decimal("0.231"), "dimensionless", "fixture non-DEX band"),
        ),
    )
    root = tmp_path / "report-root"
    battery = root / "data" / "battery"
    battery.mkdir(parents=True)
    (battery / "pins.yaml").touch()
    residual_path = battery / "residuals.jsonl"
    score_mod.write_residuals_jsonl(
        (residual,),
        {},
        residual_path,
        root=score_mod.REPO_ROOT,
    )
    stamp = score_mod.derive_store_stamp(score_mod.REPO_ROOT)
    monkeypatch.setattr(battery_score, "load_score_context", lambda _root: context)
    monkeypatch.setattr(battery_score, "derive_store_stamp", lambda _root: stamp)
    monkeypatch.setattr(
        battery_score,
        "emit_store_stamp_mismatch_warning",
        lambda _recorded, _live: None,
    )
    monkeypatch.setattr(battery_score, "load_legacy_score_rows", lambda _root: [])
    monkeypatch.setattr(
        battery_score,
        "load_pins",
        lambda _path: {"key_map": {}, "pin_band_records": []},
    )

    def reject_full_load(_path):
        pytest.fail("report-only loaded the complete residual file")

    monkeypatch.setattr(score_mod, "load_residuals_jsonl", reject_full_load)
    assert (
        battery_score.main(
            [
                "--report-only",
                "--root",
                str(root),
                "--engines",
                Engine.INTERNAL_ANALYTICAL.value,
            ]
        )
        == 0
    )
    printed = capsys.readouterr().out
    assert "report-only residuals=1" in printed
    assert (battery / "score-report.md").is_file()
    assert (battery / "score-summary.json").is_file()
    import json

    summary = json.loads((battery / "score-summary.json").read_text(encoding="utf-8"))
    assert all("n_eligible_references" not in row for row in summary["records"])
    measured_vapour = next(
        row
        for row in summary["records"]
        if row["tier"] == "measured" and row["rail"] == Rail.VAPOUR.value
    )
    assert measured_vapour["band_width_dex"] is None


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
        Engine.OPENIMCC,
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
        engine=Engine.OPENIMCC,
        channel=Engine.OPENIMCC.value,
        unit="dimensionless",
        notices=notice,
    )
    unaffected_prediction = replace(
        _predict(Decimal("0.3"), identity),
        engine=Engine.ALPHAMELTS,
        channel=Engine.ALPHAMELTS.value,
        unit="dimensionless",
    )
    saturated, _ = compile_residual(
        reference,
        Engine.OPENIMCC,
        context=context,
        prediction=saturated_prediction,
    )
    unaffected, _ = compile_residual(
        reference,
        Engine.ALPHAMELTS,
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
        engines=(Engine.OPENIMCC, Engine.ALPHAMELTS),
    )
    by_engine = {
        row["engine"]: row for row in headline if row["rail"] == saturated.rail.value
    }
    assert by_engine[Engine.OPENIMCC.value]["n"] == 0
    assert by_engine[Engine.ALPHAMELTS.value]["n"] == 1
    assert by_engine[Engine.ALPHAMELTS.value]["n_score_eligible"] == 1

    flagged = flagged_stratum_rows((saturated,), engines=(Engine.OPENIMCC,))
    assert [(row["stratum"], row["n"]) for row in flagged] == [
        ("imcc_complex_saturation", 1)
    ]
    payload = residual_to_plain(saturated)
    assert any(
        row["kind"] == "imcc_complex_saturation" for row in payload["notices"]
    )
    assert [(row["stratum"], row["n"]) for row in flagged_stratum_payloads(
        (payload,), (Engine.OPENIMCC,)
    )] == [("imcc_complex_saturation", 1)]


def test_non_allibert_typed_solid_activity_uses_fusion_conversion() -> None:
    from simulator.battery.score import _fusion_comparison_reference

    temperature = Decimal("2000")
    composition = Composition(
        basis="printed_mole_fraction",
        components=(("CaO", Decimal("0.8")), ("Al2O3", Decimal("0.2"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    experiment = F.kems_experiment()
    identity = _with_activity_reference_polymorph(
        F.activity_identity(
            formula="CaO",
            T_K=temperature,
            endmember_phase=Phase.CR,
            component_basis="CaO",
            composition=composition,
        ),
        "lime",
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
    # Premise: JANAF electronic records Ca-027 (CaO(cr)) and Ca-028 (CaO(l)),
    # 2000 K formation-Gibbs rows, give -401.713 and -372.176 kJ/mol; neither
    # record has a printed page number. Algebra: ΔG_fus=G_l°-G_s°=+29.537
    # kJ/mol, so Δlog10(a)=-29.537*1000 J/kJ / (8.31441 J/(mol K)*2000 K*ln10)
    # = -0.7714171006707842 dex. Units cancel to a dimensionless shift; hence
    # a_l/a_s=10**shift=0.1692711323009035502632434871 (float.hex:
    # 0x1.5aaad2cb1d399p-3), below one because G_l°>G_s°.
    expected = Decimal("0.1692711323009035502632434871")
    assert float(converted.value.point).hex() == "0x1.5aaad2cb1d399p-3"
    assert abs(converted.value.point - expected) < Decimal("1e-27")
    assert converted.identity.reference_state.value.endmember.phase.value is Phase.L
    assert converted.source_id == "another-source"


@pytest.mark.parametrize(
    ("formula", "polymorph", "expected_offset", "tables"),
    (
        ("Al2O3", "corundum", Decimal("0.455"), ("Al-096", "Al-100")),
        ("CaO", "lime", Decimal("0.842"), ("Ca-027", "Ca-028")),
        (
            "SiO2",
            "cristobalite_high",
            Decimal("0.008"),
            ("O-035", "O-038"),
        ),
    ),
)
def test_solid_activity_fusion_conversion_reports_positive_offset(
    formula: str,
    polymorph: str,
    expected_offset: Decimal | None,
    tables: tuple[str, str],
) -> None:
    from simulator.battery.enums import Polymorph
    from simulator.battery.generators.janaf import janaf_fusion_energy
    from simulator.battery.score import _fusion_comparison_reference

    temperature = Decimal("1933")
    experiment = F.kems_experiment()
    identity = F.activity_identity(
        formula=formula,
        T_K=temperature,
        endmember_phase=Phase.CR,
        component_basis=formula,
    )
    reference_state = identity.reference_state
    assert reference_state is not None and reference_state.is_value
    assert reference_state.value is not None
    endmember = replace(
        reference_state.value.endmember,
        polymorph=State.of(Polymorph(polymorph)),
    )
    identity = replace(
        identity,
        reference_state=State.of(
            replace(reference_state.value, endmember=endmember)
        ),
    )
    reference = F.observation(
        f"{formula}-solid-activity-at-1933K",
        experiment.experiment_id,
        identity,
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="synthetic-solid-reference-anchor",
    )

    converted = _fusion_comparison_reference(reference, engine=Engine.OPENIMCC)
    if expected_offset is None:
        assert converted.value.point == reference.value.point
        assert converted.identity.reference_state.value.endmember.phase.value is Phase.CR
        assert any(
            "fusion conversion missing input" in item.reason
            and ("missing at" in item.reason or "spans missing grid node" in item.reason)
            for item in converted.notices
        )
        return
    notice = next(
        item
        for item in converted.notices
        if item.reason.startswith("reference_converted_via_fusion;")
    )
    offset = Decimal(notice.reason.partition("offset_dex=+")[2].split(";")[0])
    fusion = janaf_fusion_energy(formula, temperature)
    assert abs(offset - expected_offset) <= Decimal("0.002")
    assert fusion.crystal_table == tables[0]
    assert fusion.liquid_table == tables[1]
    assert f"tables={tables[0]}/{tables[1]}" in notice.reason
    assert f"T={temperature} K" in notice.reason
    assert "reference-state conversion (not model error)" in notice.reason
    assert converted.identity.reference_state.value.endmember.phase.value is Phase.L


def test_periclase_fusion_conversion_uses_restored_janaf_rows() -> None:
    from simulator.battery.score import _fusion_comparison_reference

    temperature = Decimal("1873")

    experiment = F.kems_experiment()
    identity = _with_activity_reference_polymorph(
        F.activity_identity(
            formula="MgO",
            T_K=temperature,
            endmember_phase=Phase.CR,
            component_basis="MgO",
        ),
        "periclase",
    )
    reference = F.observation(
        "mgo-periclase-solid-activity-at-1873K",
        experiment.experiment_id,
        identity,
        Decimal("0.2"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="synthetic-mgo-solid-reference-anchor",
    )
    converted = _fusion_comparison_reference(reference, engine=Engine.OPENIMCC)
    assert converted.value.point != reference.value.point
    assert converted.identity.reference_state.value.endmember.phase.value is Phase.L
    assert any(
        notice.reason.startswith("reference_converted_via_fusion;")
        for notice in converted.notices
    )


def test_periclase_solid_activity_fusion_conversion_uses_janaf_nodes() -> None:
    """At 1873 K, Mg-008 and Mg-009 give the expected periclase-to-liquid shift.

    Linear interpolation uses 73/100 of each table's 1800-to-1900 K span:
    G_s° = -360.851 + 0.73*(-340.395 + 360.851) = -345.91812 kJ/mol;
    G_l° = -330.816 + 0.73*(-312.503 + 330.816) = -317.44751 kJ/mol.
    ΔG_fus = G_l° - G_s° = +28.47061 kJ/mol, so
    Δlog10(a) = 28.47061*1000/(8.31441*1873*ln(10)) = +0.793984 dex.
    """
    from simulator.battery.generators.janaf import janaf_fusion_energy
    from simulator.battery.score import _fusion_comparison_reference

    temperature = Decimal("1873")
    fusion = janaf_fusion_energy("MgO", temperature)
    assert fusion.crystal_table == "Mg-008"
    assert fusion.liquid_table == "Mg-009"
    assert fusion.delta_g_fus_kJ_per_mol > 0
    # Round 9 restored the 3200 K node in both tables, moving the crossing.
    assert abs(
        fusion.melting_temperature_K - Decimal("3104.968203")
    ) < Decimal("0.00001")
    at_melting = janaf_fusion_energy("MgO", fusion.melting_temperature_K)
    assert abs(at_melting.delta_g_fus_kJ_per_mol) < Decimal("1e-20")

    experiment = F.kems_experiment()
    identity = _with_activity_reference_polymorph(
        F.activity_identity(
            formula="MgO",
            T_K=temperature,
            endmember_phase=Phase.CR,
            component_basis="MgO",
        ),
        "periclase",
    )
    reference = F.observation(
        "mgo-periclase-solid-activity-at-1873K",
        experiment.experiment_id,
        identity,
        Decimal("0.2"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="synthetic-mgo-solid-reference-anchor",
    )
    converted = _fusion_comparison_reference(reference, engine=Engine.OPENIMCC)
    notice = next(
        item
        for item in converted.notices
        if item.reason.startswith("reference_converted_via_fusion;")
    )
    offset = Decimal(notice.reason.partition("offset_dex=+")[2].split(";")[0])
    assert abs(offset - Decimal("0.793984")) <= Decimal("0.002")
    assert converted.identity.reference_state.value.endmember.phase.value is Phase.L

    wrong_polymorph = replace(
        reference,
        identity=_with_activity_reference_polymorph(identity, "spinel"),
    )
    refused = _fusion_comparison_reference(
        wrong_polymorph, engine=Engine.OPENIMCC
    )
    assert refused.value.point == reference.value.point
    assert refused.identity.reference_state.value.endmember.phase.value is Phase.CR
    assert any(
        "Mg-008 represents polymorph periclase" in notice.reason
        for notice in refused.notices
    )

@pytest.mark.parametrize(
    ("temperature", "expected_shift_dex"),
    (
        (Decimal("1823"), Decimal("-0.96857")),
        (Decimal("1873"), Decimal("-0.90911")),
    ),
)
def test_unknown_cao_polymorph_converts_via_unique_janaf_solid_table(
    temperature: Decimal,
    expected_shift_dex: Decimal,
) -> None:
    """The sole eligible CaO(cr) JANAF table fixes the unknown solid reference.

    For the same chemical potential, mu = G° + RT ln(a) gives
    log10(a_l/a_s) = -DeltaG_fus/(RT ln(10)). JANAF's DeltaG_fus is in
    kJ/mol, so convert by 1000 and use R = 8.314462618 J/(mol K). The
    interpolated JANAF values are 33.80370 kJ/mol at 1823 K and 32.59870
    kJ/mol at 1873 K, giving shifts -0.96857 and -0.90911 dex. As a
    sanity check, DeltaH_fus(1 - T/Tm), with Tm = 3200 K, gives 34.208 and
    32.966 kJ/mol at those temperatures, close to the JANAF interpolations.
    """
    from simulator.battery.score import _fusion_comparison_reference

    experiment = F.kems_experiment()
    identity = _with_activity_reference_polymorph(
        F.activity_identity(
            formula="CaO",
            T_K=temperature,
            endmember_phase=Phase.CR,
            component_basis="CaO",
        ),
        None,
    )
    reference = F.observation(
        f"cao-unknown-polymorph-at-{temperature}K",
        experiment.experiment_id,
        identity,
        Decimal("0.25"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="synthetic-unknown-cao-polymorph",
    )
    assert reference.admission.status is AdmissionStatus.ADMITTED

    converted = _fusion_comparison_reference(reference, engine=Engine.OPENIMCC)

    assert converted.identity.reference_state.value.endmember.phase.value is Phase.L
    assert converted.value.point != reference.value.point
    assert abs(
        (converted.value.point / reference.value.point).log10() - expected_shift_dex
    ) < Decimal("0.0005")
    notice = next(
        item
        for item in converted.notices
        if item.kind is NoticeKind.DERIVATION_USES_COMPILATION
    )
    assert "reference_converted_via_fusion" in notice.reason
    assert "source polymorph is unknown" in notice.reason


def test_unknown_polymorph_with_multiple_eligible_solid_tables_still_refuses() -> None:
    from simulator.battery.score import _fusion_comparison_reference

    experiment = F.kems_experiment()
    identity = _with_activity_reference_polymorph(
        F.activity_identity(
            formula="SiO2",
            T_K=Decimal("1933"),
            endmember_phase=Phase.CR,
            component_basis="SiO2",
        ),
        None,
    )
    reference = F.observation(
        "silica-unknown-polymorph-with-multiple-solid-tables",
        experiment.experiment_id,
        identity,
        Decimal("0.3"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="synthetic-ambiguous-solid-reference",
    )

    comparison = _fusion_comparison_reference(reference, engine=Engine.OPENIMCC)

    assert comparison.value.point == reference.value.point
    assert comparison.identity.reference_state.value.endmember.phase.value is Phase.CR
    assert not any(
        item.kind is NoticeKind.DERIVATION_USES_COMPILATION
        and "reference_converted_via_fusion" in item.reason
        for item in comparison.notices
    )
    assert any(
        "O-035 represents polymorph cristobalite_high" in item.reason
        and "measured reference polymorph is unknown" in item.reason
        for item in comparison.notices
    )


def test_tridymite_fusion_conversion_extrapolation_is_flagged() -> None:
    from simulator.battery.enums import Polymorph
    from simulator.battery.generators.janaf import JANAF_R_J_PER_MOL_K
    from simulator.battery.score import _fusion_comparison_reference

    experiment = F.kems_experiment()
    base_identity = F.activity_identity(
        formula="SiO2",
        T_K=Decimal("2001"),
        endmember_phase=Phase.CR,
        component_basis="SiO2",
    )
    before = F.observation(
        "silica-unknown-polymorph-before-tridymite-lift",
        experiment.experiment_id,
        _with_activity_reference_polymorph(base_identity, None),
        Decimal("0.3"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="synthetic-tridymite-reference",
    )
    after = F.observation(
        "silica-tridymite-reference-after-lift",
        experiment.experiment_id,
        _with_activity_reference_polymorph(base_identity, Polymorph.TRIDYMITE),
        Decimal("0.3"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="synthetic-tridymite-reference",
    )

    before_comparison = _fusion_comparison_reference(
        before, engine=Engine.OPENIMCC
    )
    after_comparison = _fusion_comparison_reference(after, engine=Engine.OPENIMCC)

    assert (
        before_comparison.identity.reference_state.value.endmember.phase.value
        is Phase.L
    )
    assert (
        after_comparison.identity.reference_state.value.endmember.phase.value
        is Phase.CR
    )
    offset_notice = next(
        notice
        for notice in after_comparison.notices
        if "B1259 tridymite→cristobalite offset applied" in notice.reason
    )
    assert offset_notice.authority is Authority.EXTRAPOLATED
    assert offset_notice.band == "B1259 common printed H/S band [298.15, 2000.0] K"
    assert "fusion conversion missing input:" in offset_notice.reason
    assert "authority=extrapolated" in offset_notice.reason
    assert any(
        "JANAF liquid fusion shift not applied because liquid is natural"
        in notice.reason
        for notice in after_comparison.notices
    )
    # Freeze the printed 2000 K differences beyond the B1259 band:
    # ΔH=0.105 kcal/mol and ΔS=0.060 cal/(mol K) at 2001 K.
    delta_g_tr_J_per_mol = (
        Decimal("0.105") * Decimal("4184")
        - Decimal("2001") * Decimal("0.060") * Decimal("4.184")
    )
    expected = Decimal("0.3") * (
        -delta_g_tr_J_per_mol / (JANAF_R_J_PER_MOL_K * Decimal("2001"))
    ).exp()
    assert after_comparison.value.point == expected
    assert (
        after_comparison.identity.reference_state.value.endmember.polymorph.value
        is Polymorph.CRISTOBALITE_HIGH
    )


def test_tridymite_fusion_conversion_applies_b1259_offset_to_value() -> None:
    from simulator.battery.enums import Polymorph
    from simulator.battery.generators.janaf import (
        JANAF_R_J_PER_MOL_K,
        janaf_fusion_energy,
    )
    from simulator.battery.score import (
        _b1259_tridymite_cristobalite_delta_g,
        _fusion_comparison_reference,
    )

    temperature = Decimal("1900")
    experiment = F.kems_experiment()
    identity = _with_activity_reference_polymorph(
        F.activity_identity(
            formula="SiO2",
            T_K=temperature,
            endmember_phase=Phase.CR,
            component_basis="SiO2",
        ),
        Polymorph.TRIDYMITE,
    )
    reference = F.observation(
        "silica-tridymite-reference-at-1900K",
        experiment.experiment_id,
        identity,
        Decimal("0.3"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="synthetic-tridymite-reference",
    )

    converted = _fusion_comparison_reference(reference, engine=Engine.OPENIMCC)
    # B1259 p. 145/p. 144 prints ΔfG=-136.315/-136.324 kcal/mol at 1900 K.
    # For equal chemical potential, both that source offset and the production
    # JANAF fusion Gibbs energy multiply activity by exp(-ΔG/RT).
    delta_g_tr_J_per_mol = Decimal("-0.009") * Decimal("4184")
    fusion = janaf_fusion_energy("SiO2", temperature)
    expected = Decimal("0.3") * (
        -(delta_g_tr_J_per_mol + fusion.delta_g_fus_kJ_per_mol * Decimal(1000))
        / (JANAF_R_J_PER_MOL_K * temperature)
    ).exp()
    assert converted.value.point == expected
    assert converted.identity.reference_state.value.endmember.phase.value is Phase.L
    assert any(
        "B1259 tridymite→cristobalite offset applied" in notice.reason
        and notice.authority is Authority.CERTIFIED
        for notice in converted.notices
    )
    # Linear interpolation between the printed 1800 and 1900 K H/S rows:
    # ΔH=0.105 kcal/mol and ΔS=0.060 cal/(mol K), so ΔG at 1850 K is −6 cal/mol.
    assert _b1259_tridymite_cristobalite_delta_g(Decimal("1850"))[0] == Decimal(
        "-25.104"
    )


def test_tridymite_offset_residual_scores_in_band_and_extrapolated_values() -> None:
    from simulator.battery.enums import Polymorph
    from simulator.battery.score import (
        EnginePrediction,
        _fusion_comparison_reference,
    )

    experiment = F.kems_experiment()
    for temperature, polymorph, offset_authority in (
        (Decimal("1900"), Polymorph.TRIDYMITE, Authority.CERTIFIED),
        (Decimal("2001"), Polymorph.TRIDYMITE, Authority.EXTRAPOLATED),
        (Decimal("1900"), Polymorph.CRISTOBALITE_HIGH, None),
    ):
        identity = _with_activity_reference_polymorph(
            F.activity_identity(
                formula="SiO2",
                T_K=temperature,
                endmember_phase=Phase.CR,
                component_basis="SiO2",
            ),
            polymorph,
        )
        reference = F.observation(
            f"silica-{polymorph.value}-residual-at-{temperature}K",
            experiment.experiment_id,
            identity,
            Decimal("0.3"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id="synthetic-tridymite-residual-reference",
        )
        converted = _fusion_comparison_reference(
            reference, engine=Engine.OPENIMCC
        )
        prediction = EnginePrediction(
            engine=Engine.OPENIMCC,
            channel="openimcc",
            execution=Execution(
                state=ExecutionState.PRODUCED,
                call_evidence="test:tridymite-offset-residual",
            ),
            value=converted.value.point,
            unit="dimensionless",
            authority=Authority.CERTIFIED,
            coefficient_sources=("nasa-cea-thermo",),
            lineage_complete=True,
            identity=converted.identity,
        )

        residual, candidate = compile_residual(
            reference,
            Engine.OPENIMCC,
            context=_context(F.work(), experiment, reference, review="reviewed"),
            prediction=prediction,
        )

        assert candidate is not None
        assert residual.status is ResidualStatus.NO_BAND
        assert residual.numeric is not None
        assert residual.numeric.value == 0
        offset_notices = [
            notice
            for notice in residual.notices
            if "B1259 tridymite→cristobalite offset applied" in notice.reason
        ]
        if offset_authority is None:
            assert offset_notices == []
        else:
            assert len(offset_notices) == 1
            offset_notice = offset_notices[0]
            assert offset_notice.authority is offset_authority
            assert offset_notice.reason.startswith(
                "B1259 tridymite→cristobalite offset applied;"
            )
            assert "fusion conversion missing input:" in offset_notice.reason


@pytest.mark.parametrize("polymorph", (None, "quartz"))
def test_solid_activity_with_unmatched_polymorph_refuses_conversion(
    polymorph: str | None,
) -> None:
    from simulator.battery.score import _fusion_comparison_reference

    experiment = F.kems_experiment()
    identity = _with_activity_reference_polymorph(
        F.activity_identity(
            formula="CaO",
            T_K=Decimal("1933"),
            endmember_phase=Phase.CR,
            component_basis="CaO",
        ),
        polymorph,
    )
    reference = F.observation(
        f"cao-solid-activity-{polymorph or 'unknown'}-polymorph",
        experiment.experiment_id,
        identity,
        Decimal("0.3"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="synthetic-unmatched-solid-polymorph",
    )

    comparison = _fusion_comparison_reference(reference, engine=Engine.OPENIMCC)
    if polymorph is None:
        assert comparison.value.point != reference.value.point
        assert comparison.identity.reference_state.value.endmember.phase.value is Phase.L
        assert any(
            notice.reason.startswith("reference_converted_via_fusion;")
            and "source polymorph is unknown" in notice.reason
            for notice in comparison.notices
        )
        return

    assert comparison.value.point == reference.value.point
    assert comparison.identity.reference_state.value.endmember.phase.value is Phase.CR
    assert any(
        "fusion conversion missing input" in notice.reason
        and "Ca-027 represents polymorph lime" in notice.reason
        for notice in comparison.notices
    )

    residual, _candidate = compile_residual(
        reference,
        Engine.OPENIMCC,
        context=_context(F.work(), experiment, reference, review="reviewed"),
    )
    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.IDENTITY_INCOMPLETE
    assert residual.refusal.detail["missing_input"] == (
        "JANAF solid polymorph matching the selected table"
    )


@pytest.mark.parametrize("polymorph", (None, "quartz"))
def test_silica_solid_activity_with_unmatched_polymorph_refuses_conversion(
    polymorph: str | None,
) -> None:
    from simulator.battery.score import _fusion_comparison_reference

    experiment = F.kems_experiment()
    identity = _with_activity_reference_polymorph(
        F.activity_identity(
            formula="SiO2",
            T_K=Decimal("1933"),
            endmember_phase=Phase.CR,
            component_basis="SiO2",
        ),
        polymorph,
    )
    reference = F.observation(
        f"silica-solid-activity-{polymorph or 'unknown'}-polymorph",
        experiment.experiment_id,
        identity,
        Decimal("0.3"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="synthetic-unmatched-solid-polymorph",
    )

    comparison = _fusion_comparison_reference(reference, engine=Engine.OPENIMCC)
    assert comparison.value.point == reference.value.point
    assert comparison.identity.reference_state.value.endmember.phase.value is Phase.CR
    assert any(
        "fusion conversion missing input" in notice.reason
        and "O-035 represents polymorph cristobalite_high" in notice.reason
        for notice in comparison.notices
    )

    residual, _candidate = compile_residual(
        reference,
        Engine.OPENIMCC,
        context=_context(F.work(), experiment, reference, review="reviewed"),
    )
    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.IDENTITY_INCOMPLETE
    assert residual.refusal.detail["missing_input"] == (
        "JANAF solid polymorph matching the selected table"
    )


def test_missing_janaf_fusion_node_refuses_activity_residual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from copy import deepcopy

    from simulator.battery.generators import janaf

    original_loader = janaf.load_table_document

    def missing_alumina_row(path):
        document = original_loader(path)
        if path.name != "Al-096.yaml":
            return document
        incomplete = deepcopy(document)
        incomplete["table"]["values"] = [
            row
            for row in incomplete["table"]["values"]
            if row["temperature"].get("value") != 1900
        ]
        return incomplete

    monkeypatch.setattr(janaf, "load_table_document", missing_alumina_row)
    janaf._fusion_table_points.cache_clear()
    janaf._fusion_missing_gibbs_temperatures.cache_clear()

    experiment = F.kems_experiment()
    identity = _with_activity_reference_polymorph(
        F.activity_identity(
            formula="Al2O3",
            T_K=Decimal("1933"),
            endmember_phase=Phase.CR,
            component_basis="Al2O3",
        ),
        "corundum",
    )
    reference = F.observation(
        "alumina-solid-activity-with-missing-janaf-node",
        experiment.experiment_id,
        identity,
        Decimal("0.3"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="synthetic-missing-janaf-node",
    )

    residual, _candidate = compile_residual(
        reference,
        Engine.OPENIMCC,
        context=_context(F.work(), experiment, reference, review="reviewed"),
    )
    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.IDENTITY_INCOMPLETE
    assert residual.refusal.detail["missing_input"] == (
        "JANAF solid/liquid formation Gibbs rows"
    )
    assert any(
        "needed JANAF table node row missing or changed" in notice.reason
        and "Al-096" in notice.reason
        for notice in residual.notices
    )


def test_absent_janaf_fusion_grid_node_refuses_interpolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.battery.generators import janaf

    original_points = janaf._fusion_table_points

    def without_silica_1700(table_id: str):
        points, digest = original_points(table_id)
        if table_id == "O-038":
            points = tuple((t, g) for t, g in points if t != Decimal("1700"))
        return points, digest

    monkeypatch.setattr(janaf, "_fusion_table_points", without_silica_1700)
    monkeypatch.setattr(janaf, "_fusion_missing_gibbs_temperatures", lambda _table_id: ())

    with pytest.raises(ValueError, match="1700"):
        janaf.janaf_fusion_energy("SiO2", Decimal("1673"))


def test_fusion_comparison_skips_missing_node_outside_interpolation_bracket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.battery.generators import janaf
    from simulator.battery.score import _fusion_comparison_reference

    original_points = janaf._fusion_table_points

    def without_silica_1700(table_id: str):
        points, digest = original_points(table_id)
        if table_id == "O-038":
            points = tuple((t, g) for t, g in points if t != Decimal("1700"))
        return points, digest

    monkeypatch.setattr(janaf, "_fusion_table_points", without_silica_1700)
    monkeypatch.setattr(janaf, "_fusion_missing_gibbs_temperatures", lambda _table_id: ())
    experiment = F.kems_experiment()
    reference = F.observation(
        "silica-activity-across-missing-janaf-node",
        experiment.experiment_id,
        _with_activity_reference_polymorph(
            F.activity_identity(
                formula="SiO2",
                T_K=Decimal("1933"),
                endmember_phase=Phase.CR,
                component_basis="SiO2",
            ),
            "cristobalite_high",
        ),
        Decimal("0.3"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="synthetic-missing-janaf-grid-node",
    )

    comparison = _fusion_comparison_reference(reference, engine=Engine.OPENIMCC)
    assert comparison.value.point != reference.value.point
    assert comparison.identity.reference_state.value.endmember.phase.value is Phase.L
    assert any(
        notice.reason.startswith("reference_converted_via_fusion;")
        for notice in comparison.notices
    )


def test_absent_janaf_grid_node_refuses_binary_cell_interpolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.diagnostic_helpers import binary_pot_battery as binary

    original_points = binary._cell_janaf_gibbs_points

    def without_wo_300(table_id: str):
        points, digest = original_points(table_id)
        if table_id == "O-027":
            points = tuple((t, g) for t, g in points if t != Decimal("300"))
        return points, digest

    monkeypatch.setattr(binary, "_cell_janaf_gibbs_points", without_wo_300)

    with pytest.raises(binary._OxygenBalanceRefusal, match="300"):
        binary._cell_oxide_thermodynamics("W", 325.0)


def test_silica_fusion_interpolates_from_restored_1700_row() -> None:
    from simulator.battery.generators.janaf import janaf_fusion_energy

    fusion = janaf_fusion_energy("SiO2", Decimal("1673"))
    assert fusion.delta_g_fus_kJ_per_mol == Decimal("1.21185")


def test_liquid_activity_reference_is_not_shifted() -> None:
    from simulator.battery.score import _fusion_comparison_reference

    experiment = F.kems_experiment()
    identity = F.activity_identity(
        formula="Al2O3",
        T_K=Decimal("1933"),
        endmember_phase=Phase.L,
        component_basis="Al2O3",
    )
    reference = F.observation(
        "alumina-liquid-activity",
        experiment.experiment_id,
        identity,
        Decimal("0.2"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="synthetic-liquid-reference",
    )

    assert _fusion_comparison_reference(reference, engine=Engine.OPENIMCC) is reference


def test_solid_activity_at_or_above_fusion_is_unshifted() -> None:
    from simulator.battery.generators.janaf import janaf_fusion_energy
    from simulator.battery.score import _fusion_comparison_reference

    temperature = Decimal("3300")
    fusion = janaf_fusion_energy("CaO", temperature)
    assert temperature > fusion.melting_temperature_K
    experiment = F.kems_experiment()
    reference = F.observation(
        "cao-solid-activity-above-fusion",
        experiment.experiment_id,
        _with_activity_reference_polymorph(
            F.activity_identity(
                formula="CaO",
                T_K=temperature,
                endmember_phase=Phase.CR,
                component_basis="CaO",
            ),
            "lime",
        ),
        Decimal("0.4"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="synthetic-above-fusion",
    )

    comparison = _fusion_comparison_reference(reference, engine=Engine.OPENIMCC)
    assert comparison.value.point == reference.value.point
    assert comparison.identity.reference_state.value.endmember.phase.value is Phase.L
    assert any(
        "no numeric reference-state conversion applied" in item.reason
        and f"T_fus={fusion.melting_temperature_K} K" in item.reason
        for item in comparison.notices
    )

def test_solid_activity_at_missing_fusion_node_is_refused() -> None:
    from simulator.battery.score import _fusion_comparison_reference

    temperature = Decimal("3300")
    experiment = F.kems_experiment()
    reference = F.observation(
        "cao-solid-activity-above-fusion",
        experiment.experiment_id,
        _with_activity_reference_polymorph(
            F.activity_identity(
                formula="CaO",
                T_K=temperature,
                endmember_phase=Phase.CR,
                component_basis="CaO",
            ),
            "lime",
        ),
        Decimal("0.4"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="synthetic-above-fusion",
    )

    comparison = _fusion_comparison_reference(reference, engine=Engine.OPENIMCC)
    assert comparison.value.point == reference.value.point
    assert comparison.identity.reference_state.value.endmember.phase.value is Phase.L
    assert any(
        item.kind is NoticeKind.OUT_OF_GAMMA_DOMAIN
        and "liquid is natural" in item.reason
        for item in comparison.notices
    )


@pytest.mark.parametrize(
    ("engine", "formula"),
    (
        (Engine.ALPHAMELTS, "SiO2"),
        (Engine.THERMOENGINE, "SiO2"),
        (Engine.ALPHAMELTS, "Al2O3"),
        (Engine.THERMOENGINE, "Al2O3"),
    ),
)
def test_melts_fusion_shift_uses_restored_alumina_rows(
    engine: Engine, formula: str
) -> None:
    from simulator.battery.score import _fusion_comparison_reference

    experiment = F.kems_experiment()
    reference = F.observation(
        f"{engine.value}-{formula}-solid-activity",
        experiment.experiment_id,
        _with_activity_reference_polymorph(
            F.activity_identity(
                formula=formula,
                T_K=Decimal("1933"),
                endmember_phase=Phase.CR,
                component_basis=formula,
            ),
            "corundum" if formula == "Al2O3" else "cristobalite_high",
        ),
        Decimal("0.3"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="synthetic-melts-reference-gap",
    )

    converted = _fusion_comparison_reference(reference, engine=engine)
    if formula == "SiO2":
        assert converted.value.point != reference.value.point
        assert any(
            item.kind is NoticeKind.DERIVATION_USES_COMPILATION
            and "applied shift is approximate" in item.reason
            for item in converted.notices
        )
    else:
        assert converted.value.point != reference.value.point
        assert converted.identity.reference_state.value.endmember.phase.value is Phase.L
        assert any(
            item.kind is NoticeKind.DERIVATION_USES_COMPILATION
            and item.reason.startswith("reference_converted_via_fusion;")
            for item in converted.notices
        )


@pytest.mark.parametrize(
    ("engine", "formula", "bound"),
    (
        (Engine.ALPHAMELTS, "SiO2", "|delta| <= 0.005 dex over 1600–2300 K"),
        (Engine.THERMOENGINE, "SiO2", "|delta| <= 0.005 dex over 1600–2300 K"),
        (Engine.ALPHAMELTS, "Al2O3", "under-corrects by +0.03 dex at 1933 K"),
        (Engine.THERMOENGINE, "Al2O3", "under-corrects by +0.03 dex at 1933 K"),
    ),
)
def test_melts_liquid_reference_gap_is_attached_to_fusion_shift(
    engine: Engine, formula: str, bound: str
) -> None:
    from simulator.battery.score import _fusion_comparison_reference

    experiment = F.kems_experiment()
    reference = F.observation(
        f"{engine.value}-{formula}-solid-activity",
        experiment.experiment_id,
        _with_activity_reference_polymorph(
            F.activity_identity(
                formula=formula,
                T_K=Decimal("1933"),
                endmember_phase=Phase.CR,
                component_basis=formula,
            ),
            "corundum" if formula == "Al2O3" else "cristobalite_high",
        ),
        Decimal("0.3"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="synthetic-melts-reference-gap",
    )

    converted = _fusion_comparison_reference(reference, engine=engine)
    assert converted.value.point != reference.value.point
    assert any(
        item.kind is NoticeKind.DERIVATION_USES_COMPILATION
        and "applied shift is approximate" in item.reason
        and bound in item.reason
        and "2026-10-02-melts-vs-janaf-liquid/findings.md" in item.reason
        for item in converted.notices
    )

def test_unestablished_engine_reference_keeps_solid_value_and_notices_residual() -> None:
    from simulator.battery.score import _fusion_comparison_reference

    inference_notice = Notice(
        kind=NoticeKind.DERIVATION_USES_COMPILATION,
        affected_quantities=(Quantity.ACTIVITY,),
        reason="relation=extract_inference; standard state source inference retained",
        origin="synthetic-reference-state-inference",
    )
    experiment = F.kems_experiment()
    identity = F.activity_identity(
        formula="CaO",
        T_K=Decimal("1933"),
        endmember_phase=Phase.CR,
        component_basis="CaO",
    )
    reference = F.observation(
        "cao-solid-activity-unestablished-engine",
        experiment.experiment_id,
        identity,
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="synthetic-unestablished-engine",
        notices=(inference_notice,),
    )

    untouched = _fusion_comparison_reference(
        reference, engine=Engine.INTERNAL_ANALYTICAL
    )
    assert untouched.value.point == reference.value.point
    assert untouched.identity.reference_state.value.endmember.phase.value is Phase.CR
    assert any(
        "engine reference is unestablished" in item.reason
        for item in untouched.notices
    )

    prediction = EnginePrediction(
        engine=Engine.INTERNAL_ANALYTICAL,
        channel="internal-analytical",
        execution=Execution(
            state=ExecutionState.PRODUCED,
            call_evidence="test:unestablished-activity-reference",
        ),
        value=Decimal("0.5"),
        unit="dimensionless",
        authority=Authority.CERTIFIED,
        coefficient_sources=("nasa-cea-thermo",),
        lineage_complete=True,
        identity=reference.identity,
    )
    context = _context(F.work(), experiment, reference, review="reviewed")
    residual, _candidate = compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        prediction=prediction,
    )
    assert residual.numeric is not None
    assert abs(residual.numeric.value - Decimal("0.5").log10()) < Decimal("0.000001")
    assert inference_notice in residual.notices
    assert any(
        "engine reference is unestablished" in item.reason
        for item in residual.notices
    )


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


def test_score_store_reuses_and_closes_engine_handles_for_100_cells(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    from simulator.diagnostic_helpers.binary_pot_battery import EngineHandle

    class _PoolLikeBackend:
        def __init__(self) -> None:
            self.read_fd, self.write_fd = os.pipe()
            self.calls = 0
            self.closed = False

        def equilibrate(self, **_kwargs):
            self.calls += 1
            raise RuntimeError("fd regression probe")

        def close(self) -> None:
            if self.closed:
                return
            os.close(self.read_fd)
            os.close(self.write_fd)
            self.closed = True

    opened = []

    def open_probe_engine(name: str) -> EngineHandle:
        backend = _PoolLikeBackend()
        opened.append(backend)
        return EngineHandle(
            name=name,
            backend=backend,
            available=True,
            unavailable_reason=None,
            takes_fo2=False,
            supports_intrinsic_fo2=True,
        )

    monkeypatch.setattr(battery, "open_battery_engine", open_probe_engine)
    experiment = F.tabulation_experiment()
    reference = F.observation(
        "fd-leak-reference",
        experiment.experiment_id,
        F.psat_identity("Na"),
        Decimal("0.1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="work-1",
    )
    context = _context(F.work(), experiment, reference, review="reviewed")

    equilibrate_cell_calls = 0
    real_equilibrate_cell = battery.equilibrate_cell

    def count_equilibrate_cell(*args, **kwargs):
        nonlocal equilibrate_cell_calls
        equilibrate_cell_calls += 1
        return real_equilibrate_cell(*args, **kwargs)

    monkeypatch.setattr(battery, "equilibrate_cell", count_equilibrate_cell)
    import simulator.battery.score as score_module

    monkeypatch.setattr(
        score_module,
        "comparison_candidates",
        lambda _context: (reference,) * 100,
    )
    fd_before = len(os.listdir("/dev/fd"))
    try:
        residuals, _ = score_store(
            context,
            engines=(Engine.VAPOROCK,),
            include_diagnostics=False,
        )
        fd_after = len(os.listdir("/dev/fd"))
        assert len(residuals) == 100
        assert fd_after <= fd_before + 2, (
            f"file descriptors grew from {fd_before} to {fd_after} across "
            "100 cell calls"
        )
        assert len(opened) == 1
        assert opened[0].calls == 100
        assert equilibrate_cell_calls == 100
    finally:
        for backend in opened:
            backend.close()
    assert len(os.listdir("/dev/fd")) <= fd_before + 1


def test_score_store_attempts_all_owned_handle_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    from simulator.diagnostic_helpers.binary_pot_battery import EngineHandle

    class _Backend:
        def __init__(self, fail_close: bool) -> None:
            self.fail_close = fail_close
            self.closed = False

        def equilibrate(self, **_kwargs):
            raise RuntimeError("engine probe")

        def close(self) -> None:
            self.closed = True
            if self.fail_close:
                raise RuntimeError("close probe")

    opened = []

    def open_probe_engine(name: str) -> EngineHandle:
        backend = _Backend(fail_close=not opened)
        opened.append(backend)
        return EngineHandle(
            name=name,
            backend=backend,
            available=True,
            unavailable_reason=None,
            takes_fo2=False,
            supports_intrinsic_fo2=True,
        )

    monkeypatch.setattr(battery, "open_battery_engine", open_probe_engine)
    experiment = F.tabulation_experiment()
    reference = F.observation(
        "fd-close-reference",
        experiment.experiment_id,
        F.psat_identity("Na"),
        Decimal("0.1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="work-1",
    )
    context = _context(F.work(), experiment, reference, review="reviewed")

    import simulator.battery.score as score_module

    monkeypatch.setattr(
        score_module,
        "comparison_candidates",
        lambda _context: (reference,),
    )
    with pytest.raises(RuntimeError, match="close probe"):
        score_store(
            context,
            engines=(Engine.VAPOROCK, Engine.MAGEMIN),
            include_diagnostics=False,
        )

    assert len(opened) == 2
    assert all(backend.closed for backend in opened)


@pytest.mark.parametrize(
    ("formula", "temperature", "missing_reason"),
    (
        ("CaO", Decimal("100"), "outside the JANAF table range"),
        ("NaO0.5", Decimal("1000"), "no JANAF fusion table pair"),
    ),
)
def test_missing_janaf_fusion_input_refuses_activity_residual(
    formula: str, temperature: Decimal, missing_reason: str
) -> None:
    from simulator.battery.score import (
        FLAGGED_STRATUM_REFERENCE_CONVERTED_VIA_FUSION,
        _fusion_comparison_reference,
    )

    experiment = F.kems_experiment()
    identity = F.activity_identity(
        formula=formula,
        T_K=temperature,
        endmember_phase=Phase.CR,
        component_basis=formula,
    )
    reference = F.observation(
        f"missing-janaf-{formula}-activity",
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
        engines=(Engine.OPENIMCC,),
    )
    assert len(residuals) == 1
    assert residuals[0].status is ResidualStatus.REFUSED
    assert residuals[0].refusal is not None
    assert residuals[0].refusal.reason is RefusalReason.IDENTITY_INCOMPLETE
    assert (
        residuals[0].refusal.detail["missing_input"]
        == "JANAF solid/liquid formation Gibbs rows"
    )
    notice = next(
        notice
        for notice in residuals[0].notices
        if notice.kind is NoticeKind.OUT_OF_GAMMA_DOMAIN
    )
    assert formula in notice.reason
    assert missing_reason in notice.reason
    if formula == "CaO":
        assert "JANAF table ranges" in notice.band


def test_allibert_solid_activity_fusion_conversion_is_diagnostic_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.battery.generators.janaf import (
        janaf_fusion_energy,
    )
    from simulator.battery.score import (
        FLAGGED_STRATUM_REFERENCE_CONVERTED_VIA_FUSION,
        _fusion_comparison_reference,
        flagged_stratum_rows,
        headline_rows,
        render_score_report_from_payloads,
    )

    assert "kems-051-allibert-1981" not in inspect.getsource(
        _fusion_comparison_reference
    )

    temperature = Decimal("2000")
    composition = Composition(
        basis="printed_mole_fraction",
        components=(("CaO", Decimal("0.8")), ("Al2O3", Decimal("0.2"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    experiment = F.kems_experiment()
    identity = _with_activity_reference_polymorph(
        F.activity_identity(
            formula="CaO",
            T_K=temperature,
            endmember_phase=Phase.CR,
            component_basis="CaO",
            composition=composition,
        ),
        "lime",
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
    assert alumina_fusion.melting_temperature_K == Decimal(
        "2326.528497409326424870466321"
    )
    assert alumina_fusion.accepted_melting_temperature_K == Decimal("2327")
    alumina_reference = replace(
        reference,
        observation_id="allibert-alumina-solid-activity",
        identity=_with_activity_reference_polymorph(
            F.activity_identity(
                formula="Al2O3",
                T_K=Decimal("2060"),
                endmember_phase=Phase.CR,
                component_basis="Al2O3",
                composition=composition,
            ),
            "corundum",
        ),
    )
    converted_alumina = _fusion_comparison_reference(alumina_reference)
    assert converted_alumina.value.point != alumina_reference.value.point
    assert converted_alumina.identity.reference_state.value.endmember.phase.value is Phase.L
    assert (
        converted_alumina.identity.reference_state.value.component_basis == "Al2O3"
    )
    alumina_notice = next(
        notice
        for notice in converted_alumina.notices
        if notice.reason.startswith(
            f"{FLAGGED_STRATUM_REFERENCE_CONVERTED_VIA_FUSION};"
        )
    )
    assert (
        "distance_below_JANAF_Tm=266.528497409326424870466321"
        in alumina_notice.reason
    )
    assert "accepted_Tm~2327 K" in alumina_notice.reason
    assert any(
        notice.kind is NoticeKind.DERIVATION_USES_COMPILATION
        and notice.reason.startswith("reference_converted_via_fusion;")
        for notice in converted_alumina.notices
    )
    silica_fusion = janaf_fusion_energy("SiO2", Decimal("1933"))
    assert silica_fusion.delta_g_fus_kJ_per_mol == Decimal("0.27784")
    assert Decimal("1994.4") < silica_fusion.melting_temperature_K < Decimal("1994.5")
    assert silica_fusion.accepted_melting_temperature_K == Decimal("1986")

    allibert_1933 = replace(
        reference,
        observation_id="allibert-cao-solid-activity-at-1933K",
        identity=replace(identity, temperature_K=State.of(Decimal("1933"))),
    )
    converted_1933 = _fusion_comparison_reference(allibert_1933)
    # Premise: JANAF records Ca-027 (CaO(cr)) and Ca-028 (CaO(l)), each with
    # T=1900/2000 K formation-Gibbs rows and no printed page number. Linear
    # interpolation to 1933 K gives G_s°=-420.996+0.33*(-401.713+420.996)
    # =-414.63261 and G_l°=-389.048+0.33*(-372.176+389.048)=-383.48024
    # kJ/mol; ΔG_fus=+31.15237 kJ/mol. Therefore Δlog10(a)=-31.15237*1000
    # J/kJ/(8.31441 J/(mol K)*1933 K*ln10)=-0.8418061863721363 dex.
    # Units cancel to a dimensionless shift; a_l/a_s=10**shift is below one
    # because G_l°>G_s°: 0.1439440817603981956094785450, float.hex
    # 0x1.26cc279ce8c6cp-3.
    expected = Decimal("0.1439440817603981956094785450")
    assert float(converted_1933.value.point).hex() == "0x1.26cc279ce8c6cp-3"
    assert abs(converted_1933.value.point - expected) < Decimal("1e-27")

    converted = _fusion_comparison_reference(reference)
    assert converted.identity.reference_state.value.endmember.phase.value is Phase.L
    assert converted.evidence.class_.is_unknown
    assert reference.value.point == Decimal("1")
    assert reference.identity.reference_state.value.endmember.phase.value is Phase.CR

    prediction = EnginePrediction(
        engine=Engine.OPENIMCC,
        channel="openimcc",
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
        Engine.OPENIMCC,
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
        and "reference-state conversion (not model error)" in notice.reason
        and "offset_dex=+" in notice.reason
        and "tables=Ca-027/Ca-028" in notice.reason
        and "distance_below_JANAF_Tm=1200" in notice.reason
        and "accepted_Tm~2886 K" in notice.reason
        for notice in residual.notices
    )
    assert headline_rows(
        (residual,), context=context, engines=(Engine.OPENIMCC,)
    )[0]["n"] == 0
    assert [
        (row["stratum"], row["n"])
        for row in flagged_stratum_rows(
            (residual,), engines=(Engine.OPENIMCC,)
        )
    ] == [(FLAGGED_STRATUM_REFERENCE_CONVERTED_VIA_FUSION, 1)]

    payload = residual_to_plain(residual)
    for tier in ("measured", "compilation"):
        headline = next(
            row
            for row in headline_payloads(
                (payload,), (Engine.OPENIMCC,), tier=tier
            )
            if row["rail"] == residual.rail.value
        )
        assert headline["n"] == 0
        assert headline["n_candidates"] == 0
    assert [
        (row["stratum"], row["n"])
        for row in flagged_stratum_payloads(
            (payload,), (Engine.OPENIMCC,)
        )
    ] == [(FLAGGED_STRATUM_REFERENCE_CONVERTED_VIA_FUSION, 1)]
    report = render_score_report_from_payloads(
        (payload,), engines=(Engine.OPENIMCC,), hostname="test"
    )
    assert (
        f"| {residual.rail.value} | {Engine.OPENIMCC.value} | "
        "0 | 0 | 0 | 0 |"
    ) in report
    assert (
        f"| {FLAGGED_STRATUM_REFERENCE_CONVERTED_VIA_FUSION} | "
        f"{residual.rail.value} | {Engine.OPENIMCC.value} | 1 |"
    ) in report

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
    assert not converted_source_point.identity.composition.is_value
    assert converted_source_point.identity.species.phase.is_unknown
    assert (
        converted_source_point.identity.reference_state.value.component_basis
        == "CaO"
    )
    assert (
        converted_source_point.identity.reference_state.value.endmember.phase.value
        is Phase.L
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
        == "Al2O3"
    )
    assert converted_alumina_source_point.identity.species.phase.is_unknown
    from simulator.battery.generators.bench import activity_request_for_engine
    from types import SimpleNamespace

    monkeypatch.setitem(
        sys.modules,
        "openimcc",
        SimpleNamespace(IMCC_PARENT_OXIDES=("CaO", "Al2O3", "SiO2", "MgO")),
    )

    activity_request = activity_request_for_engine(
        experiment, converted_alumina, Engine.ALPHAMELTS.value
    )
    assert activity_request is not None
    assert activity_request.payload is not None
    assert activity_request.payload["reference_state"] == (
        "raoultian_pure_liquid_endmember"
    )

    model_derived_reference = replace(
        reference,
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
        assert point.identity.reference_state.value.component_basis == "CaO"
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


def test_admitted_model_derived_rows_emit_residuals_per_imcc_engine() -> None:
    import simulator.battery.score as score_module

    context = load_score_context(
        sources=(
            "allibert",
            "stolyarova",
            "kems-ms2000-044",
            "kems-012-sossi-2019",
        )
    )
    observations = context.observations
    filtered = context
    admitted_model_derived = {
        key
        for key, obs in observations.items()
        if obs.admission.status is AdmissionStatus.ADMITTED
        and obs.evidence.class_.is_value
        and obs.evidence.class_.value is EvidenceClass.MODEL_DERIVED
    }
    allibert_activities = {
        key
        for key, obs in observations.items()
        if obs.source_id == "kems-051-allibert-1981"
        and isinstance(obs.identity, Identity)
        and quantity_token(obs.identity) is Quantity.ACTIVITY
    }
    allibert_admitted = {
        key
        for key in allibert_activities
        if observations[key].admission.status is AdmissionStatus.ADMITTED
    }
    allibert_rejected = allibert_activities - allibert_admitted
    stolyarova_activities = {
        key
        for key, obs in observations.items()
        if obs.source_id == "kems-053-stolyarova-1991"
        and isinstance(obs.identity, Identity)
        and quantity_token(obs.identity) is Quantity.ACTIVITY
    }
    stolyarova_derived_pressures = {
        key
        for key, obs in observations.items()
        if obs.source_id == "kems-053-stolyarova-1991"
        and isinstance(obs.identity, Identity)
        and quantity_token(obs.identity) is Quantity.P_PARTIAL
        and obs.admission.status is AdmissionStatus.ADMITTED
        and obs.evidence.class_.is_value
        and obs.evidence.class_.value is EvidenceClass.MODEL_DERIVED
    }
    stolyarova_1995_derived_pressures = {
        key
        for key, obs in observations.items()
        if obs.source_id == "stolyarova-1995-cao-alumina-kems"
        and isinstance(obs.identity, Identity)
        and quantity_token(obs.identity) is Quantity.P_PARTIAL
        and obs.admission.status is AdmissionStatus.ADMITTED
        and obs.evidence.class_.is_value
        and obs.evidence.class_.value is EvidenceClass.MODEL_DERIVED
    }
    stolyarova_1996_derived_pressures = {
        key
        for key, obs in observations.items()
        if obs.source_id == "stolyarova-1996-cao-alumina-silica-kems"
        and isinstance(obs.identity, Identity)
        and quantity_token(obs.identity) is Quantity.P_PARTIAL
        and obs.admission.status is AdmissionStatus.ADMITTED
        and obs.evidence.class_.is_value
        and obs.evidence.class_.value is EvidenceClass.MODEL_DERIVED
    }
    kems_model_derived = {
        key
        for key, obs in observations.items()
        if obs.source_id in {"kems-ms2000-044", "kems-012-sossi-2019"}
        and obs.admission.status is AdmissionStatus.ADMITTED
        and obs.evidence.class_.is_value
        and obs.evidence.class_.value is EvidenceClass.MODEL_DERIVED
    }
    headline_diagnostic_references = (
        (stolyarova_activities & admitted_model_derived)
        | stolyarova_derived_pressures
        | stolyarova_1995_derived_pressures
        | stolyarova_1996_derived_pressures
        | kems_model_derived
    )
    assert len(allibert_admitted) == 16
    assert len(allibert_rejected) == 55
    assert len(stolyarova_activities) == 54
    assert len(stolyarova_derived_pressures) == 9
    # Eqn 1 p_O (9), Eqn 2 p_O (8), and calculated p_O2 (9) are model-derived,
    # not measured pressures (review, Derived O / O2).
    assert len(stolyarova_1995_derived_pressures) == 26
    # Table 2's calculated 1933 K oxygen-pressure columns contain 30 pO,
    # 10 p'O, and 15 p''O observations, all model-derived.
    assert len(stolyarova_1996_derived_pressures) == 55
    assert {
        observations[key].observation_id.split("::")[1]
        for key in stolyarova_1996_derived_pressures
    } == {
        "stolyarova_1996_table2_pO_1933k",
        "stolyarova_1996_table2_pO_prime_1933k",
        "stolyarova_1996_table2_pO_double_prime_1933k",
    }
    assert [
        sum(
            observations[key].observation_id.split("::")[1] == observation_id
            for key in stolyarova_1996_derived_pressures
        )
        for observation_id in (
            "stolyarova_1996_table2_pO_1933k",
            "stolyarova_1996_table2_pO_prime_1933k",
            "stolyarova_1996_table2_pO_double_prime_1933k",
        )
    ] == [30, 10, 15]
    assert len(kems_model_derived) == 28
    assert len(
        stolyarova_activities | stolyarova_derived_pressures | kems_model_derived
    ) == 91
    assert len(headline_diagnostic_references) == 136
    assert admitted_model_derived == headline_diagnostic_references
    assert not admitted_model_derived & {
        obs.observation_id for obs in score_module.comparison_candidates(filtered)
    }

    def typed_test_refusal(engine, point, *, handles, experiment):
        if (
            point.source_id == "kems-053-stolyarova-1991"
            and isinstance(point.identity, Identity)
            and quantity_token(point.identity) is Quantity.ACTIVITY
        ):
            return score_module.predict_with_engine(
                engine,
                point,
                handles=handles,
                experiment=experiment,
            )
        reason = (
            RefusalReason.IDENTITY_UNKNOWN
            if point.source_id == "kems-051-allibert-1981"
            else RefusalReason.IDENTITY_INCOMPLETE
        )
        return EnginePrediction(
            engine=engine,
            channel=score_module.ENGINE_CHANNELS[engine],
            execution=Execution(state=ExecutionState.NOT_PROBED),
            coefficient_sources=(),
            lineage_complete=False,
            refusal_reason=reason,
            refusal_detail={"reason": "test_engine_identity_unavailable"},
            identity=point.identity if isinstance(point.identity, Identity) else None,
        )

    engines = (Engine.OPENIMCC,)
    residuals, _ = score_store(
        filtered,
        engines=engines,
        predict=typed_test_refusal,
    )
    headline_diagnostic_residuals = [
        residual
        for residual in residuals
        if residual.reference in headline_diagnostic_references
    ]
    assert len(headline_diagnostic_residuals) == len(headline_diagnostic_references)
    assert all(
        "reference_measured_evidence" in residual.exclusions
        for residual in headline_diagnostic_residuals
    )
    diagnostic_context = replace(
        filtered,
        observations={
            key: observations[key] for key in headline_diagnostic_references
        },
        origins={
            key: value
            for key, value in filtered.origins.items()
            if key in headline_diagnostic_references
        },
    )

    from simulator.battery.score import headline_payload_records, headline_rows

    def assert_empty_measured_headlines(rows):
        assert rows
        assert all(
            row["n"] == 0
            and row["n_refused"] == 0
            and row["n_candidates"] == 0
            and row["n_score_eligible"] == 0
            and row["rms_dex"] is None
            and row["band_width_dex"] is None
            and row["n_inside_band"] == 0
            for row in rows
        )

    object_headlines = headline_rows(
        headline_diagnostic_residuals,
        context=diagnostic_context,
        engines=engines,
    )
    assert_empty_measured_headlines(object_headlines)
    assert all(row["n_eligible_references"] == 0 for row in object_headlines)
    payload_rows = [
        residual_to_plain(residual) for residual in headline_diagnostic_residuals
    ]
    assert all(
        row.get("status") == ResidualStatus.REFUSED.value
        or row.get("notices")
        or row.get("refusal")
        for row in payload_rows
    )
    payload_record_headlines = headline_payload_records(
        payload_rows,
        engines=engines,
        observations=diagnostic_context.observations,
        origins=diagnostic_context.origins,
    )
    assert_empty_measured_headlines(payload_record_headlines)
    for engine in engines:
        assert_empty_measured_headlines(
            headline_payloads(payload_rows, (engine,), tier="measured")
        )

    def engine_rows(source_ids: set[str], engine: Engine):
        return [
            residual
            for residual in residuals
            if observations[residual.reference].source_id in source_ids
            and residual.key.rsplit("::", 1)[-1] == engine.value
        ]

    for engine in engines:
        allibert_rows = engine_rows({"kems-051-allibert-1981"}, engine)
        assert len(allibert_rows) == 16
        assert {row.reference for row in allibert_rows} == allibert_admitted
        assert all(row.status is ResidualStatus.REFUSED for row in allibert_rows)
        # b-617 types Allibert's printed per-point phases: the two xCaO = 0.80
        # "CaO + melt" points refuse as two-phase bulk compositions; the 14
        # single-phase points lack the pressure set required by the in-cell
        # fallback and refuse as an unverified effusion regime.
        assert all(row.refusal is not None for row in allibert_rows)
        assert (
            sum(
                row.refusal.reason is RefusalReason.BULK_NOT_LIQUID_COMPOSITION
                for row in allibert_rows
            )
            == 2
        )
        assert (
            sum(
                row.refusal.reason is RefusalReason.EFFUSION_REGIME_UNVERIFIED
                for row in allibert_rows
            )
            == 14
        )
        assert all(not row.score_eligible for row in allibert_rows)
        allibert_cao_rows = [
            row
            for row in allibert_rows
            if observations[row.reference].identity.species.formula == "CaO"
        ]
        allibert_alumina_rows = [
            row
            for row in allibert_rows
            if observations[row.reference].identity.species.formula == "Al2O3"
        ]
        assert len(allibert_cao_rows) == 8
        assert all(
            any(
                notice.kind is NoticeKind.DERIVATION_USES_COMPILATION
                and notice.reason.startswith("reference_converted_via_fusion;")
                and "source polymorph is unknown" in notice.reason
                for notice in row.notices
            )
            for row in allibert_cao_rows
        )
        assert len(allibert_alumina_rows) == 8
        assert all(
            any(
                "fusion conversion missing input" in notice.reason
                and "measured reference polymorph is unknown" in notice.reason
                for notice in row.notices
            )
            for row in allibert_alumina_rows
        )
        assert not any(row.reference in allibert_rejected for row in residuals)

        stolyarova_rows = engine_rows(
            {"kems-053-stolyarova-1991", "stolyarova-1995-cao-alumina-kems"},
            engine,
        )
        stolyarova_rows.extend(
            row
            for row in residuals
            if row.reference in stolyarova_1996_derived_pressures
            and row.key.rsplit("::", 1)[-1] == engine.value
        )
        assert len(stolyarova_rows) == 235
        activity_rows = [
            row for row in stolyarova_rows if row.reference in stolyarova_activities
        ]
        derived_pressure_rows = [
            row
            for row in stolyarova_rows
            if row.reference in stolyarova_derived_pressures
        ]
        assert len(activity_rows) == 54
        assert len(derived_pressure_rows) == 9
        stolyarova_1995_rows = [
            row
            for row in stolyarova_rows
            if row.reference in stolyarova_1995_derived_pressures
        ]
        assert len(stolyarova_1995_rows) == 26
        stolyarova_1996_rows = [
            row
            for row in stolyarova_rows
            if row.reference in stolyarova_1996_derived_pressures
        ]
        assert len(stolyarova_1996_rows) == 55
        assert all(row.status is ResidualStatus.REFUSED for row in stolyarova_1996_rows)
        assert all(not row.score_eligible for row in stolyarova_1996_rows)
        assert {
            row.refusal.reason
            for row in stolyarova_1996_rows
            if row.refusal is not None
        } == {
            RefusalReason.IDENTITY_INCOMPLETE,
            RefusalReason.EFFUSION_REGIME_UNVERIFIED,
        }
        assert all(row.status is ResidualStatus.REFUSED for row in stolyarova_rows)
        assert all(row.status is ResidualStatus.REFUSED for row in activity_rows)
        assert all(
            row.refusal is not None
                and row.refusal.reason
                in {
                    RefusalReason.EFFUSION_REGIME_UNVERIFIED,
                    RefusalReason.IDENTITY_INCOMPLETE,
                    RefusalReason.UNDERDETERMINED_APPARATUS,
                }
            for row in activity_rows
        )
        assert any(
            row.refusal is not None
            and row.refusal.reason is RefusalReason.EFFUSION_REGIME_UNVERIFIED
            for row in activity_rows
        )
        assert all(not row.score_eligible for row in stolyarova_rows)
        assert all(row.status is ResidualStatus.REFUSED for row in stolyarova_1995_rows)


def test_kume_real_migrated_activity_scores_numeric_with_openimcc(
    tmp_path: Path,
) -> None:
    pytest.importorskip("openimcc", reason="openimcc is not importable")
    from tests.battery.test_migrate import _migrate_real_extract

    result = _migrate_real_extract(
        tmp_path, "kume-2000-cao-activities.yaml", write=True
    )
    work_id = next(iter(result.works))
    observation_id = next(
        observation_id
        for observation_id in result.observations
        if observation_id.endswith("::kume_2000_table2_sample_101")
    )
    context = load_score_context(
        tmp_path / "tree", sources=("kume-2000-cao-activities",)
    )
    reference = context.observations[observation_id]

    assert reference.identity.species.phase.is_value
    assert reference.identity.species.phase.value is Phase.L

    residuals, _candidates = score_store(
        context,
        engines=(Engine.OPENIMCC,),
        work_id=work_id,
    )
    residual = next(row for row in residuals if row.reference == observation_id)

    assert residual.numeric is not None
    assert residual.status is not ResidualStatus.REFUSED
    assert residual.score_eligible is False
    assert any(
        notice.kind is NoticeKind.UNVERIFIED_APPARATUS
        for notice in residual.notices
    )


def test_allibert_xcao_0_80_rows_refuse_bulk_not_liquid_composition(tmp_path: Path) -> None:
    """Printed 'CaO + melt' is the two-phase marker. The scorer does not stamp it liquid."""

    from simulator.battery.identity import quantity_token
    from simulator.battery.score import compile_residual
    from tests.battery.test_migrate import _migrate_real_extract

    result = _migrate_real_extract(tmp_path, "kems-051-allibert-1981.yaml")
    rows = []
    for observation in result.observations.values():
        if observation.source_id != "kems-051-allibert-1981":
            continue
        if quantity_token(observation.identity) is not Quantity.ACTIVITY:
            continue
        if str(getattr(observation.locator, "table", None)) != "II":
            continue
        holders = [observation.identity.composition]
        holders.append((observation.point_conditions or {}).get("composition"))
        cao = None
        for holder in holders:
            state = getattr(holder, "state", holder)
            if state is None or not getattr(state, "is_value", False) or state.value is None:
                continue
            cao = next(
                (amount for name, amount in state.value.components if name == "CaO"),
                None,
            )
            if cao is not None:
                break
        if cao is None:
            continue
        if cao == Decimal("0.8"):
            rows.append(observation)
    assert len(rows) == 2
    assert {observation.identity.species.formula for observation in rows} == {
        "CaO",
        "Al2O3",
    }

    def predict_must_not_run(*args, **kwargs):
        raise AssertionError("two-phase bulk row reached the engine")

    context = ScoreContext(
        works=result.works,
        experiments=result.experiments,
        observations={observation.observation_id: observation for observation in rows},
        extract_review={"kems-051-allibert-1981": "reviewed"},
    )
    for observation in rows:
        assert observation.identity.species.phase.is_unknown
        assert "bulk_composition_in_two_phase_region" in (
            observation.identity.species.phase.reason or ""
        )
        for engine in (Engine.OPENIMCC,):
            residual, _candidate = compile_residual(
                observation,
                engine,
                context=context,
                predict=predict_must_not_run,
            )
            assert residual.status is ResidualStatus.REFUSED
            assert residual.numeric is None
            assert residual.refusal is not None
            assert (
                residual.refusal.reason
                is RefusalReason.BULK_NOT_LIQUID_COMPOSITION
            )


def test_sole_typed_bench_without_recorded_link_is_not_adopted() -> None:
    """An unlinked experiment does not inherit its work's only typed bench.

    Restoring the sole-bench adoption in _bench_for_score makes this red:
    the W bench would be attached and the refusal would not be
    cell_material_unknown.
    """
    from simulator.battery.enums import BenchIdentityBasis, CellMaterial
    from simulator.battery.records import Bench, BenchIdentity

    experiment = F.kems_experiment()
    assert experiment.bench_id is None
    bench = Bench(
        id="sole-w",
        work_id=experiment.work_id or "work-1",
        identity=BenchIdentity(
            BenchIdentityBasis.INFERRED_FROM_EMBEDDED_EVIDENCE,
            reason="test fixture",
        ),
        cell_materials=(F.located(CellMaterial.W),),
    )
    composition = Composition(
        basis="ordered_complete_mole_inventory",
        components=(("K2O", Decimal("0.2")), ("SiO2", Decimal("0.8"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    ident = replace(
        F.activity_identity(
            formula="K",
            composition=composition,
            component_basis="K2O",
        ),
        quantity=Quantity.P_PARTIAL,
        species=Species("K", Phase.G),
        fO2_Pa=State.unknown("no fO2 printed"),
    )
    reference = F.observation(
        "unlinked-sole-bench",
        experiment.experiment_id,
        ident,
        Decimal("1e-6"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="sole-bench-work",
    )
    context = replace(
        _context(F.work(), experiment, reference, review="reviewed"),
        benches={bench.id: bench},
    )
    residual, _candidate = compile_residual(
        reference,
        Engine.OPENIMCC,
        context=context,
    )
    assert residual.refusal is not None
    assert residual.refusal.detail.get("reason") == "cell_material_unknown"


def _cell_material_score_case(material, inference):
    from simulator.battery.enums import BenchIdentityBasis
    from simulator.battery.records import Bench, BenchIdentity

    bench_id = f"cell-{material.value}"
    experiment = replace(F.kems_experiment(), bench_id=bench_id)
    reference = F.observation(
        f"{bench_id}-row",
        experiment.experiment_id,
        replace(
            _partial_identity(),
            species=Species("K", Phase.G),
            total_pressure_Pa=State.of(Decimal("1e-6")),
        ),
        Decimal("1e-6"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="test-kems",
    )
    fo2_inference = Derivation(
        relation="inferred fO2 from oxygen balance",
        inputs=("printed oxygen-bearing species",),
        parameters=(),
        output_unit="Pa",
    )
    reference = replace(
        reference,
        point_conditions={
            **(reference.point_conditions or {}),
            "fO2_Pa": Located(
                State.of(Decimal("1e-8")),
                locator=F.loc(page=18, paragraph="Results"),
                inference=fo2_inference,
            ),
        },
    )
    bench = Bench(
        id=bench_id,
        work_id=experiment.work_id or "work-1",
        identity=BenchIdentity(
            BenchIdentityBasis.INFERRED_FROM_EMBEDDED_EVIDENCE,
            reason="cell material fixture",
        ),
        cell_materials=(F.located(material),)
        if inference is None
        else (
            Located(
                State.of(material),
                locator=F.loc(page=18, paragraph="Results"),
                inference=inference,
            ),
        ),
    )
    context = replace(
        _context(F.work(), experiment, reference, review="reviewed"),
        benches={bench.id: bench},
    )
    prediction = lambda engine, identity, **kwargs: EnginePrediction(
        engine=engine,
        channel=engine.value,
        execution=Execution(state=ExecutionState.PRODUCED, call_evidence="test:predict"),
        value=Decimal("1e-6"),
        unit="Pa",
        authority=Authority.CERTIFIED,
        coefficient_sources=("test",),
        lineage_complete=True,
        identity=getattr(identity, "identity", identity),
    )
    return reference, context, bench, prediction


def _migrated_stolyarova_cell_context(tmp_path: Path) -> ScoreContext:
    from simulator.battery.migrate import Migrator, write_outputs

    fixture = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "battery"
        / "stolyarova-1996-inferred-cell.yaml"
    )
    root = tmp_path / "stolyarova-store"
    extract_dir = root / "data" / "literature" / "extracts"
    extract_dir.mkdir(parents=True)
    shutil.copyfile(fixture, extract_dir / fixture.name)

    migrator = Migrator(root, index={}, aliases={})
    migrator.migrate_extracts()
    migrator.finalize()
    write_outputs(migrator.result, root)
    context = load_score_context(root)
    review = dict(context.extract_review)
    review["stolyarova-1996-cao-alumina-silica-kems"] = "reviewed"
    return replace(context, extract_review=review)


@pytest.mark.parametrize(
    "record_form",
    (
        "bench.cell_materials[0]",
        "bench.cell_material_and_liner",
        "experiment.apparatus.cell_material_and_liner",
    ),
)
def test_inferred_cell_material_is_noticed_in_each_record_form(record_form: str) -> None:
    from simulator.battery.enums import CellMaterial
    from simulator.battery.records import Apparatus
    from simulator.battery.score import _cell_apparatus_inference_notices

    inference = Derivation(
        relation="extract_inference",
        inputs=("inferred=true", "Mo ions identify the cell material"),
        parameters=(),
        output_unit="as_published",
    )
    reference, context, bench, _prediction = _cell_material_score_case(
        CellMaterial.MO, inference
    )
    experiment = context.experiments[reference.experiment_id]
    if record_form == "bench.cell_material_and_liner":
        bench = replace(
            bench,
            cell_materials=None,
            cell_material_and_liner=Located(State.of("Mo"), inference=inference),
        )
    elif record_form == "experiment.apparatus.cell_material_and_liner":
        bench = replace(bench, cell_materials=None)
        experiment = replace(
            experiment,
            apparatus=Apparatus(
                cell_material_and_liner=Located(State.of("Mo"), inference=inference)
            ),
        )

    notices = _cell_apparatus_inference_notices(reference, experiment, bench)
    notice = next(item for item in notices if item.kind is NoticeKind.CELL_MATERIAL_INFERRED)
    assert json.loads(notice.reason.partition(":")[2])["field"] == record_form


def test_real_stolyarova_cell_passes_mo_gate_and_scores_with_notice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.battery.enums import CellMaterial
    from simulator.battery.score import predict_with_engine
    from simulator.diagnostic_helpers import binary_pot_battery as battery

    context = _migrated_stolyarova_cell_context(tmp_path)
    reference = next(
        item
        for item in context.observations.values()
        if isinstance(item.identity, Identity)
        and quantity_token(item.identity) is Quantity.P_PARTIAL
        and item.evidence.class_.is_value
        and item.evidence.class_.value is EvidenceClass.MEASURED_DIRECT
    )
    experiment = context.experiments[reference.experiment_id]
    assert experiment.bench_id is not None
    bench = context.benches[experiment.bench_id]
    material = bench.cell_materials[0]
    assert material.state.value is CellMaterial.MO
    assert material.inference is not None
    assert material.inference.relation == "extract_inference"
    assert material.inference.inputs[0] == "inferred=true"

    # Stolyarova does not publish orifice geometry. Supply valid test geometry
    # so this integration case reaches prediction and residual scoring.
    ordinary_kems = F.kems_experiment(
        experiment.experiment_id, experiment.work_id or "work-1"
    )
    experiment = replace(
        experiment,
        apparatus=ordinary_kems.apparatus,
        pressure_environment=ordinary_kems.pressure_environment,
    )
    engine_handle = battery.EngineHandle(
        name=Engine.OPENIMCC.value,
        backend=object(),
        available=True,
        unavailable_reason=None,
        takes_fo2=False,
        supports_intrinsic_fo2=True,
        identity={"version": "test"},
    )
    prediction_inputs = []

    def fake_equilibrate(_handle, pot, *, temperature_K, po2, **_kwargs):
        prediction_inputs.append(po2)
        return battery.EquilibrateCell(
            pot_id=pot.pot_id,
            engine=Engine.OPENIMCC.value,
            temperature_K=temperature_K,
            po2=po2,
            status="ok",
            refusal_reason=None,
            engine_status="ok",
            engine_reason=None,
            melt_activities={},
            gas_partial_pressures_Pa={
                reference.identity.species.formula: float(reference.value.point)
            },
            liquid_fraction=1.0,
            wall_s=0.0,
            cpu_s=0.0,
            hostname="test",
        )

    monkeypatch.setattr(battery, "open_battery_engine", lambda _name: engine_handle)
    monkeypatch.setattr(battery, "equilibrate_cell", fake_equilibrate)
    prediction = predict_with_engine(
        Engine.OPENIMCC,
        reference,
        experiment=experiment,
        bench=bench,
    )
    assert len(prediction_inputs) == 1
    assert prediction_inputs[0].cell_material == "Mo"

    # This legacy extract leaves several identity axes unresolved. Use a typed
    # KEMS scoring identity for the residual while keeping the migrated row id,
    # measured value, and inferred bench unchanged.
    reference = replace(
        reference,
        identity=replace(
            _partial_identity(),
            species=Species("K", Phase.G),
            temperature_K=reference.identity.temperature_K,
            total_pressure_Pa=State.of(Decimal("1e-6")),
        ),
        point_conditions={
            **(reference.point_conditions or {}),
            "fO2_Pa": Located(
                State.of(Decimal("1e-8")),
                locator=F.loc(page=18, paragraph="Results"),
                inference=Derivation(
                    relation="inferred fO2 from oxygen balance",
                    inputs=("printed oxygen-bearing species",),
                    parameters=(),
                    output_unit="Pa",
                ),
            ),
        },
    )
    prediction = replace(prediction, identity=reference.identity)

    context = replace(
        context,
        experiments={**context.experiments, experiment.experiment_id: experiment},
    )
    band = DecisionBand(Decimal("0.2"), "dimensionless", "clean KEMS band")
    residual, _ = compile_residual(
        reference,
        Engine.OPENIMCC,
        context=context,
        prediction=prediction,
        derived_band=band,
    )
    assert residual.status is ResidualStatus.MATCH
    assert residual.numeric is not None
    assert residual.numeric.decision_band == band
    assert residual.score_eligible is False
    assert any(
        item.kind is NoticeKind.CELL_MATERIAL_INFERRED
        for item in residual.notices
    )
    strata = flagged_stratum_payloads(
        (residual_to_plain(residual),), (Engine.OPENIMCC,)
    )
    assert strata[0]["stratum"] == "cell-material-inferred"


def test_stolyarova_rows_do_not_move_any_kems_band_path(tmp_path: Path) -> None:
    from simulator.battery.enums import BenchIdentityBasis, CellMaterial
    from simulator.battery.records import Bench, BenchIdentity
    from simulator.battery.score import _derive_kems_partial_pressure_band

    context = _migrated_stolyarova_cell_context(tmp_path)
    flagged = next(
        item
        for item in context.observations.values()
        if isinstance(item.identity, Identity)
        and quantity_token(item.identity) is Quantity.P_PARTIAL
        and item.evidence.class_.is_value
        and item.evidence.class_.value is EvidenceClass.MEASURED_DIRECT
    )
    flagged_experiment = context.experiments[flagged.experiment_id]
    flagged_bench = context.benches[flagged_experiment.bench_id]

    ordinary_kems = F.kems_experiment(
        "stolyarova-clean-kems", flagged_experiment.work_id or "work-1"
    )
    clean_bench = Bench(
        id="stolyarova-clean-pt",
        work_id=ordinary_kems.work_id or "work-1",
        identity=BenchIdentity(BenchIdentityBasis.DESCRIBED_IN_THIS_WORK),
        cell_materials=(F.located(CellMaterial.PT),),
    )
    clean_experiment = replace(ordinary_kems, bench_id=clean_bench.id)
    band_identity = replace(
        flagged.identity,
        species=replace(flagged.identity.species, phase=State.of(Phase.G)),
        composition=_partial_identity().composition,
    )
    clean_rows = tuple(
        replace(
            flagged,
            observation_id=f"stolyarova-clean-{index}",
            experiment_id=clean_experiment.experiment_id,
            source_id="stolyarova-clean-test",
            identity=band_identity,
            value=Value.point_of(value),
            notices=(),
        )
        for index, value in enumerate((Decimal("1e-4"), Decimal("2e-4")))
    )
    clean = {row.observation_id: row for row in clean_rows}
    experiments = {
        flagged_experiment.experiment_id: flagged_experiment,
        clean_experiment.experiment_id: clean_experiment,
    }
    benches = {flagged_bench.id: flagged_bench, clean_bench.id: clean_bench}

    printed_clean = {
        row.observation_id: replace(
            row,
            uncertainty=Uncertainty(
                UncertaintyKind.PRINTED,
                verbatim="10% pressure error",
            ),
        )
        for row in clean_rows
    }
    printed_flagged = replace(
        flagged,
        uncertainty=Uncertainty(
            UncertaintyKind.PRINTED,
            verbatim="80% pressure error",
        ),
    )
    printed_flagged_replica = replace(
        printed_flagged,
        observation_id="stolyarova-inferred-printed-replica",
        value=Value.point_of(Decimal("1e-2")),
    )
    printed_mixed = {
        **printed_clean,
        printed_flagged.observation_id: printed_flagged,
        printed_flagged_replica.observation_id: printed_flagged_replica,
    }
    printed_baseline = _derive_kems_partial_pressure_band(
        printed_clean, experiments, {clean_bench.id: clean_bench}
    )
    printed_mixed_band = _derive_kems_partial_pressure_band(
        printed_mixed, experiments, benches
    )
    assert printed_baseline is not None
    assert "printed pressure envelope" in printed_baseline.rule
    assert printed_mixed_band == printed_baseline

    flagged_replica = replace(
        flagged,
        observation_id="stolyarova-inferred-replicate",
        identity=band_identity,
        value=Value.point_of(Decimal("1e-2")),
    )
    replicate_flagged = replace(flagged, identity=band_identity)
    replicate_mixed = {
        **clean,
        flagged.observation_id: replicate_flagged,
        flagged_replica.observation_id: flagged_replica,
    }
    replicate_baseline = _derive_kems_partial_pressure_band(
        clean, experiments, {clean_bench.id: clean_bench}
    )
    replicate_mixed_band = _derive_kems_partial_pressure_band(
        replicate_mixed, experiments, benches
    )
    assert replicate_baseline is not None
    assert "replicate scatter" in replicate_baseline.rule
    assert replicate_mixed_band == replicate_baseline
    assert derive_kems_partial_pressure_band(
        replicate_mixed, experiments, benches=benches
    ) == replicate_baseline


def test_inferred_mo_cell_scores_and_is_visible_on_residual_row() -> None:
    from simulator.battery.enums import CellMaterial

    inference = Derivation(
        relation="inferred from printed Mo oxide ions",
        inputs=("Mo+;MoO+;MoO2+;MoO3+",),
        parameters=(),
        output_unit="cell material",
    )
    reference, context, bench, prediction = _cell_material_score_case(
        CellMaterial.MO, inference
    )
    clean_exp = replace(
        context.experiments[reference.experiment_id],
        experiment_id="clean-for-score",
        bench_id="clean-pt",
    )
    clean_rows = tuple(
        replace(
            reference,
            observation_id=f"clean-for-score-{index}",
            experiment_id=clean_exp.experiment_id,
            value=Value.point_of(value),
        )
        for index, value in enumerate((Decimal("1e-6"), Decimal("2e-6")))
    )
    clean_bench = replace(
        bench,
        id="clean-pt",
        cell_materials=(F.located(CellMaterial.PT),),
    )
    context = replace(
        context,
        observations={
            **context.observations,
            **{row.observation_id: row for row in clean_rows},
        },
        experiments={**context.experiments, clean_exp.experiment_id: clean_exp},
        benches={**context.benches, clean_bench.id: clean_bench},
    )

    residual, _candidate = compile_residual(
        reference,
        Engine.OPENIMCC,
        context=context,
        predict=prediction,
    )

    assert residual.status is ResidualStatus.MATCH
    assert residual.numeric is not None
    assert residual.numeric.decision_band is not None
    assert residual.score_eligible is False
    assert "not_flagged_stratum" in residual.exclusions
    notice = next(
        item for item in residual.notices
        if item.kind is NoticeKind.CELL_MATERIAL_INFERRED
    )
    payload = residual_to_plain(residual)
    serialized_notice = next(
        item for item in payload["notices"]
        if item["kind"] == NoticeKind.CELL_MATERIAL_INFERRED.value
    )
    evidence = json.loads(notice.reason.partition(":")[2])
    assert evidence["field"] == "bench.cell_materials[0]"
    assert evidence["inference"]["relation"] == "inferred from printed Mo oxide ions"
    assert evidence["locator"]["page"] == 18
    assert serialized_notice["reason"] == notice.reason
    from simulator.battery.score import flagged_stratum_payloads

    report_strata = flagged_stratum_payloads((payload,), (Engine.OPENIMCC,))
    assert report_strata == [
        {
            "stratum": "cell-material-inferred",
            "rail": Rail.VAPOUR.value,
            "engine": Engine.OPENIMCC.value,
            "n": 1,
            "median_dex": "0",
            "rms_dex": "0",
        }
    ]
    from simulator.battery.score import _derive_kems_partial_pressure_band

    assert _derive_kems_partial_pressure_band(
        context.observations, context.experiments, context.benches
    ) == residual.numeric.decision_band


def test_inferred_cell_replicates_do_not_move_fallback_band() -> None:
    from simulator.battery.enums import CellMaterial
    from simulator.battery.score import _derive_kems_partial_pressure_band

    inference = Derivation(
        relation="inferred from printed Mo oxide ions",
        inputs=("Mo+;MoO+;MoO2+;MoO3+",),
        parameters=(),
        output_unit="cell material",
    )
    flagged, flagged_context, flagged_bench, _ = _cell_material_score_case(
        CellMaterial.MO, inference
    )
    flagged_exp = flagged_context.experiments[flagged.experiment_id]
    flagged_replica = replace(
        flagged,
        observation_id="inferred-replica",
        value=Value.point_of(Decimal("1e-3")),
    )

    clean_exp = replace(flagged_exp, experiment_id="clean-kems", bench_id="clean-cell")
    clean = replace(
        flagged,
        observation_id="clean-row",
        experiment_id=clean_exp.experiment_id,
        value=Value.point_of(Decimal("1e-6")),
    )
    clean_replica = replace(
        clean,
        observation_id="clean-replica",
        value=Value.point_of(Decimal("2e-6")),
    )
    clean_bench = replace(
        flagged_bench,
        id="clean-cell",
        cell_materials=(F.located(CellMaterial.PT),),
    )
    experiments = {
        flagged_exp.experiment_id: flagged_exp,
        clean_exp.experiment_id: clean_exp,
    }
    clean_rows = {row.observation_id: row for row in (clean, clean_replica)}
    mixed_rows = {
        row.observation_id: row
        for row in (clean, clean_replica, flagged, flagged_replica)
    }

    clean_band = _derive_kems_partial_pressure_band(
        clean_rows, experiments, {clean_bench.id: clean_bench}
    )
    mixed_band = _derive_kems_partial_pressure_band(
        mixed_rows,
        experiments,
        {clean_bench.id: clean_bench, flagged_bench.id: flagged_bench},
    )
    assert clean_band is not None
    assert mixed_band == clean_band
    # The public helper must receive the same bench map as scoring so it can
    # exclude inferred cell materials itself.
    from simulator.battery.score import (
        derive_kems_partial_pressure_band,
        decision_band_for,
    )

    inferred_only = {
        row.observation_id: row for row in (flagged, flagged_replica)
    }
    assert derive_kems_partial_pressure_band(
        inferred_only, {flagged_exp.experiment_id: flagged_exp}
    ) is not None
    assert derive_kems_partial_pressure_band(
        inferred_only,
        {flagged_exp.experiment_id: flagged_exp},
        benches={flagged_bench.id: flagged_bench},
    ) is None
    assert _derive_kems_partial_pressure_band(
        inferred_only,
        {flagged_exp.experiment_id: flagged_exp},
        {flagged_bench.id: flagged_bench},
    ) is None
    assert decision_band_for(
        Quantity.P_PARTIAL,
        SourceRelation.INDEPENDENT,
        rail=Rail.VAPOUR,
        method=MethodToken.KNUDSEN_EFFUSION,
        observations=inferred_only,
        experiments={flagged_exp.experiment_id: flagged_exp},
        derived_band=None,
    ) is None


@pytest.mark.parametrize(
    "relation", ("mm_to_m", "cm_to_m", "identity:m", "extract_inference")
)
@pytest.mark.parametrize("field", ("orifice_area_m2", "orifice_diameter_m"))
def test_derived_apparatus_convenience_value_does_not_raise_inference_notice(
    relation: str,
    field: str,
) -> None:
    from simulator.battery.records import ApparatusGeometry
    from simulator.battery.score import _cell_apparatus_inference_notices

    reference = F.observation(
        "converted-orifice",
        "converted-kems",
        _partial_identity(),
        Decimal("1e-6"),
    )
    conversion = Derivation(
        relation=relation,
        inputs=("printed orifice geometry",),
        parameters=(),
        output_unit="m" if field == "orifice_diameter_m" else "m2",
    )
    area = replace(F.located(Decimal("1e-6")), inference=conversion)
    experiment = replace(
        F.kems_experiment("converted-kems"),
        apparatus=replace(
            F.kems_experiment("converted-kems").apparatus,
            geometry=ApparatusGeometry(**{field: area}),
        ),
    )
    assert _cell_apparatus_inference_notices(reference, experiment, None) == ()


def test_noninferred_cell_material_has_no_inference_notice() -> None:
    from simulator.battery.enums import CellMaterial

    reference, context, _bench, prediction = _cell_material_score_case(
        CellMaterial.MO, None
    )

    residual, _candidate = compile_residual(
        reference,
        Engine.OPENIMCC,
        context=context,
        predict=prediction,
    )

    assert residual.status is not ResidualStatus.REFUSED
    assert residual.numeric is not None
    assert not any(
        item.kind is NoticeKind.CELL_MATERIAL_INFERRED
        for item in residual.notices
    )


def test_derived_effusion_knudsen_number_does_not_raise_apparatus_notice() -> None:
    from simulator.battery.enums import CellMaterial

    reference, context, _bench, prediction = _cell_material_score_case(
        CellMaterial.MO, None
    )
    inference = Derivation(
        relation="inferred orifice Knudsen number",
        inputs=("printed free-molecular description",),
        parameters=(),
        output_unit="dimensionless",
    )
    experiment = context.experiments[reference.experiment_id]
    regime = replace(
        experiment.pressure_environment.regime,
        knudsen_number_orifice=Located(
            State.of(Value.point_of(Decimal("20"))),
            locator=F.loc(page=18, paragraph="Results"),
            inference=inference,
        ),
    )
    experiment = replace(
        experiment,
        pressure_environment=replace(experiment.pressure_environment, regime=regime),
    )
    context = replace(context, experiments={experiment.experiment_id: experiment})

    residual, _candidate = compile_residual(
        reference,
        Engine.OPENIMCC,
        context=context,
        predict=prediction,
    )

    assert not any(
        item.kind is NoticeKind.CELL_MATERIAL_INFERRED
        for item in residual.notices
    )


def test_inferred_nonmodelled_cell_still_refuses_oxygen_balance() -> None:
    from simulator.battery.enums import CellMaterial

    reference, context, _bench, _prediction = _cell_material_score_case(
        CellMaterial.TA,
        Derivation(
            relation="inferred tantalum cell",
            inputs=("printed Ta ion",),
            parameters=(),
            output_unit="cell material",
        ),
    )

    residual, _candidate = compile_residual(
        reference,
        Engine.OPENIMCC,
        context=context,
    )

    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.detail.get("reason") == "reactive_cell_oxygen_reservoir"
    assert any(
        item.kind is NoticeKind.CELL_MATERIAL_INFERRED
        for item in residual.notices
    )


def test_score_store_refuses_synthetic_unknown_reference_activity_point() -> None:
    work = F.work("unknown-activity-reference-fixture")
    experiment = F.tabulation_experiment(
        "unknown-activity-reference-experiment", work.work_id
    )
    identity = replace(
        F.activity_identity(formula="CaO"),
        reference_state=State.unknown("source reference state not printed"),
    )
    observation = F.observation(
        "unknown-activity-reference-fixture::activity",
        experiment.experiment_id,
        identity,
        Decimal("0.42"),
        source_id=work.work_id,
    )
    observation = replace(
        observation,
        evidence=replace(
            observation.evidence,
            class_=State.unknown("method class not established"),
        ),
    )
    context = _context(work, experiment, observation)

    def no_prediction(*args, **kwargs):
        pytest.fail("unknown activity reference state must refuse before prediction")

    residuals, _ = score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        include_diagnostics=True,
        predict=no_prediction,
    )

    assert len(residuals) == 1
    residual = residuals[0]
    assert residual.reference == observation.observation_id
    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.IDENTITY_UNKNOWN
    assert residual.refusal.detail["reason"] == "activity_reference_state_unknown"
    assert residual.refusal.detail["fields"] == ["reference_state"]


def test_score_store_refuses_categorical_stolyarova_figure_only_row() -> None:
    source_id = "stolyarova-1996-cao-alumina-silica-kems"
    context = load_score_context(sources=(source_id,))
    figure_row = next(
        obs
        for obs in context.observations.values()
        if obs.source_id == source_id and obs.value.kind is ValueKind.CATEGORICAL
    )
    figure_context = replace(
        context,
        observations={figure_row.observation_id: figure_row},
        origins={
            figure_row.observation_id: context.origins[figure_row.observation_id]
        }
        if figure_row.observation_id in context.origins
        else {},
    )
    residuals, _ = score_store(
        figure_context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        include_diagnostics=True,
    )

    assert len(residuals) == 1
    refusal = residuals[0].refusal
    assert refusal is not None
    assert refusal.reason is RefusalReason.IDENTITY_UNKNOWN
    assert "figure_only" in refusal.detail["quantity_reason"]
    assert refusal.detail["value_kind"] == ValueKind.CATEGORICAL.value


def test_score_store_records_each_in_scope_observation_in_small_fixture() -> None:
    work = F.work("score-drop-fixture")
    experiment = F.tabulation_experiment(
        "score-drop-fixture-experiment", work.work_id
    )
    activity_identity = replace(
        F.activity_identity(formula="CaO"),
        reference_state=State.unknown("source reference state not printed"),
    )
    activity = F.observation(
        "score-drop-fixture::activity",
        experiment.experiment_id,
        activity_identity,
        Decimal("0.42"),
        evidence=EvidenceClass.MEASURED_REDUCED,
        source_id=work.work_id,
    )
    activity = replace(
        activity,
        evidence=replace(
            activity.evidence,
            class_=State.unknown("unmapped method_class measured_reduced"),
        ),
    )
    figure_identity = replace(
        activity_identity,
        quantity=State.unknown("unsupported quantity 'CaO_activity_figure_only'"),
    )
    figure = F.observation(
        "score-drop-fixture::figure-only",
        experiment.experiment_id,
        figure_identity,
        Decimal("0"),
        evidence=EvidenceClass.FIGURE_ONLY,
        admission=AdmissionStatus.REJECTED,
        source_id=work.work_id,
    )
    figure = replace(
        figure,
        value=Value(
            kind=ValueKind.CATEGORICAL,
            categorical="bound_not_point_ordering",
        ),
    )
    context = _context(work, experiment, activity, figure)

    def no_prediction(*args, **kwargs):
        pytest.fail("fixture observations must refuse before prediction")

    residuals, _ = score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        include_diagnostics=True,
        predict=no_prediction,
    )

    records_by_reference = Counter(row.reference for row in residuals)
    assert records_by_reference.keys() == {activity.observation_id, figure.observation_id}
    assert all(records_by_reference[obs.observation_id] >= 1 for obs in (activity, figure))

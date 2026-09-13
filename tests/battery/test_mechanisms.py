"""Red→green cases for v2.1 §Test specification (M01–M16 + reviewer counters).

Each test name carries the mechanism id. M11 is owner-only; M13 is the
preserved sequence gap. Scoring/engine dispatch is chunk 2: these tests
exercise the schema-level contracts those rows require.
"""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest

from simulator.battery.enums import (
    AdmissionStatus,
    AmountBasis,
    Authority,
    Engine,
    EvidenceClass,
    ExecutionState,
    IdentityEqualKind,
    MethodToken,
    NoticeKind,
    PerBasis,
    Phase,
    Quantity,
    Rail,
    ReferenceStateConvention,
    RefusalReason,
    ResidualStatus,
    SourceRelation,
    StateTag,
)
from simulator.battery.identity import (
    Identity,
    atm_to_pa,
    bar_to_pa,
    celsius_to_kelvin,
    identity_equal,
    kcal_th_to_kJ_per_mol,
    log10K_from_delta_fG_kJ_mol,
    rescale_energy_per_basis,
)
from simulator.battery.records import (
    Annotations,
    Composition,
    Derivation,
    Execution,
    Located,
    Notice,
    ResidualRefusal,
    Species,
    State,
    Uncertainty,
    Value,
    union_notices,
)
from simulator.battery.validate import validate_corpus
from simulator.battery.validity import (
    background_pressure_high,
    effusion_regime_unverified,
    run_validity_gates,
    table_self_consistency,
    underdetermined_apparatus,
)
from tests.battery import factories as F


def test_m01_inconsistent_o2_table_is_invalid_source() -> None:
    """O2 ΔfG=0 and logK=−59.154 → invalid_source; consistent 0/0 passes."""

    bad = table_self_consistency(
        delta_fG_kJ_mol=Decimal("0"),
        log10_Kf=Decimal("-59.154"),
        T_K=Decimal("298.15"),
        per=PerBasis.MOL_SPECIES,
        standard_pressure_Pa=bar_to_pa("1"),
        reaction_id="O2",
    )
    assert bad.passed is False
    assert bad.reason is RefusalReason.INVALID_SOURCE
    assert bad.primary_check == "delta_fG_logK"
    good = table_self_consistency(
        delta_fG_kJ_mol=Decimal("0"),
        log10_Kf=Decimal("0"),
        T_K=Decimal("298.15"),
    )
    assert good.passed is True
    assert good.reason is None


def test_m02_floor_notice_keeps_numeric_but_not_score_eligible() -> None:
    """Floor-tagged vapour residual cannot be score_eligible; diagnostic numeric can.

    Inputs do not carry the answer: the validator must refuse a measured +
    certified pair that claims score_eligible=True with a floor notice.
    """

    ident = F.psat_identity("Na")
    w = F.work()
    exp = F.tabulation_experiment()
    ref = F.observation(
        "ref-m02",
        exp.experiment_id,
        ident,
        Decimal("0.1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    cand = F.engine_obs(
        "cand-m02",
        exp.experiment_id,
        ident,
        Decimal("1"),
        notices=(F.floor_notice(),),
        authority=Authority.CERTIFIED,
    )
    scored = F.residual(
        "m02-scored",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MISMATCH,
        rail=Rail.VAPOUR,
        score_eligible=True,
        notices=union_notices(ref.notices, cand.notices),
    )
    scored_report = validate_corpus([w], [exp], [ref, cand], [scored])
    assert not scored_report.ok
    assert any(
        i.reason is RefusalReason.CONDITIONAL_FIELD and "floor" in i.detail
        for i in scored_report.issues
    )
    diagnostic = F.residual(
        "m02-diagnostic",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MISMATCH,
        rail=Rail.VAPOUR,
        score_eligible=False,
        notices=union_notices(ref.notices, cand.notices),
    )
    diagnostic_report = validate_corpus([w], [exp], [ref, cand], [diagnostic])
    assert diagnostic_report.ok
    assert diagnostic.numeric is not None
    assert any(n.kind is NoticeKind.FLOOR_INVERSION for n in diagnostic.notices)
    clean_cand = F.engine_obs(
        "cand-m02-clean",
        exp.experiment_id,
        ident,
        Decimal("0.1"),
        authority=Authority.CERTIFIED,
    )
    clean = F.residual(
        "m02-clean",
        ref.observation_id,
        candidate=clean_cand.observation_id,
        status=ResidualStatus.MATCH,
        rail=Rail.VAPOUR,
        score_eligible=True,
    )
    assert validate_corpus([w], [exp], [ref, clean_cand], [clean]).ok


def test_m03_cao_liquid_vs_crystal_is_identity_mismatch() -> None:
    liquid = F.cao_identity(Phase.L, T_K=Decimal("1200"))
    crystal = F.cao_identity(Phase.CR, T_K=Decimal("1200"))
    outcome = identity_equal(liquid, crystal)
    assert outcome.kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert "species.phase" in outcome.fields
    control = identity_equal(crystal, F.cao_identity(Phase.CR, T_K=Decimal("1200")))
    assert control.kind is IdentityEqualKind.EQUAL


def test_m04_residual_notice_union_survives_endpoint_ancestry() -> None:
    floor = F.floor_notice("provider")
    later = Notice(
        kind=NoticeKind.EXTRAPOLATION,
        affected_quantities=(Quantity.P_SAT,),
        reason="later clean timestep still carries inherited floor",
        origin="carrier:wall",
    )
    combined = union_notices((floor,), (), (floor, later))
    assert combined == (floor, later)
    ident = F.psat_identity("Na")
    w = F.work()
    exp = F.tabulation_experiment()
    ref = F.observation(
        "ref-m04",
        exp.experiment_id,
        ident,
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    cand = F.engine_obs("cand-m04", exp.experiment_id, ident, Decimal("1"), notices=combined)
    res = F.residual(
        "m04",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        notices=union_notices(ref.notices, cand.notices),
        score_eligible=False,
        rail=Rail.VAPOUR,
    )
    assert any(n.kind is NoticeKind.FLOOR_INVERSION for n in res.notices)
    assert validate_corpus([w], [exp], [ref, cand], [res]).ok


def test_m05_tiny_physical_pressure_is_not_a_floor_by_magnitude() -> None:
    a = F.psat_identity("Na", T_K=Decimal("800"))
    b = F.psat_identity("Na", T_K=Decimal("800"))
    assert identity_equal(a, b).kind is IdentityEqualKind.EQUAL
    # 1e-30 Pa and 1e-25 Pa are different values, not a provenance classifier.
    tiny = F.observation("tiny", "exp-1", a, Decimal("1e-30"))
    assert tiny.value.point == Decimal("1e-30")
    floor = F.floor_notice()
    assert floor.kind is NoticeKind.FLOOR_INVERSION
    assert floor.original == Decimal("1e-40")
    converted_floor = F.observation(
        "converted-floor",
        "exp-1",
        a,
        Decimal("1e-25"),
        notices=(floor,),
    )
    assert converted_floor.notices[0].kind is NoticeKind.FLOOR_INVERSION


def test_m06_clamp_emits_floor_inversion_with_original_and_band() -> None:
    notice = F.floor_notice("adapter:projection")
    assert notice.kind is NoticeKind.FLOOR_INVERSION
    assert notice.original == Decimal("1e-40")
    assert notice.band is not None
    assert notice.reason
    clean = F.observation("clean-m06", "exp-1", F.psat_identity("Na"), Decimal("1"))
    assert clean.notices == ()


def test_m07_wall_identity_and_determinants_required_for_deposit() -> None:
    a = F.wall_deposit_identity(wall_T=Decimal("1673.15"), wall_material="SiO2")
    b = F.wall_deposit_identity(wall_T=Decimal("1773.15"), wall_material="SiO2")
    c = F.wall_deposit_identity(wall_T=Decimal("1673.15"), wall_material="Fe")
    assert identity_equal(a, a).kind is IdentityEqualKind.EQUAL
    assert identity_equal(a, b).kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert identity_equal(a, c).kind is IdentityEqualKind.IDENTITY_MISMATCH
    exp = F.tabulation_experiment(method=MethodToken.VACUUM_CHAMBER_PYROLYSIS)
    gate = underdetermined_apparatus(exp, Quantity.WALL_DEPOSIT_MASS)
    assert gate.passed is False
    assert gate.reason is RefusalReason.UNDERDETERMINED_APPARATUS


def test_m08_out_of_gamma_domain_is_extrapolated_notice_not_certified() -> None:
    ident = F.psat_identity("Na")
    w = F.work()
    exp = F.tabulation_experiment()
    notice = Notice(
        kind=NoticeKind.OUT_OF_GAMMA_DOMAIN,
        affected_quantities=(Quantity.P_SAT,),
        reason="gamma domain [1673,1673] K",
        origin="sf18",
        band="[1673,1673] K",
    )
    certified_with_domain = F.engine_obs(
        "cand-m08-certified",
        exp.experiment_id,
        ident,
        Decimal("1"),
        authority=Authority.CERTIFIED,
        notices=(notice,),
        certified_band={"temperature_K": (Decimal("1673"), Decimal("1673"))},
    )
    certified_report = validate_corpus([w], [exp], [certified_with_domain])
    assert not certified_report.ok
    assert any("extrapolated" in i.detail for i in certified_report.issues)
    extrapolated = F.engine_obs(
        "cand-m08",
        exp.experiment_id,
        ident,
        Decimal("1"),
        authority=Authority.EXTRAPOLATED,
        notices=(notice,),
        certified_band={"temperature_K": (Decimal("1673"), Decimal("1673"))},
    )
    assert validate_corpus([w], [exp], [extrapolated]).ok
    clean = F.engine_obs(
        "cand-m08-clean",
        exp.experiment_id,
        ident,
        Decimal("1"),
        authority=Authority.CERTIFIED,
    )
    assert validate_corpus([w], [exp], [clean]).ok


def test_m09_nao05_scalar_vs_na2o_component_basis_mismatch() -> None:
    a = F.activity_identity(formula="NaO0.5", component_basis="NaO0.5")
    b = F.activity_identity(formula="Na2O", component_basis="Na2O")
    outcome = identity_equal(a, b)
    assert outcome.kind is IdentityEqualKind.IDENTITY_MISMATCH
    control = identity_equal(a, F.activity_identity(formula="NaO0.5", component_basis="NaO0.5"))
    assert control.kind is IdentityEqualKind.EQUAL


def test_m10_liquid_vs_solid_henrian_reference_state_mismatch() -> None:
    liquid = F.activity_identity(
        formula="P2O5",
        convention=ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER,
        endmember_phase=Phase.L,
        component_basis="P2O5",
    )
    henrian_solid = F.activity_identity(
        formula="P2O5",
        convention=ReferenceStateConvention.HENRIAN_SOLID,
        endmember_phase=Phase.CR,
        component_basis="P2O5",
    )
    solid_raoultian = F.activity_identity(
        formula="P2O5",
        convention=ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER,
        endmember_phase=Phase.CR,
        component_basis="P2O5",
    )
    assert identity_equal(liquid, henrian_solid).kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert identity_equal(liquid, solid_raoultian).kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert identity_equal(liquid, liquid).kind is IdentityEqualKind.EQUAL


def test_m12_rejected_admission_cannot_be_score_eligible() -> None:
    ident = F.activity_identity()
    w = F.work()
    exp = F.tabulation_experiment()
    rejected = F.observation(
        "rej-m12",
        exp.experiment_id,
        ident,
        Decimal("0.5"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        admission=AdmissionStatus.REJECTED,
    )
    cand = F.engine_obs("cand-m12", exp.experiment_id, ident, Decimal("0.5"))
    bad = F.residual(
        "m12-bad",
        rejected.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
        rail=Rail.MELT_ACTIVITY,
    )
    report = validate_corpus([w], [exp], [rejected, cand], [bad])
    assert not report.ok
    assert any(i.reason is RefusalReason.ADMISSION_NOT_ADMITTED for i in report.issues)
    admitted = F.observation(
        "adm-m12",
        exp.experiment_id,
        ident,
        Decimal("0.5"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    good = F.residual(
        "m12-good",
        admitted.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
        rail=Rail.MELT_ACTIVITY,
    )
    assert validate_corpus([w], [exp], [admitted, cand], [good]).ok


def test_m14_unavailable_execution_states_are_truthful() -> None:
    ident = F.o2_identity()
    w = F.work()
    exp = F.tabulation_experiment()
    ref = F.observation(
        "ref-m14",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    unsupported = F.residual(
        "m14-unsupported",
        ref.observation_id,
        status=ResidualStatus.REFUSED,
        execution=ExecutionState.UNSUPPORTED,
        refusal=ResidualRefusal(RefusalReason.UNSUPPORTED, {"channel": "melts"}),
        quantity=Quantity.DELTA_FG,
    )
    not_probed = F.residual(
        "m14-not-probed",
        ref.observation_id,
        status=ResidualStatus.REFUSED,
        execution=ExecutionState.NOT_PROBED,
        refusal=ResidualRefusal(RefusalReason.NOT_PROBED, {"channel": "vaporock"}),
    )
    attempted = F.residual(
        "m14-attempted",
        ref.observation_id,
        status=ResidualStatus.REFUSED,
        execution=ExecutionState.ATTEMPTED_UNAVAILABLE,
        refusal=ResidualRefusal(RefusalReason.ATTEMPTED_UNAVAILABLE, {"channel": "cea"}),
    )
    assert attempted.execution.call_evidence
    produced = F.engine_obs("cand-m14", exp.experiment_id, ident, Decimal("0"))
    ok = F.residual(
        "m14-produced",
        ref.observation_id,
        candidate=produced.observation_id,
        status=ResidualStatus.MATCH,
        execution=ExecutionState.PRODUCED,
        score_eligible=True,
    )
    report = validate_corpus(
        [w], [exp], [ref, produced], [unsupported, not_probed, attempted, ok]
    )
    assert report.ok
    missing_call = F.residual(
        "m14-no-call",
        ref.observation_id,
        status=ResidualStatus.REFUSED,
        execution=ExecutionState.ATTEMPTED_UNAVAILABLE,
        refusal=ResidualRefusal(RefusalReason.ATTEMPTED_UNAVAILABLE, {}),
    )
    object.__setattr__(missing_call.execution, "call_evidence", None) if False else None
    broken = ResidualRefusal(RefusalReason.ATTEMPTED_UNAVAILABLE, {})
    from simulator.battery.records import Residual, CandidateRequest, Execution as Ex

    no_evidence = Residual(
        key="m14-silent",
        reference=ref.observation_id,
        execution=Ex(state=ExecutionState.ATTEMPTED_UNAVAILABLE, call_evidence=None),
        rail=Rail.THERMOCHEMISTRY,
        status=ResidualStatus.REFUSED,
        source_relation=SourceRelation.UNKNOWN,
        score_eligible=False,
        exclusions=(),
        notices=(),
        candidate_request=CandidateRequest(exp.experiment_id, Quantity.DELTA_FG, Engine.NASA_CEA_9, "cea"),
        refusal=broken,
    )
    silent = validate_corpus([w], [exp], [ref], [no_evidence])
    assert not silent.ok


def test_m15_growth_is_not_a_census_gate_and_roles_are_not_counts() -> None:
    w = F.work()
    exp = F.tabulation_experiment()
    ident = F.o2_identity()
    rows = [
        F.observation(
            f"row-{i}",
            exp.experiment_id,
            ident,
            Decimal("0"),
            evidence=EvidenceClass.MEASURED_DIRECT,
        )
        for i in range(3)
    ]
    report = validate_corpus([w], [exp], rows)
    assert report.ok
    rows.append(
        F.observation(
            "row-3",
            exp.experiment_id,
            ident,
            Decimal("0"),
            evidence=EvidenceClass.MEASURED_DIRECT,
        )
    )
    assert validate_corpus([w], [exp], rows).ok
    own = F.residual(
        "m15-own",
        rows[0].observation_id,
        candidate=F.engine_obs("eng-m15", exp.experiment_id, ident, Decimal("0")).observation_id,
        status=ResidualStatus.MATCH,
        source_relation=SourceRelation.SAME_INPUT,
        score_eligible=False,
    )
    independent = F.residual(
        "m15-ind",
        rows[1].observation_id,
        candidate="eng-m15",
        status=ResidualStatus.MATCH,
        source_relation=SourceRelation.INDEPENDENT,
        score_eligible=True,
    )
    eng = F.engine_obs("eng-m15", exp.experiment_id, ident, Decimal("0"))
    swapped = validate_corpus([w], [exp], rows + [eng], [own, independent])
    assert swapped.ok
    assert own.source_relation is SourceRelation.SAME_INPUT
    assert independent.source_relation is SourceRelation.INDEPENDENT
    # Role mutation is a provenance predicate, not a count. Swapping labels
    # without swapping coefficient_sources would be a later scorer concern.


def test_m16_refused_residual_cannot_carry_a_numeric_score() -> None:
    ident = F.cao_identity(Phase.CR)
    w = F.work()
    exp = F.tabulation_experiment()
    ref = F.observation("ref-m16", exp.experiment_id, ident, Decimal("0.064"))
    cand = F.engine_obs("cand-m16", exp.experiment_id, ident, Decimal("0.064"))
    refused = F.residual(
        "m16-refused",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.REFUSED,
        refusal=ResidualRefusal(RefusalReason.INVALID_IDENTITY, {"oxide": "Fe2O3"}),
        score_eligible=False,
    )
    assert refused.numeric is None
    assert validate_corpus([w], [exp], [ref, cand], [refused]).ok
    from simulator.battery.records import Residual, ResidualNumeric, DecisionBand
    from simulator.battery.enums import MetricOperation

    forged = Residual(
        key="m16-forged",
        reference=ref.observation_id,
        execution=Execution(state=ExecutionState.PRODUCED),
        rail=Rail.THERMOCHEMISTRY,
        status=ResidualStatus.REFUSED,
        source_relation=SourceRelation.INDEPENDENT,
        score_eligible=True,
        exclusions=(),
        notices=(),
        candidate=cand.observation_id,
        numeric=ResidualNumeric(
            operation=MetricOperation.ABSOLUTE,
            unit="kJ_per_declared_mol_basis",
            value=Decimal("0.064"),
            decision_band=DecisionBand(Decimal("1"), "kJ_per_declared_mol_basis", "x"),
        ),
        refusal=ResidualRefusal(RefusalReason.INVALID_IDENTITY, {}),
    )
    report = validate_corpus([w], [exp], [ref, cand], [forged])
    assert not report.ok


def test_mf_f03_unknown_identity_forbids_numeric_residual() -> None:
    """Unknown/unknown identity is a refused attempt, never a numeric diagnostic."""

    from dataclasses import replace

    ident = F.psat_identity("Na")
    unknown = replace(ident, temperature_K=State.unknown("not printed"))
    w = F.work()
    exp = F.tabulation_experiment()
    ref = F.observation(
        "ref-unknown",
        exp.experiment_id,
        unknown,
        Decimal("0.1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    cand = F.engine_obs("cand-unknown", exp.experiment_id, unknown, Decimal("0.1"))
    numeric = F.residual(
        "unknown-numeric",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=False,
        rail=Rail.VAPOUR,
    )
    report = validate_corpus([w], [exp], [ref, cand], [numeric])
    assert not report.ok
    assert any(i.reason is RefusalReason.IDENTITY_UNKNOWN for i in report.issues)
    refused = F.residual(
        "unknown-refused",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.REFUSED,
        refusal=ResidualRefusal(RefusalReason.IDENTITY_UNKNOWN, {"fields": ["temperature_K"]}),
        score_eligible=False,
        rail=Rail.VAPOUR,
    )
    assert validate_corpus([w], [exp], [ref, cand], [refused]).ok
    known_ref = F.observation(
        "ref-known",
        exp.experiment_id,
        ident,
        Decimal("0.1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    known_cand = F.engine_obs("cand-known", exp.experiment_id, ident, Decimal("0.1"))
    known = F.residual(
        "known-numeric",
        known_ref.observation_id,
        candidate=known_cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
        rail=Rail.VAPOUR,
    )
    assert validate_corpus([w], [exp], [known_ref, known_cand], [known]).ok


def test_m13_sequence_gap_is_preserved() -> None:
    """M13 is not confirmed; do not invent a mechanism or silently promote it."""

    assert not hasattr(RefusalReason, "M13")
    assert "m13" not in {r.value for r in RefusalReason}


def test_sc_f03_pi_p2_4_alpha_ordering_transition_subtypes() -> None:
    kems_alpha = Identity(
        quantity=Quantity.EVAPORATION_COEFFICIENT_ALPHA,
        species=Species("Na", Phase.G),
        subtype=State.of("kems"),
        per=State.of(PerBasis.DIMENSIONLESS),
        temperature_K=State.of(Decimal("1000")),
        reservoir=State.of(Species("Na", Phase.L)),
        composition=State.of(
            Composition(
                "ordered_complete_mole_inventory",
                (("Na", Decimal("1")),),
                AmountBasis.MOLE_FRACTION,
            )
        ),
        fO2_Pa=State.of(Decimal("1e-10")),
        total_pressure_Pa=State.of(Decimal("1e-6")),
        sweep_gas=State.of(
            F.SweepIdentity(
                species="none",
                flow_sccm=State.not_applicable("carrier-free"),
                partial_pressure_Pa=State.not_applicable("carrier-free"),
            )
        ),
        exposure=State.of(
            F.Exposure(area_m2=State.of(Decimal("1e-4")), duration_s=State.of(Decimal("1")))
        ),
        sample_mass_kg=State.not_applicable("alpha is intensive"),
        wall=State.not_applicable("not a deposit"),
        standard_pressure_Pa=State.not_applicable("not thermo p°"),
        reaction=State.not_applicable("not formation"),
        formation_elements=State.not_applicable("not formation"),
        reference_state=State.not_applicable("not activity"),
    )
    langmuir = Identity(**{**kems_alpha.__dict__, "subtype": State.of("langmuir")})
    assert identity_equal(kems_alpha, langmuir).kind is IdentityEqualKind.IDENTITY_MISMATCH
    melt = Identity(
        quantity=Quantity.TRANSITION_TEMPERATURE,
        species=Species("Na", Phase.CR, polymorph=State.of("bcc")),
        subtype=State.of("melting"),
        total_pressure_Pa=State.of(Decimal("101325")),
        composition=State.of(
            Composition(
                "ordered_complete_mole_inventory",
                (("Na", Decimal("1")),),
                AmountBasis.MOLE_FRACTION,
            )
        ),
        fO2_Pa=State.not_applicable("pure Na melting is not redox"),
        temperature_K=State.not_applicable("T is the result"),
        per=State.not_applicable("temperature quantity"),
        standard_pressure_Pa=State.not_applicable("not a thermo table"),
        reaction=State.not_applicable("not formation"),
        formation_elements=State.not_applicable("not formation"),
        reference_state=State.not_applicable("not activity"),
        reservoir=State.not_applicable("not vapour"),
        sweep_gas=State.not_applicable("not kinetic"),
        exposure=State.not_applicable("not kinetic"),
        sample_mass_kg=State.not_applicable("not extensive"),
        wall=State.not_applicable("not deposit"),
    )
    boil = Identity(**{**melt.__dict__, "subtype": State.of("boiling")})
    assert identity_equal(melt, boil).kind is IdentityEqualKind.IDENTITY_MISMATCH
    from simulator.battery.enums import ValueKind

    bound = Value(kind=ValueKind.BOUND, bound_operator="<", bound_value=Decimal("1"))
    assert bound.kind is ValueKind.BOUND
    assert bound.point is None


def test_sc_f04_omitted_wall_or_reservoir_refuses() -> None:
    complete = F.wall_deposit_identity()
    missing_wall = Identity(**{**complete.__dict__, "wall": State.unknown("page did not state wall")})
    outcome = identity_equal(complete, missing_wall)
    assert outcome.kind is IdentityEqualKind.IDENTITY_UNKNOWN
    olivine = Identity(
        **{
            **complete.__dict__,
            "reservoir": State.of(Species("Mg2SiO4", Phase.CR, polymorph=State.of("forsterite"))),
        }
    )
    assert identity_equal(complete, olivine).kind is IdentityEqualKind.IDENTITY_MISMATCH
    exp = F.kems_experiment(orifice_area=None, clausing=None)
    gate = underdetermined_apparatus(exp, Quantity.P_SAT)
    assert gate.reason is RefusalReason.UNDERDETERMINED_APPARATUS


def test_sc_f05_pi_p1_4_hematite_annotations_do_not_compare() -> None:
    ident = F.oxide_identity(
        "Fe2O3",
        Phase.CR,
        metal_formula="Fe",
        nu_metal=Fraction(2),
        nu_o2=Fraction("3/2"),
        polymorph="hematite",
        T_K=Decimal("1000"),
    )
    w = F.work()
    exp = F.tabulation_experiment()
    a = F.observation(
        "hem-a",
        exp.experiment_id,
        ident,
        Decimal("1"),
        annotations=Annotations(space_group="R-3c", crystal_system="hexagonal", transition_note="above Curie 960 K"),
    )
    b = F.observation("hem-b", exp.experiment_id, ident, Decimal("1"))
    assert identity_equal(a.identity, b.identity).kind is IdentityEqualKind.EQUAL
    quartz_generic = F.oxide_identity(
        "SiO2",
        Phase.CR,
        metal_formula="Si",
        nu_metal=Fraction(1),
        nu_o2=Fraction(1),
        polymorph="quartz",
        T_K=Decimal("800"),
    )
    quartz_alpha = F.oxide_identity(
        "SiO2",
        Phase.CR,
        metal_formula="Si",
        nu_metal=Fraction(1),
        nu_o2=Fraction(1),
        polymorph="alpha-quartz",
        T_K=Decimal("800"),
    )
    assert identity_equal(quartz_generic, quartz_alpha).kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert validate_corpus([w], [exp], [a, b]).ok


def test_pi_p1_1_formation_elements_na_liquid_vs_gas() -> None:
    liquid = F.na2o_identity(Phase.L, T_K=Decimal("1700"))
    gas = F.na2o_identity(Phase.G, T_K=Decimal("1700"))
    outcome = identity_equal(liquid, gas)
    assert outcome.kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert "formation_elements" in outcome.fields or "species.phase" in str(outcome.fields)
    # Same product Na2O(l) but elemental Na(l) vs Na(g).
    assert liquid.species.phase is gas.species.phase
    assert outcome.fields == ("formation_elements",) or "formation_elements" in outcome.fields


def test_pi_p1_2_pref_is_not_psat() -> None:
    pref = F.pref_identity()
    psat = F.psat_identity("Na", T_K=Decimal("1156"))
    assert pref.quantity is Quantity.P_REFERENCE
    assert psat.quantity is Quantity.P_SAT
    assert identity_equal(pref, psat).kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert identity_equal(psat, F.psat_identity("Na", T_K=Decimal("1156"))).kind is IdentityEqualKind.EQUAL


def test_pi_p1_3_p2o5_vs_p4o10_component_basis() -> None:
    p2o5 = F.activity_identity(formula="P2O5", component_basis="P2O5", endmember_phase=Phase.L)
    p4o10 = F.activity_identity(formula="P4O10", component_basis="P4O10", endmember_phase=Phase.CR)
    assert identity_equal(p2o5, p4o10).kind is IdentityEqualKind.IDENTITY_MISMATCH


def test_pi_p1_5_fo2_channel_is_not_identity() -> None:
    a = F.activity_identity(fO2_Pa=Decimal("1e-8"))
    b = F.activity_identity(fO2_Pa=Decimal("1e-8"))
    assert identity_equal(a, b).kind is IdentityEqualKind.EQUAL
    from simulator.battery.records import FO2Control
    from simulator.battery.enums import FO2Channel

    F.tabulation_experiment(
        experiment_id="buf",
        fO2_control=FO2Control(channel=State.of(FO2Channel.BUFFER), buffer=F.located("IW-2")),
    )
    F.tabulation_experiment(
        experiment_id="cmd",
        fO2_control=FO2Control(channel=State.of(FO2Channel.COMMANDED)),
    )
    # Channel lives on Experiment, not Identity.
    assert identity_equal(a, b).equal


def test_pi_p1_6_projected_pot_changes_identity() -> None:
    full = Composition(
        "ordered_complete_mole_inventory",
        (("SiO2", Decimal("0.48")), ("NaO0.5", Decimal("0.50")), ("P2O5", Decimal("0.02"))),
        AmountBasis.MOLE_FRACTION,
    )
    dropped = Composition(
        "ordered_complete_mole_inventory",
        (("SiO2", Decimal("0.4898")), ("NaO0.5", Decimal("0.5102"))),
        AmountBasis.MOLE_FRACTION,
    )
    a = F.activity_identity(composition=full)
    b = F.activity_identity(composition=dropped)
    assert identity_equal(a, b).kind is IdentityEqualKind.IDENTITY_MISMATCH
    notice = Notice(
        kind=NoticeKind.COMPOSITION_PROJECTED,
        affected_quantities=(Quantity.ACTIVITY,),
        reason="dropped P2O5",
        origin="engine:magemin",
        dropped=("P2O5",),
        dropped_mass_fraction=Decimal("0.0152"),
    )
    w = F.work()
    exp = F.tabulation_experiment()
    ref = F.observation("ref-pot", exp.experiment_id, a, Decimal("0.1"), evidence=EvidenceClass.MEASURED_DIRECT)
    cand = F.engine_obs(
        "cand-pot",
        exp.experiment_id,
        b,
        Decimal("0.1"),
        notices=(notice,),
        requested=full,
    )
    res = F.residual(
        "pi-p1-6",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.REFUSED,
        refusal=ResidualRefusal(RefusalReason.IDENTITY_MISMATCH, {"fields": ["composition"]}),
        notices=union_notices(cand.notices),
        score_eligible=False,
        rail=Rail.MELT_ACTIVITY,
    )
    assert cand.engine is not None
    assert cand.engine.requested_composition is not None
    assert cand.engine.requested_composition.is_value
    assert validate_corpus([w], [exp], [ref, cand], [res]).ok


def test_pi_p2_2_p2_3_bar_equals_1e5_pa_kelvin_is_exact() -> None:
    a = F.o2_identity()
    b = F.o2_identity()
    assert a.standard_pressure_Pa is not None and a.standard_pressure_Pa.value == Decimal("100000")
    atm = F.o2_identity()
    from dataclasses import replace

    atm = replace(atm, standard_pressure_Pa=State.of(atm_to_pa("1")))
    assert identity_equal(a, atm).kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert bar_to_pa("1") == Decimal("100000")
    assert celsius_to_kelvin(Decimal("1370")) == Decimal("1643.15")
    t_a = replace(a, temperature_K=State.of(celsius_to_kelvin("1370")))
    t_b = replace(a, temperature_K=State.of(Decimal("1643")))
    assert identity_equal(t_a, t_b).kind is IdentityEqualKind.IDENTITY_MISMATCH
    t_c = replace(a, temperature_K=State.of(Decimal("1643.15")))
    assert identity_equal(t_a, t_c).kind is IdentityEqualKind.EQUAL


def test_sc_f06_fedkin_lineage_resolves_and_incomplete_is_unknown() -> None:
    w = F.work()
    exp = F.tabulation_experiment()
    ident = F.psat_identity("Na")
    raw = F.observation("raw-fedkin", exp.experiment_id, ident, Decimal("1"), evidence=EvidenceClass.MEASURED_DIRECT)
    derived = F.observation(
        "fit-a",
        exp.experiment_id,
        ident,
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_REDUCED,
        derived_from=("raw-fedkin",),
        derivation=Derivation(
            relation="fit",
            inputs=("raw-fedkin",),
            parameters=(),
            output_unit="Pa",
        ),
    )
    report = validate_corpus([w], [exp], [raw, derived])
    assert report.ok
    orphan = F.observation(
        "fit-orphan",
        exp.experiment_id,
        ident,
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_REDUCED,
        derived_from=("missing-parent",),
        derivation=Derivation(relation="fit", inputs=("missing-parent",), parameters=(), output_unit="Pa"),
    )
    assert not validate_corpus([w], [exp], [raw, orphan]).ok


def test_sc_f07_own_input_numeric_diagnostic_is_not_empirical_score() -> None:
    ident = F.o2_identity()
    w = F.work()
    exp = F.tabulation_experiment()
    cea = F.observation(
        "cea-row",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
    )
    eng = F.engine_obs("cea-eval", exp.experiment_id, ident, Decimal("0"))
    res = F.residual(
        "own-input",
        cea.observation_id,
        candidate=eng.observation_id,
        status=ResidualStatus.MATCH,
        source_relation=SourceRelation.SAME_INPUT,
        score_eligible=False,
    )
    report = validate_corpus([w], [exp], [cea, eng], [res])
    assert report.ok
    assert res.numeric is not None
    assert res.score_eligible is False
    bad = F.residual(
        "own-input-scored",
        cea.observation_id,
        candidate=eng.observation_id,
        status=ResidualStatus.MATCH,
        source_relation=SourceRelation.SAME_INPUT,
        score_eligible=True,
    )
    assert not validate_corpus([w], [exp], [cea, eng], [bad]).ok


def test_sc_f08_residual_key_is_stable_and_units_are_not_inherited() -> None:
    ident = F.o2_identity()
    w = F.work()
    exp = F.tabulation_experiment()
    ref = F.observation(
        "ref-pin",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    cand = F.engine_obs("cand-pin", exp.experiment_id, ident, Decimal("0"))
    res = F.residual(
        "janaf:O2:delta_fG:298.15:thermochemistry",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    assert res.key == "janaf:O2:delta_fG:298.15:thermochemistry"
    assert res.numeric is not None
    assert res.numeric.unit == res.numeric.decision_band.unit
    assert validate_corpus([w], [exp], [ref, cand], [res]).ok


def test_sc_f09_structured_value_is_not_collapsed() -> None:
    from simulator.battery.enums import ValueKind

    expr = Value(
        kind=ValueKind.EXPRESSION,
        expression_text="A - B/T",
        expression_parameters=(("A", Decimal("10")), ("B", Decimal("20000"))),
        expression_domain="900-1600 K",
    )
    series = Value(
        kind=ValueKind.SERIES,
        series=((Decimal("1000"), Decimal("1.2")), (Decimal("1100"), Decimal("2.4"))),
    )
    assert expr.kind is ValueKind.EXPRESSION
    assert expr.point is None
    assert series.kind is ValueKind.SERIES
    unc = Uncertainty(kind=__import__("simulator.battery.enums", fromlist=["UncertaintyKind"]).UncertaintyKind.PRINTED, verbatim="±0.02")
    assert unc.verbatim == "±0.02"


def test_sc_f11_metric_domain_and_none_uncertainty() -> None:
    from simulator.battery.enums import ValueKind, UncertaintyKind

    point = Value.point_of(Decimal("0"))
    none = Uncertainty(kind=UncertaintyKind.NONE)
    assert point.kind is ValueKind.POINT
    assert none.kind is UncertaintyKind.NONE
    bound = Value(kind=ValueKind.BOUND, bound_operator="<", bound_value=Decimal("1"))
    assert bound.kind is not ValueKind.POINT


def test_mf_f01_f02_f03_f05_f06_default_free_validator() -> None:
    w = F.work()
    exp = F.tabulation_experiment()
    ident = F.o2_identity()
    pending = F.observation(
        "pending",
        exp.experiment_id,
        ident,
        Decimal("0"),
        admission=AdmissionStatus.PENDING,
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    cand = F.engine_obs("eng-pending", exp.experiment_id, ident, Decimal("0"))
    scored = F.residual(
        "pending-scored",
        pending.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    assert not validate_corpus([w], [exp], [pending, cand], [scored]).ok
    cr = Species("Fe2O3", Phase.CR, polymorph=State.unknown("page did not resolve form"))
    unknown_poly = F.oxide_identity(
        "Fe2O3",
        Phase.CR,
        metal_formula="Fe",
        nu_metal=Fraction(2),
        nu_o2=Fraction("3/2"),
        polymorph="hematite",
    )
    unknown_poly = Identity(**{**unknown_poly.__dict__, "species": cr})
    other = F.oxide_identity(
        "Fe2O3",
        Phase.CR,
        metal_formula="Fe",
        nu_metal=Fraction(2),
        nu_o2=Fraction("3/2"),
        polymorph="hematite",
    )
    assert identity_equal(unknown_poly, other).kind is IdentityEqualKind.IDENTITY_UNKNOWN


def test_mf_f04_f16_scoped_notices_and_explicit_apparatus_gate() -> None:
    exp = F.kems_experiment(orifice_area=None, clausing=Decimal("0.9"))
    assert underdetermined_apparatus(exp, Quantity.P_SAT).reason is RefusalReason.UNDERDETERMINED_APPARATUS
    complete = F.kems_experiment()
    assert underdetermined_apparatus(complete, Quantity.P_SAT).passed
    assert effusion_regime_unverified(complete, Quantity.P_SAT).passed
    high_bg = F.kems_experiment(total_P=Decimal("1"))
    assert background_pressure_high(high_bg, Quantity.P_SAT).reason is RefusalReason.BACKGROUND_PRESSURE_HIGH
    kinetic = F.kems_experiment(total_P=Decimal("100"))
    # millibar kinetic / yield is outside this gate
    assert background_pressure_high(kinetic, Quantity.MASS_LOSS_FRACTION).passed
    unknown_kn = F.kems_experiment(kn=None)
    assert effusion_regime_unverified(unknown_kn, Quantity.P_SAT).reason is RefusalReason.EFFUSION_REGIME_UNVERIFIED
    low_kn = F.kems_experiment(kn=Decimal("0.5"))
    assert effusion_regime_unverified(low_kn, Quantity.P_SAT).reason is RefusalReason.EFFUSION_REGIME_UNVERIFIED
    from dataclasses import replace as _replace

    from simulator.battery.records import Located as _Located

    unknown_bg = _replace(
        F.kems_experiment(),
        pressure_environment=_replace(
            F.kems_experiment().pressure_environment,
            total_pressure_Pa=_Located(State.unknown("not printed"), locator=F.loc()),
        ),
    )
    unknown_effusion = effusion_regime_unverified(unknown_bg, Quantity.P_SAT)
    assert unknown_effusion.passed is False
    assert unknown_effusion.reason is RefusalReason.EFFUSION_REGIME_UNVERIFIED
    ident_psat = F.psat_identity("Na")
    obs_psat = F.observation("kems-unknown-bg", unknown_bg.experiment_id, ident_psat, Decimal("1"))
    combined = run_validity_gates(unknown_bg, obs_psat)
    assert combined.passed is False
    assert combined.reason is RefusalReason.EFFUSION_REGIME_UNVERIFIED
    stated = F.kems_experiment(total_P=Decimal("1e-6"))
    assert effusion_regime_unverified(stated, Quantity.P_SAT).passed
    assert background_pressure_high(stated, Quantity.P_SAT).passed
    assert run_validity_gates(
        stated, F.observation("kems-stated-bg", stated.experiment_id, ident_psat, Decimal("1"))
    ).passed


def test_physics_false_refuse_compilation_not_applicable_axes_equal() -> None:
    """Legit compilation pairs with not_applicable fields must not refuse."""

    a = F.o2_identity()
    b = F.o2_identity()
    assert a.composition is not None and a.composition.tag is StateTag.NOT_APPLICABLE
    assert identity_equal(a, b).kind is IdentityEqualKind.EQUAL
    cp = Identity(
        quantity=Quantity.CP,
        species=Species("O2", Phase.G),
        per=State.of(PerBasis.MOL_SPECIES),
        temperature_K=State.of(Decimal("298.15")),
        standard_pressure_Pa=State.of(bar_to_pa("1")),
        composition=State.not_applicable("pure standard cp"),
        fO2_Pa=State.not_applicable("pure standard cp"),
        sweep_gas=State.not_applicable("pure standard cp"),
        reaction=State.not_applicable("pure standard cp"),
        formation_elements=State.not_applicable("pure standard cp"),
        reference_state=State.not_applicable("pure standard cp"),
        reservoir=State.not_applicable("pure standard cp"),
        total_pressure_Pa=State.not_applicable("pure standard cp"),
        exposure=State.not_applicable("pure standard cp"),
        sample_mass_kg=State.not_applicable("pure standard cp"),
        wall=State.not_applicable("pure standard cp"),
        subtype=State.not_applicable("pure standard cp"),
    )
    assert identity_equal(cp, cp).kind is IdentityEqualKind.EQUAL
    w = F.work()
    exp = F.tabulation_experiment()
    obs_a = F.observation("cp-a", exp.experiment_id, cp, Decimal("29.4"))
    obs_b = F.observation("cp-b", exp.experiment_id, cp, Decimal("29.4"))
    assert validate_corpus([w], [exp], [obs_a, obs_b]).ok
    filled = Identity(**{**cp.__dict__, "composition": State.of(
        Composition("ordered_complete_mole_inventory", (("O2", Decimal("1")),), AmountBasis.MOLE_FRACTION)
    )})
    assert identity_equal(cp, filled).kind is IdentityEqualKind.INVALID_IDENTITY
    from dataclasses import replace

    extra_p = replace(
        F.psat_identity("Na"),
        standard_pressure_Pa=State.of(Decimal("100000")),
    )
    extra_vs_extra = identity_equal(extra_p, extra_p)
    assert extra_vs_extra.kind is IdentityEqualKind.INVALID_IDENTITY
    assert "standard_pressure_Pa" in extra_vs_extra.fields
    w_psat = F.work()
    exp_psat = F.tabulation_experiment()
    extra_obs = F.observation("psat-extra-p", exp_psat.experiment_id, extra_p, Decimal("1"))
    extra_report = validate_corpus([w_psat], [exp_psat], [extra_obs])
    assert not extra_report.ok
    assert any(i.reason is RefusalReason.INVALID_IDENTITY for i in extra_report.issues)
    clean_psat = F.psat_identity("Na")
    assert identity_equal(clean_psat, F.psat_identity("Na")).kind is IdentityEqualKind.EQUAL

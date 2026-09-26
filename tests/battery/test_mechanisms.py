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
    AssetRole,
    Authority,
    Engine,
    EvidenceClass,
    ExecutionState,
    ExperimentKind,
    IdentityEqualKind,
    MethodToken,
    MetricOperation,
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
    ValueKind,
)
from simulator.battery.identity import (
    Identity,
    WallIdentity,
    atm_to_pa,
    bar_to_pa,
    celsius_to_kelvin,
    identity_equal,
    kcal_th_to_kJ_per_mol,
    log10K_from_delta_fG_kJ_mol,
    quantity_token,
    rescale_energy_per_basis,
)
from simulator.battery.records import (
    Admission,
    Annotations,
    CandidateRequest,
    Composition,
    Derivation,
    Execution,
    Located,
    Notice,
    Reaction,
    ReactionTerm,
    Residual,
    ResidualRefusal,
    SourceFile,
    Species,
    StandardState,
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
    """O2 ΔfG=0 and logK=−59.154 → invalid_source before comparison; 0/0 passes."""

    from dataclasses import replace

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

    ident_g = F.o2_identity()
    ident_k = replace(ident_g, quantity=Quantity.LOG10_KF)
    w = F.work()
    exp = F.tabulation_experiment()
    dg = F.observation(
        "o2-dg",
        exp.experiment_id,
        ident_g,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    lk_bad = F.observation(
        "o2-lk-bad",
        exp.experiment_id,
        ident_k,
        Decimal("-59.154"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    cand = F.engine_obs("o2-eng", exp.experiment_id, ident_g, Decimal("0"))
    scored = F.residual(
        "m01-scored",
        dg.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    scored_report = validate_corpus([w], [exp], [dg, lk_bad, cand], [scored])
    assert not scored_report.ok
    assert any(i.reason is RefusalReason.INVALID_SOURCE for i in scored_report.issues)
    refused = F.residual(
        "m01-refused",
        dg.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.REFUSED,
        refusal=ResidualRefusal(
            RefusalReason.INVALID_SOURCE,
            {"gate": "table"},
            check_refs=("delta_fG_logK",),
        ),
        score_eligible=False,
    )
    assert validate_corpus([w], [exp], [dg, lk_bad, cand], [refused]).ok
    lk_good = F.observation(
        "o2-lk-good",
        exp.experiment_id,
        ident_k,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    consistent = F.residual(
        "m01-consistent",
        dg.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    assert validate_corpus([w], [exp], [dg, lk_good, cand], [consistent]).ok


def test_r01_engine_sibling_cannot_mask_printed_table_inconsistency() -> None:
    """Printed ΔfG/logK pairing ignores engine observations and keeps every printed pair.

    Codex F01: inserting an engine logK=0 before a printed logK=-59.154 must not
    repair the invalid source. Distinct p° are distinct identity points.
    """

    from dataclasses import replace

    ident_g = F.o2_identity()
    ident_k = replace(ident_g, quantity=Quantity.LOG10_KF)
    w = F.work()
    exp = F.tabulation_experiment()
    ref = F.observation(
        "r01-ref",
        exp.experiment_id,
        ident_g,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    cand = F.engine_obs("r01-cand", exp.experiment_id, ident_g, Decimal("0"))
    bad_log = F.observation(
        "r01-bad-logK",
        exp.experiment_id,
        ident_k,
        Decimal("-59.154"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    engine_log = F.engine_obs("r01-engine-logK", exp.experiment_id, ident_k, Decimal("0"))
    scored = F.residual(
        "r01-scored",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    printed_only = validate_corpus([w], [exp], [ref, bad_log, cand], [scored])
    assert not printed_only.ok
    assert any(i.reason is RefusalReason.INVALID_SOURCE for i in printed_only.issues)
    engine_first = validate_corpus(
        [w], [exp], [ref, engine_log, bad_log, cand], [scored]
    )
    assert not engine_first.ok
    assert any(i.reason is RefusalReason.INVALID_SOURCE for i in engine_first.issues)
    good_log = F.observation(
        "r01-good-logK",
        exp.experiment_id,
        ident_k,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    all_printed = validate_corpus(
        [w], [exp], [ref, good_log, bad_log, cand], [scored]
    )
    assert not all_printed.ok
    other_pressure = replace(
        bad_log,
        observation_id="r01-bad-101325",
        identity=replace(ident_k, standard_pressure_Pa=State.of(Decimal("101325"))),
    )
    distinct_p = validate_corpus([w], [exp], [ref, other_pressure, cand], [scored])
    assert distinct_p.ok
    consistent = F.residual(
        "r01-consistent",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    assert validate_corpus([w], [exp], [ref, good_log, cand], [consistent]).ok


def test_r01_failed_gate_refusal_must_carry_invalid_source_and_check_evidence() -> None:
    """A refused residual for a failed table gate cannot hide behind another reason."""

    from dataclasses import replace

    ident_g = F.o2_identity()
    ident_k = replace(ident_g, quantity=Quantity.LOG10_KF)
    w = F.work()
    exp = F.tabulation_experiment()
    ref = F.observation(
        "r01-ref-reason",
        exp.experiment_id,
        ident_g,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    cand = F.engine_obs("r01-cand-reason", exp.experiment_id, ident_g, Decimal("0"))
    bad_log = F.observation(
        "r01-bad-reason-logK",
        exp.experiment_id,
        ident_k,
        Decimal("-59.154"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    wrong = F.residual(
        "r01-wrong-reason",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.REFUSED,
        score_eligible=False,
        refusal=ResidualRefusal(RefusalReason.IDENTITY_MISMATCH, {}),
    )
    report = validate_corpus([w], [exp], [ref, bad_log, cand], [wrong])
    assert not report.ok
    assert any(i.reason is RefusalReason.INVALID_SOURCE for i in report.issues)
    correct = F.residual(
        "r01-correct-reason",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.REFUSED,
        score_eligible=False,
        refusal=ResidualRefusal(
            RefusalReason.INVALID_SOURCE,
            {"gate": "delta_fG_logK"},
            check_refs=("delta_fG_logK",),
        ),
    )
    assert validate_corpus([w], [exp], [ref, bad_log, cand], [correct]).ok


def test_r10_failed_gate_refusal_requires_failed_check_evidence() -> None:
    """Codex F02: invalid_source with empty or foreign check evidence is invalid.

    A nonempty detail string is not failed-check evidence. The checks tuple
    must name the primary table check.
    """

    from dataclasses import replace

    ident_g = F.o2_identity()
    ident_k = replace(ident_g, quantity=Quantity.LOG10_KF)
    w = F.work()
    exp = F.tabulation_experiment()
    ref = F.observation(
        "r10-ref",
        exp.experiment_id,
        ident_g,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    cand = F.engine_obs("r10-cand", exp.experiment_id, ident_g, Decimal("0"))
    bad_log = F.observation(
        "r10-bad-logK",
        exp.experiment_id,
        ident_k,
        Decimal("-59.154"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )

    def refused(key: str, refusal: ResidualRefusal) -> Residual:
        return F.residual(
            key,
            ref.observation_id,
            candidate=cand.observation_id,
            status=ResidualStatus.REFUSED,
            score_eligible=False,
            refusal=refusal,
        )

    foreign_detail = refused(
        "r10-foreign-detail",
        ResidualRefusal(RefusalReason.INVALID_SOURCE, {"gate": "made-up-unrelated-check"}),
    )
    foreign_report = validate_corpus([w], [exp], [ref, bad_log, cand], [foreign_detail])
    assert not foreign_report.ok
    assert any(i.reason is RefusalReason.INVALID_SOURCE for i in foreign_report.issues)
    empty_refs = refused(
        "r10-empty-refs",
        ResidualRefusal(
            RefusalReason.INVALID_SOURCE,
            {"gate": "delta_fG_logK"},
            check_refs=(),
        ),
    )
    empty_report = validate_corpus([w], [exp], [ref, bad_log, cand], [empty_refs])
    assert not empty_report.ok
    foreign_refs = refused(
        "r10-foreign-refs",
        ResidualRefusal(
            RefusalReason.INVALID_SOURCE,
            {"gate": "delta_fG_logK"},
            check_refs=("made-up-unrelated-check",),
        ),
    )
    foreign_refs_report = validate_corpus([w], [exp], [ref, bad_log, cand], [foreign_refs])
    assert not foreign_refs_report.ok
    wrong_reason = refused(
        "r10-wrong-reason-valid-refs",
        ResidualRefusal(
            RefusalReason.IDENTITY_MISMATCH,
            {"gate": "delta_fG_logK"},
            check_refs=("delta_fG_logK",),
        ),
    )
    wrong_report = validate_corpus([w], [exp], [ref, bad_log, cand], [wrong_reason])
    assert not wrong_report.ok
    assert any(i.reason is RefusalReason.INVALID_SOURCE for i in wrong_report.issues)
    correct = refused(
        "r10-correct-refs",
        ResidualRefusal(
            RefusalReason.INVALID_SOURCE,
            {"gate": "delta_fG_logK"},
            check_refs=("delta_fG_logK",),
        ),
    )
    assert validate_corpus([w], [exp], [ref, bad_log, cand], [correct]).ok


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
    assert diagnostic.notices[0].original == Decimal("1e-40")
    assert Quantity.P_SAT in diagnostic.notices[0].affected_quantities
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
    from dataclasses import replace

    floor = F.floor_notice("provider")
    later = Notice(
        kind=NoticeKind.DERIVATION_USES_COMPILATION,
        affected_quantities=(Quantity.P_SAT,),
        reason="later clean timestep still carries inherited floor",
        origin="carrier:wall",
    )
    combined = union_notices((floor,), (), (floor, later))
    assert combined == (floor, later)
    distinct_original = replace(floor, original=Decimal("1e-50"), band="different-band")
    assert union_notices((floor,), (distinct_original,)) == (floor, distinct_original)
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
    dropped = F.residual(
        "m04-dropped",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        notices=(),
        score_eligible=False,
        rail=Rail.VAPOUR,
    )
    dropped_report = validate_corpus([w], [exp], [ref, cand], [dropped])
    assert not dropped_report.ok
    assert any("endpoint notices" in i.detail for i in dropped_report.issues)


def test_m05_tiny_physical_pressure_is_not_a_floor_by_magnitude() -> None:
    a = F.psat_identity("Na", T_K=Decimal("800"))
    b = F.psat_identity("Na", T_K=Decimal("800"))
    assert identity_equal(a, b).kind is IdentityEqualKind.EQUAL
    w = F.work()
    exp = F.tabulation_experiment()
    tiny = F.observation(
        "tiny",
        exp.experiment_id,
        a,
        Decimal("1e-30"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    clean_cand = F.engine_obs("tiny-cand", exp.experiment_id, a, Decimal("1e-30"))
    clean = F.residual(
        "m05-clean",
        tiny.observation_id,
        candidate=clean_cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
        rail=Rail.VAPOUR,
    )
    assert validate_corpus([w], [exp], [tiny, clean_cand], [clean]).ok
    floor = F.floor_notice()
    assert floor.original == Decimal("1e-40")
    assert Quantity.P_SAT in floor.affected_quantities
    floor_cand = F.engine_obs(
        "floor-cand",
        exp.experiment_id,
        a,
        Decimal("1e-25"),
        notices=(floor,),
    )
    scored_floor = F.residual(
        "m05-floor-scored",
        tiny.observation_id,
        candidate=floor_cand.observation_id,
        status=ResidualStatus.MISMATCH,
        score_eligible=True,
        notices=union_notices(floor_cand.notices),
        rail=Rail.VAPOUR,
    )
    assert not validate_corpus([w], [exp], [tiny, floor_cand], [scored_floor]).ok
    diagnostic = F.residual(
        "m05-floor-diagnostic",
        tiny.observation_id,
        candidate=floor_cand.observation_id,
        status=ResidualStatus.MISMATCH,
        score_eligible=False,
        notices=union_notices(floor_cand.notices),
        rail=Rail.VAPOUR,
    )
    assert validate_corpus([w], [exp], [tiny, floor_cand], [diagnostic]).ok


def test_m06_clamp_emits_floor_inversion_with_original_and_band() -> None:
    from dataclasses import replace

    notice = F.floor_notice("adapter:projection")
    ident = F.psat_identity("Na")
    w = F.work()
    exp = F.tabulation_experiment()
    complete = F.observation(
        "floor-complete",
        exp.experiment_id,
        ident,
        Decimal("1e-25"),
        notices=(notice,),
    )
    assert validate_corpus([w], [exp], [complete]).ok
    incomplete = replace(notice, original=None, band=None)
    missing = F.observation(
        "floor-incomplete",
        exp.experiment_id,
        ident,
        Decimal("1e-25"),
        notices=(incomplete,),
    )
    missing_report = validate_corpus([w], [exp], [missing])
    assert not missing_report.ok
    assert any("original and band" in i.detail for i in missing_report.issues)
    clean = F.observation("clean-m06", exp.experiment_id, ident, Decimal("1"))
    assert clean.notices == ()
    assert validate_corpus([w], [exp], [clean]).ok


def test_m07_apparatus_rejects_unknown_calibration_and_requires_flux_area() -> None:
    """Calibrated KEMS pressure is geometry-free; flux still needs orifice area."""

    from dataclasses import replace

    from simulator.battery.records import Apparatus, ApparatusGeometry, Located

    ke = F.kems_experiment()
    bad_geometry = ApparatusGeometry(
        orifice_area_m2=Located(State.of(Decimal("-1")), locator=F.loc()),
        clausing_factor=Located(State.of(Decimal("0")), locator=F.loc()),
    )
    unknown_cal = {"constant": Located(State.unknown("not known"), locator=F.loc())}
    bad = replace(
        ke,
        apparatus=Apparatus(geometry=bad_geometry, calibration=unknown_cal),
    )
    bad_gate = underdetermined_apparatus(bad, Quantity.P_SAT)
    assert bad_gate.passed is False
    assert bad_gate.reason is RefusalReason.UNDERDETERMINED_APPARATUS
    assert bad_gate.primary_check == "kems_calibration"
    check = next(c for c in bad_gate.checks if c.name == "kems_calibration")
    assert check.detail["missing"] == ["calibration"]
    tga = F.tabulation_experiment(method=MethodToken.TGA)
    tga_gate = underdetermined_apparatus(tga, Quantity.MASS_LOSS_RATE)
    assert tga_gate.passed is False
    assert tga_gate.reason is RefusalReason.UNDERDETERMINED_APPARATUS
    complete = F.kems_experiment()
    assert underdetermined_apparatus(complete, Quantity.P_SAT).passed
    assert run_validity_gates(
        complete,
        F.observation("kems-ok-geom", complete.experiment_id, F.psat_identity("Na"), Decimal("1")),
    ).passed


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
    w = F.work()
    ref = F.observation(
        "wall-ref",
        exp.experiment_id,
        a,
        Decimal("0.001"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    cand = F.engine_obs("wall-cand", exp.experiment_id, a, Decimal("0.001"))
    scored = F.residual(
        "m07-scored",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
        rail=Rail.WALL_DEPOSITION,
    )
    scored_report = validate_corpus([w], [exp], [ref, cand], [scored])
    assert not scored_report.ok
    assert any(i.reason is RefusalReason.UNDERDETERMINED_APPARATUS for i in scored_report.issues)
    refused = F.residual(
        "m07-refused",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.REFUSED,
        refusal=ResidualRefusal(
            RefusalReason.UNDERDETERMINED_APPARATUS,
            {"missing": ["wall"]},
            check_refs=("geometry_determinants",),
        ),
        score_eligible=False,
        rail=Rail.WALL_DEPOSITION,
    )
    assert validate_corpus([w], [exp], [ref, cand], [refused]).ok


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
    same_species = F.activity_identity(formula="Na2O", component_basis="NaO0.5")
    other_basis = F.activity_identity(formula="Na2O", component_basis="Na2O")
    basis_only = identity_equal(same_species, other_basis)
    assert basis_only.kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert identity_equal(same_species, F.activity_identity(formula="Na2O", component_basis="NaO0.5")).kind is IdentityEqualKind.EQUAL


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
    assert identity_equal(solid_raoultian, solid_raoultian).kind is IdentityEqualKind.EQUAL
    convention_only = F.activity_identity(
        formula="P2O5",
        convention=ReferenceStateConvention.HENRIAN_SOLID,
        endmember_phase=Phase.CR,
        component_basis="P2O5",
    )
    assert identity_equal(solid_raoultian, convention_only).kind is IdentityEqualKind.IDENTITY_MISMATCH
    from dataclasses import replace as _replace_ss

    from simulator.battery.records import StandardState as _SS

    bare_endmember = Species("P2O5", Phase.CR)
    bare_ss = _SS(
        convention=ReferenceStateConvention.HENRIAN_SOLID,
        endmember=bare_endmember,
        component_basis="P2O5",
        reference_pressure_bar=Decimal("1"),
    )
    incomplete = _replace_ss(henrian_solid, reference_state=State.of(bare_ss))
    nested_unknown = identity_equal(incomplete, incomplete)
    assert nested_unknown.kind is IdentityEqualKind.IDENTITY_UNKNOWN


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
    from dataclasses import replace

    own_input_eng = replace(
        eng,
        observation_id="eng-m15-own-input",
        engine=replace(eng.engine, coefficient_sources=(rows[1].observation_id,)),
    )
    relabelled = F.residual(
        "m15-relabelled",
        rows[1].observation_id,
        candidate=own_input_eng.observation_id,
        status=ResidualStatus.MATCH,
        source_relation=SourceRelation.INDEPENDENT,
        score_eligible=True,
    )
    role_swap = validate_corpus([w], [exp], rows + [own_input_eng], [relabelled])
    assert not role_swap.ok
    assert any("coefficient_sources" in i.detail or "lineage" in i.detail for i in role_swap.issues)
    labelled_same = F.residual(
        "m15-same-input-ok",
        rows[1].observation_id,
        candidate=own_input_eng.observation_id,
        status=ResidualStatus.MATCH,
        source_relation=SourceRelation.SAME_INPUT,
        score_eligible=False,
    )
    assert validate_corpus([w], [exp], rows + [own_input_eng], [labelled_same]).ok
    same_work = replace(
        eng,
        observation_id="eng-m15-same-work",
        engine=replace(eng.engine, coefficient_sources=("janaf-4th",)),
    )
    same_work_res = F.residual(
        "m15-same-work-ok",
        rows[1].observation_id,
        candidate=same_work.observation_id,
        status=ResidualStatus.MATCH,
        source_relation=SourceRelation.INDEPENDENT,
        score_eligible=True,
    )
    same_work_report = validate_corpus([w], [exp], rows + [same_work], [same_work_res])
    assert not same_work_report.ok
    assert any(
        "coefficient_sources" in i.detail or "lineage" in i.detail
        for i in same_work_report.issues
    )


def test_r02_lineage_overlap_is_observation_table_not_work_or_asset() -> None:
    """Independence is resolved observation/table lineage, not work/source/asset sets.

    Codex F02: coefficient_sources=(reference.observation_id,) and
    lineage_complete=False cannot score; a different point in the same work can.
    """

    from dataclasses import replace

    w = F.work()
    exp = F.tabulation_experiment()
    ident = F.o2_identity()
    ref = F.observation(
        "r02-ref",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    cand = F.engine_obs("r02-cand", exp.experiment_id, ident, Decimal("0"))
    scored = F.residual(
        "r02-scored",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    assert validate_corpus([w], [exp], [ref, cand], [scored]).ok
    circular = replace(
        cand,
        observation_id="r02-circular",
        engine=replace(cand.engine, coefficient_sources=(ref.observation_id,)),
    )
    circular_res = F.residual(
        "r02-circular",
        ref.observation_id,
        candidate=circular.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    circular_report = validate_corpus([w], [exp], [ref, circular], [circular_res])
    assert not circular_report.ok
    incomplete = replace(
        cand,
        observation_id="r02-incomplete",
        engine=replace(cand.engine, lineage_complete=False),
    )
    incomplete_res = F.residual(
        "r02-incomplete",
        ref.observation_id,
        candidate=incomplete.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    incomplete_report = validate_corpus([w], [exp], [ref, incomplete], [incomplete_res])
    assert not incomplete_report.ok
    same_work = replace(
        cand,
        observation_id="r02-same-work",
        engine=replace(cand.engine, coefficient_sources=(w.work_id,)),
    )
    same_work_res = F.residual(
        "r02-same-work",
        ref.observation_id,
        candidate=same_work.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    same_work_report = validate_corpus([w], [exp], [ref, same_work], [same_work_res])
    assert not same_work_report.ok
    assert any(
        "coefficient_sources" in i.detail or "lineage" in i.detail
        for i in same_work_report.issues
    )
    asset = replace(
        cand,
        observation_id="r02-asset",
        engine=replace(cand.engine, coefficient_sources=("pdf-1",)),
    )
    asset_res = F.residual(
        "r02-asset",
        ref.observation_id,
        candidate=asset.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    asset_report = validate_corpus([w], [exp], [ref, asset], [asset_res])
    assert not asset_report.ok
    assert any(
        "coefficient_sources" in i.detail or "lineage" in i.detail
        for i in asset_report.issues
    )


def _r08_scored_o2(*, observation_id: str, coefficient_sources: tuple[str, ...], T_K=None):
    from dataclasses import replace

    w = F.work()
    exp = F.tabulation_experiment()
    ident = F.o2_identity() if T_K is None else F.o2_identity(T_K=T_K)
    ref = F.observation(
        observation_id,
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    cand = F.engine_obs("r08-cand", exp.experiment_id, ident, Decimal("0"))
    cand = replace(
        cand,
        observation_id=f"{observation_id}-cand",
        engine=replace(cand.engine, coefficient_sources=coefficient_sources),
    )
    scored = F.residual(
        f"{observation_id}-scored",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    return w, exp, ref, cand, scored


def test_r08_unresolvable_coefficient_source_is_unknown_not_independent() -> None:
    """Codex F01: an unregistered string is incomplete → unknown, never independence."""

    w, exp, ref, cand, scored = _r08_scored_o2(
        observation_id="r08-unresolved",
        coefficient_sources=("missing-observation-or-table",),
    )
    report = validate_corpus([w], [exp], [ref, cand], [scored])
    assert not report.ok
    assert any(i.reason is RefusalReason.LINEAGE_UNKNOWN for i in report.issues)
    control_w, control_exp, control_ref, control_cand, control_scored = _r08_scored_o2(
        observation_id="r08-unresolved-control",
        coefficient_sources=("nasa-cea-thermo",),
    )
    assert validate_corpus(
        [control_w], [control_exp], [control_ref, control_cand], [control_scored]
    ).ok


def test_r08_work_id_or_pdf_alias_is_not_resolved_independence() -> None:
    """Work/file aliases expand to registered inputs; they are not independence."""

    w, exp, ref, cand, scored = _r08_scored_o2(
        observation_id="r08-work-alias",
        coefficient_sources=(F.work().work_id,),
    )
    work_report = validate_corpus([w], [exp], [ref, cand], [scored])
    assert not work_report.ok
    pdf_w, pdf_exp, pdf_ref, pdf_cand, pdf_scored = _r08_scored_o2(
        observation_id="r08-pdf-alias",
        coefficient_sources=("pdf-1",),
    )
    pdf_report = validate_corpus([pdf_w], [pdf_exp], [pdf_ref, pdf_cand], [pdf_scored])
    assert not pdf_report.ok
    control_w, control_exp, control_ref, control_cand, control_scored = _r08_scored_o2(
        observation_id="r08-alias-control",
        coefficient_sources=("nasa-cea-thermo",),
    )
    assert validate_corpus(
        [control_w], [control_exp], [control_ref, control_cand], [control_scored]
    ).ok


def test_r08_consumed_table_via_read_from_refuses_independence() -> None:
    """Reference lineage includes the TABLE_CSV consumed through read_from."""

    from dataclasses import replace

    w = F.work()
    table = SourceFile(
        asset_id="shared-table",
        role=AssetRole.TABLE_CSV,
        path="shared-table.csv",
        sha256=State.of("table"),
    )
    w = replace(w, source_files=replace(w.source_files, files=w.source_files.files + (table,)))
    exp = F.tabulation_experiment()
    ident = F.o2_identity()
    ref = replace(
        F.observation(
            "r08-shared-ref",
            exp.experiment_id,
            ident,
            Decimal("0"),
            evidence=EvidenceClass.MEASURED_DIRECT,
        ),
        read_from="shared-table",
    )
    cand = F.engine_obs("r08-shared-cand", exp.experiment_id, ident, Decimal("0"))
    cand = replace(
        cand,
        engine=replace(cand.engine, coefficient_sources=("shared-table",)),
    )
    scored = F.residual(
        "r08-shared-scored",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    report = validate_corpus([w], [exp], [ref, cand], [scored])
    assert not report.ok
    disjoint = replace(
        cand,
        observation_id="r08-shared-control-cand",
        engine=replace(cand.engine, coefficient_sources=("nasa-cea-thermo",)),
    )
    control = F.residual(
        "r08-shared-control",
        ref.observation_id,
        candidate=disjoint.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    assert validate_corpus([w], [exp], [ref, disjoint], [control]).ok


def test_r08_nested_derived_from_parent_is_not_independent() -> None:
    """Grok F01: overlap through a parent that is not the compared observation id."""

    from dataclasses import replace

    w = F.work()
    exp = F.tabulation_experiment()
    ident = F.o2_identity()
    parent = F.observation(
        "raw-parent",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    ref = F.observation(
        "r08-nested-ref",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        derived_from=("raw-parent",),
    )
    cand = F.engine_obs("r08-nested-cand", exp.experiment_id, ident, Decimal("0"))
    cand = replace(
        cand,
        engine=replace(cand.engine, coefficient_sources=("raw-parent",)),
    )
    scored = F.residual(
        "r08-nested-scored",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    report = validate_corpus([w], [exp], [parent, ref, cand], [scored])
    assert not report.ok
    control_cand = replace(
        cand,
        observation_id="r08-nested-control-cand",
        engine=replace(cand.engine, coefficient_sources=("nasa-cea-thermo",)),
    )
    control = F.residual(
        "r08-nested-control",
        ref.observation_id,
        candidate=control_cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    assert validate_corpus([w], [exp], [parent, ref, control_cand], [control]).ok


def test_r08_nested_derivation_input_parent_is_not_independent() -> None:
    """Recursive derivation.inputs must be in the reference lineage on their own."""

    from dataclasses import replace

    w = F.work()
    exp = F.tabulation_experiment()
    ident = F.o2_identity()
    parent = F.observation(
        "raw-parent-input",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    unrelated = F.observation(
        "unrelated-raw",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    ref = F.observation(
        "r08-input-ref",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_REDUCED,
        derived_from=("unrelated-raw",),
        derivation=Derivation(
            relation="ion-to-pressure",
            inputs=("raw-parent-input",),
            parameters=(),
            output_unit="kJ_per_declared_mol_basis",
        ),
    )
    cand = F.engine_obs("r08-input-cand", exp.experiment_id, ident, Decimal("0"))
    cand = replace(
        cand,
        engine=replace(cand.engine, coefficient_sources=("raw-parent-input",)),
    )
    scored = F.residual(
        "r08-input-scored",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    report = validate_corpus([w], [exp], [parent, unrelated, ref, cand], [scored])
    assert not report.ok
    control_cand = replace(
        cand,
        observation_id="r08-input-control-cand",
        engine=replace(cand.engine, coefficient_sources=("nasa-cea-thermo",)),
    )
    control = F.residual(
        "r08-input-control",
        ref.observation_id,
        candidate=control_cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    assert validate_corpus(
        [w], [exp], [parent, unrelated, ref, control_cand], [control]
    ).ok


def test_r08_same_work_different_temperature_point_may_score() -> None:
    """Independence is a second observation at another T, not a Work id substitution."""

    from dataclasses import replace

    w = F.work()
    exp = F.tabulation_experiment()
    ident = F.o2_identity()
    other_ident = F.o2_identity(T_K=Decimal("400"))
    ref = F.observation(
        "r08-point-ref",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    other = F.observation(
        "r08-point-400K",
        exp.experiment_id,
        other_ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    cand = F.engine_obs("r08-point-cand", exp.experiment_id, ident, Decimal("0"))
    cand = replace(
        cand,
        engine=replace(cand.engine, coefficient_sources=(other.observation_id,)),
    )
    scored = F.residual(
        "r08-point-scored",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    assert validate_corpus([w], [exp], [ref, other, cand], [scored]).ok
    circular = replace(
        cand,
        observation_id="r08-point-circular",
        engine=replace(cand.engine, coefficient_sources=(ref.observation_id,)),
    )
    circular_res = F.residual(
        "r08-point-circular",
        ref.observation_id,
        candidate=circular.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    circular_report = validate_corpus([w], [exp], [ref, other, circular], [circular_res])
    assert not circular_report.ok


def _r03_vapour_pair():
    ident = F.psat_identity("Na")
    w = F.work()
    exp = F.tabulation_experiment()
    ref = F.observation(
        "r03-vap-ref",
        exp.experiment_id,
        ident,
        Decimal("0.1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    return ident, w, exp, ref


def test_r03_fallback_notice_blocks_vapour_score_eligible() -> None:
    ident, w, exp, ref = _r03_vapour_pair()
    notice = Notice(
        kind=NoticeKind.FALLBACK,
        affected_quantities=(Quantity.P_SAT,),
        reason="fallback producer",
        origin="adapter",
        source="melt",
        destination="vacuum-floor",
    )
    cand = F.engine_obs(
        "r03-fallback-cand",
        exp.experiment_id,
        ident,
        Decimal("1"),
        notices=(notice,),
    )
    scored = F.residual(
        "r03-fallback-scored",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MISMATCH,
        rail=Rail.VAPOUR,
        score_eligible=True,
        notices=union_notices(cand.notices),
    )
    report = validate_corpus([w], [exp], [ref, cand], [scored])
    assert not report.ok
    clean = F.engine_obs("r03-fallback-clean", exp.experiment_id, ident, Decimal("0.1"))
    control = F.residual(
        "r03-fallback-clean",
        ref.observation_id,
        candidate=clean.observation_id,
        status=ResidualStatus.MATCH,
        rail=Rail.VAPOUR,
        score_eligible=True,
    )
    assert validate_corpus([w], [exp], [ref, clean], [control]).ok


def test_r03_pressure_provenance_unknown_blocks_vapour_score_eligible() -> None:
    ident, w, exp, ref = _r03_vapour_pair()
    notice = Notice(
        kind=NoticeKind.PRESSURE_PROVENANCE_UNKNOWN,
        affected_quantities=(Quantity.P_SAT,),
        reason="provenance unknown",
        origin="candidate",
    )
    cand = F.engine_obs(
        "r03-prov-cand",
        exp.experiment_id,
        ident,
        Decimal("1"),
        notices=(notice,),
    )
    scored = F.residual(
        "r03-prov-scored",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MISMATCH,
        rail=Rail.VAPOUR,
        score_eligible=True,
        notices=union_notices(cand.notices),
    )
    assert not validate_corpus([w], [exp], [ref, cand], [scored]).ok
    clean = F.engine_obs("r03-prov-clean", exp.experiment_id, ident, Decimal("0.1"))
    control = F.residual(
        "r03-prov-clean",
        ref.observation_id,
        candidate=clean.observation_id,
        status=ResidualStatus.MATCH,
        rail=Rail.VAPOUR,
        score_eligible=True,
    )
    assert validate_corpus([w], [exp], [ref, clean], [control]).ok


def test_r03_floor_inversion_blocks_regardless_of_original_magnitude() -> None:
    from dataclasses import replace

    ident, w, exp, ref = _r03_vapour_pair()
    floor = replace(F.floor_notice(), original=Decimal("1e-60"))
    cand = F.engine_obs(
        "r03-tiny-floor-cand",
        exp.experiment_id,
        ident,
        Decimal("1e-25"),
        notices=(floor,),
    )
    scored = F.residual(
        "r03-tiny-floor-scored",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MISMATCH,
        rail=Rail.VAPOUR,
        score_eligible=True,
        notices=union_notices(cand.notices),
    )
    assert not validate_corpus([w], [exp], [ref, cand], [scored]).ok
    clean = F.engine_obs("r03-tiny-floor-clean", exp.experiment_id, ident, Decimal("0.1"))
    control = F.residual(
        "r03-tiny-floor-clean",
        ref.observation_id,
        candidate=clean.observation_id,
        status=ResidualStatus.MATCH,
        rail=Rail.VAPOUR,
        score_eligible=True,
    )
    assert validate_corpus([w], [exp], [ref, clean], [control]).ok


def test_r03_floor_notice_with_original_requires_band() -> None:
    from dataclasses import replace

    ident, w, exp, ref = _r03_vapour_pair()
    missing_band = replace(F.floor_notice(), band=None)
    obs = F.observation(
        "r03-floor-no-band",
        exp.experiment_id,
        ident,
        Decimal("1e-25"),
        notices=(missing_band,),
    )
    report = validate_corpus([w], [exp], [obs])
    assert not report.ok
    assert any("original and band" in i.detail for i in report.issues)
    complete = F.observation(
        "r03-floor-band-ok",
        exp.experiment_id,
        ident,
        Decimal("1e-25"),
        notices=(F.floor_notice(),),
    )
    assert validate_corpus([w], [exp], [complete]).ok


def test_r03_out_of_certified_band_forbids_certified() -> None:
    ident, w, exp, _ref = _r03_vapour_pair()
    notice = Notice(
        kind=NoticeKind.OUT_OF_CERTIFIED_BAND,
        affected_quantities=(Quantity.P_SAT,),
        reason="outside certified temperature domain",
        origin="candidate",
        band="1000..1100 K",
    )
    certified = F.engine_obs(
        "r03-band-certified",
        exp.experiment_id,
        ident,
        Decimal("1"),
        authority=Authority.CERTIFIED,
        notices=(notice,),
        certified_band={"temperature_K": (Decimal("1000"), Decimal("1100"))},
    )
    report = validate_corpus([w], [exp], [certified])
    assert not report.ok
    assert any("extrapolated" in i.detail for i in report.issues)
    extrapolated = F.engine_obs(
        "r03-band-extrapolated",
        exp.experiment_id,
        ident,
        Decimal("1"),
        authority=Authority.EXTRAPOLATED,
        notices=(notice,),
        certified_band={"temperature_K": (Decimal("1000"), Decimal("1100"))},
    )
    assert validate_corpus([w], [exp], [extrapolated]).ok
    clean = F.engine_obs("r03-band-clean", exp.experiment_id, ident, Decimal("1"))
    assert validate_corpus([w], [exp], [clean]).ok


def test_r03_superseded_admission_cannot_score() -> None:
    from dataclasses import replace

    ident = F.activity_identity()
    w = F.work()
    exp = F.tabulation_experiment()
    kept = F.observation(
        "r03-kept",
        exp.experiment_id,
        ident,
        Decimal("0.5"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    superseded = F.observation(
        "r03-superseded",
        exp.experiment_id,
        ident,
        Decimal("0.5"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        admission=AdmissionStatus.SUPERSEDED,
    )
    superseded = replace(
        superseded,
        admission=replace(superseded.admission, superseded_by=kept.observation_id),
    )
    cand = F.engine_obs("r03-sup-cand", exp.experiment_id, ident, Decimal("0.5"))
    bad = F.residual(
        "r03-sup-scored",
        superseded.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
        rail=Rail.MELT_ACTIVITY,
    )
    report = validate_corpus([w], [exp], [kept, superseded, cand], [bad])
    assert not report.ok
    assert any(i.reason is RefusalReason.ADMISSION_NOT_ADMITTED for i in report.issues)
    good = F.residual(
        "r03-sup-control",
        kept.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
        rail=Rail.MELT_ACTIVITY,
    )
    assert validate_corpus([w], [exp], [kept, superseded, cand], [good]).ok


def test_r03_produced_execution_requires_candidate() -> None:
    ident = F.o2_identity()
    w = F.work()
    exp = F.tabulation_experiment()
    ref = F.observation(
        "r03-prod-ref",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    missing = Residual(
        key="r03-produced-missing",
        reference=ref.observation_id,
        execution=Execution(state=ExecutionState.PRODUCED),
        rail=Rail.THERMOCHEMISTRY,
        status=ResidualStatus.REFUSED,
        source_relation=SourceRelation.UNKNOWN,
        score_eligible=False,
        exclusions=(),
        notices=(),
        candidate=None,
        candidate_request=CandidateRequest(
            exp.experiment_id, Quantity.DELTA_FG, Engine.NASA_CEA_9, "cea"
        ),
        refusal=ResidualRefusal(RefusalReason.ATTEMPTED_UNAVAILABLE, {"channel": "cea"}),
    )
    report = validate_corpus([w], [exp], [ref], [missing])
    assert not report.ok
    assert any("produced execution requires candidate" in i.detail for i in report.issues)
    cand = F.engine_obs("r03-prod-cand", exp.experiment_id, ident, Decimal("0"))
    control = F.residual(
        "r03-produced-ok",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    assert validate_corpus([w], [exp], [ref, cand], [control]).ok


def test_r03_numeric_branch_forbids_refusal_payload() -> None:
    from dataclasses import replace

    ident = F.o2_identity()
    w = F.work()
    exp = F.tabulation_experiment()
    ref = F.observation(
        "r03-num-ref",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    cand = F.engine_obs("r03-num-cand", exp.experiment_id, ident, Decimal("0"))
    match = F.residual(
        "r03-num-match",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    assert validate_corpus([w], [exp], [ref, cand], [match]).ok
    with_refusal = replace(
        match,
        key="r03-num-refusal",
        refusal=ResidualRefusal(RefusalReason.IDENTITY_MISMATCH, {"fields": ["quantity"]}),
    )
    report = validate_corpus([w], [exp], [ref, cand], [with_refusal])
    assert not report.ok
    assert any("numeric branch forbids refusal" in i.detail for i in report.issues)


def test_r03_match_mismatch_requires_numeric() -> None:
    ident = F.o2_identity()
    w = F.work()
    exp = F.tabulation_experiment()
    ref = F.observation(
        "r03-match-ref",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    cand = F.engine_obs("r03-match-cand", exp.experiment_id, ident, Decimal("0"))
    missing = Residual(
        key="r03-match-no-numeric",
        reference=ref.observation_id,
        execution=Execution(state=ExecutionState.PRODUCED),
        rail=Rail.THERMOCHEMISTRY,
        status=ResidualStatus.MATCH,
        source_relation=SourceRelation.INDEPENDENT,
        score_eligible=False,
        exclusions=(),
        notices=(),
        candidate=cand.observation_id,
        numeric=None,
        refusal=None,
    )
    report = validate_corpus([w], [exp], [ref, cand], [missing])
    assert not report.ok
    assert any("match/mismatch requires numeric" in i.detail for i in report.issues)
    control = F.residual(
        "r03-match-numeric-ok",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    assert validate_corpus([w], [exp], [ref, cand], [control]).ok


def test_r03_no_band_requires_numeric_and_forbids_refusal() -> None:
    from dataclasses import replace

    ident = F.o2_identity()
    w = F.work()
    exp = F.tabulation_experiment()
    ref = F.observation(
        "r03-no-band-ref",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    cand = F.engine_obs("r03-no-band-cand", exp.experiment_id, ident, Decimal("0"))
    missing = F.residual(
        "r03-no-band-no-numeric",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.NO_BAND,
        score_eligible=True,
    )
    report = validate_corpus([w], [exp], [ref, cand], [missing])
    assert not report.ok
    assert any("no_band requires numeric" in i.detail for i in report.issues)

    numeric = F.residual(
        "r03-no-band-numeric-seed",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    valid = replace(
        numeric,
        key="r03-no-band-numeric",
        status=ResidualStatus.NO_BAND,
        numeric=replace(numeric.numeric, decision_band=None),
    )
    assert validate_corpus([w], [exp], [ref, cand], [valid]).ok

    with_refusal = replace(
        valid,
        key="r03-no-band-refusal",
        refusal=ResidualRefusal(RefusalReason.IDENTITY_MISMATCH, {"fields": ["quantity"]}),
    )
    report = validate_corpus([w], [exp], [ref, cand], [with_refusal])
    assert not report.ok
    assert any("numeric branch forbids refusal" in i.detail for i in report.issues)


def test_r03_score_eligible_requires_measured_evidence() -> None:
    ident = F.o2_identity()
    w = F.work()
    exp = F.tabulation_experiment()
    compiled = F.observation(
        "r03-compiled",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
    )
    cand = F.engine_obs("r03-meas-cand", exp.experiment_id, ident, Decimal("0"))
    bad = F.residual(
        "r03-compiled-scored",
        compiled.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    report = validate_corpus([w], [exp], [compiled, cand], [bad])
    assert not report.ok
    assert any("measured_*" in i.detail for i in report.issues)
    measured = F.observation(
        "r03-measured",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    good = F.residual(
        "r03-measured-scored",
        measured.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    assert validate_corpus([w], [exp], [measured, cand], [good]).ok


def test_r03_no_output_residual_requires_candidate_request() -> None:
    ident = F.o2_identity()
    w = F.work()
    exp = F.tabulation_experiment()
    ref = F.observation(
        "r03-req-ref",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    missing = Residual(
        key="r03-no-request",
        reference=ref.observation_id,
        execution=Execution(state=ExecutionState.UNSUPPORTED),
        rail=Rail.THERMOCHEMISTRY,
        status=ResidualStatus.REFUSED,
        source_relation=SourceRelation.UNKNOWN,
        score_eligible=False,
        exclusions=(),
        notices=(),
        candidate=None,
        candidate_request=None,
        refusal=ResidualRefusal(RefusalReason.UNSUPPORTED, {"channel": "melts"}),
    )
    report = validate_corpus([w], [exp], [ref], [missing])
    assert not report.ok
    assert any("candidate_request" in i.detail for i in report.issues)
    control = F.residual(
        "r03-request-ok",
        ref.observation_id,
        status=ResidualStatus.REFUSED,
        execution=ExecutionState.UNSUPPORTED,
        refusal=ResidualRefusal(RefusalReason.UNSUPPORTED, {"channel": "melts"}),
    )
    assert control.candidate_request is not None
    assert validate_corpus([w], [exp], [ref], [control]).ok


def test_r03_p4o10_component_basis_difference_is_mismatch() -> None:
    p2o5 = F.activity_identity(formula="P2O5", component_basis="P2O5")
    p4o10 = F.activity_identity(formula="P2O5", component_basis="P4O10")
    outcome = identity_equal(p2o5, p4o10)
    assert outcome.kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert identity_equal(
        p2o5, F.activity_identity(formula="P2O5", component_basis="P2O5")
    ).kind is IdentityEqualKind.EQUAL


def test_r03_reference_pressure_bar_difference_is_mismatch() -> None:
    from dataclasses import replace

    a = F.activity_identity()
    ss = a.reference_state.value
    assert isinstance(ss, StandardState)
    b = replace(
        a,
        reference_state=State.of(replace(ss, reference_pressure_bar=Decimal("1.01325"))),
    )
    assert identity_equal(a, b).kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert identity_equal(a, F.activity_identity()).kind is IdentityEqualKind.EQUAL


def test_r03_value_branch_exclusivity_without_nan() -> None:
    with pytest.raises(ValueError):
        Value(
            kind=ValueKind.POINT,
            point=Decimal("1"),
            bound_operator="<",
            bound_value=Decimal("1"),
        )
    assert Value.point_of(Decimal("1")).point == Decimal("1")


def test_r03_composition_nonnegativity_without_duplicate_keys() -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        Composition(
            "ordered_complete_mole_inventory",
            (("Na2O", Decimal("-0.1")),),
            AmountBasis.MOLE_FRACTION,
        )
    control = Composition(
        "ordered_complete_mole_inventory",
        (("Na2O", Decimal("0.1")),),
        AmountBasis.MOLE_FRACTION,
    )
    assert control.components[0][1] == Decimal("0.1")


def test_r03_decided_admission_requires_decided_by() -> None:
    from dataclasses import replace

    ident = F.o2_identity()
    w = F.work()
    exp = F.tabulation_experiment()
    admitted = F.observation("r03-decided-ok", exp.experiment_id, ident, Decimal("0"))
    assert admitted.admission.decided_by is not None
    assert validate_corpus([w], [exp], [admitted]).ok
    missing = replace(
        admitted,
        observation_id="r03-decided-missing",
        admission=Admission(AdmissionStatus.ADMITTED, "canonical", decided_by=None),
    )
    report = validate_corpus([w], [exp], [missing])
    assert not report.ok
    assert any("decided_by" in i.detail for i in report.issues)


def test_r03_source_id_membership_with_valid_read_from() -> None:
    from dataclasses import replace

    ident = F.o2_identity()
    w = F.work()
    exp = F.tabulation_experiment()
    ok = F.observation("r03-src-ok", exp.experiment_id, ident, Decimal("0"))
    assert ok.read_from == "pdf-1"
    assert validate_corpus([w], [exp], [ok]).ok
    bad = replace(ok, observation_id="r03-src-bad", source_id="not-in-work")
    report = validate_corpus([w], [exp], [bad])
    assert not report.ok
    assert any("source_id" in i.detail for i in report.issues)


def test_r03_fo2_reconciliation_vs_experiment_point_conditions() -> None:
    from dataclasses import replace

    ident = F.activity_identity(fO2_Pa=Decimal("1e-8"))
    w = F.work()
    exp = F.tabulation_experiment(T_K=ident.temperature_K.value)
    exp = replace(
        exp,
        conditions={
            "temperature_K": F.located(ident.temperature_K.value),
            "fO2_Pa": F.located(Decimal("1e-8")),
        },
    )
    matching = F.observation(
        "r03-fo2-ok",
        exp.experiment_id,
        ident,
        Decimal("0.5"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    assert validate_corpus([w], [exp], [matching]).ok
    disagreed = replace(
        matching,
        observation_id="r03-fo2-bad",
        identity=replace(ident, fO2_Pa=State.of(Decimal("1e-2"))),
    )
    assert disagreed.point_conditions is not None
    assert "fO2_Pa" in disagreed.point_conditions
    assert "total_pressure_Pa" in disagreed.point_conditions
    report = validate_corpus([w], [exp], [disagreed])
    assert not report.ok
    assert any(i.reason is RefusalReason.INVALID_IDENTITY for i in report.issues)
    fo2_issues = [i for i in report.issues if i.reason is RefusalReason.INVALID_IDENTITY]
    assert any("fO2_Pa" in i.path for i in fo2_issues)
    assert not any("total_pressure_Pa" in i.path for i in fo2_issues)


def test_r03_union_fingerprint_honours_from_to_dropped_and_fraction() -> None:
    fallback_a = Notice(
        kind=NoticeKind.FALLBACK,
        affected_quantities=(Quantity.P_SAT,),
        reason="fallback producer",
        origin="adapter",
        source="from-a",
        destination="to-a",
    )
    fallback_b = Notice(
        kind=NoticeKind.FALLBACK,
        affected_quantities=(Quantity.P_SAT,),
        reason="fallback producer",
        origin="adapter",
        source="from-b",
        destination="to-b",
    )
    assert union_notices((fallback_a,), (fallback_b,)) == (fallback_a, fallback_b)
    assert union_notices((fallback_a,), (fallback_a,)) == (fallback_a,)
    projected_a = Notice(
        kind=NoticeKind.COMPOSITION_PROJECTED,
        affected_quantities=(Quantity.ACTIVITY,),
        reason="dropped oxide",
        origin="engine",
        dropped=("P2O5",),
        dropped_mass_fraction=Decimal("0.0152"),
    )
    projected_b = Notice(
        kind=NoticeKind.COMPOSITION_PROJECTED,
        affected_quantities=(Quantity.ACTIVITY,),
        reason="dropped oxide",
        origin="engine",
        dropped=("P2O5",),
        dropped_mass_fraction=Decimal("0.02"),
    )
    assert union_notices((projected_a,), (projected_b,)) == (projected_a, projected_b)


def test_r09_union_fingerprint_isolates_each_constituent() -> None:
    """Each fingerprint field is independently identity: original, band, source,
    destination, dropped, and reason. Clean identical notices still collapse.
    """

    from dataclasses import replace

    floor = F.floor_notice()
    original_only = replace(floor, original=Decimal("1e-50"))
    band_only = replace(floor, band="[1e-20, 1] bar")
    assert union_notices((floor,), (original_only,)) == (floor, original_only)
    assert union_notices((floor,), (band_only,)) == (floor, band_only)
    fallback = Notice(
        kind=NoticeKind.FALLBACK,
        affected_quantities=(Quantity.P_SAT,),
        reason="fallback producer",
        origin="adapter",
        source="from-a",
        destination="to-a",
    )
    source_only = replace(fallback, source="from-b")
    destination_only = replace(fallback, destination="to-b")
    reason_only = replace(fallback, reason="different producer")
    assert union_notices((fallback,), (source_only,)) == (fallback, source_only)
    assert union_notices((fallback,), (destination_only,)) == (fallback, destination_only)
    assert union_notices((fallback,), (reason_only,)) == (fallback, reason_only)
    assert union_notices((fallback,), (fallback,)) == (fallback,)
    projected = Notice(
        kind=NoticeKind.COMPOSITION_PROJECTED,
        affected_quantities=(Quantity.ACTIVITY,),
        reason="dropped oxide",
        origin="engine",
        dropped=("P2O5",),
        dropped_mass_fraction=Decimal("0.0152"),
    )
    dropped_only = replace(projected, dropped=("Fe2O3",))
    assert union_notices((projected,), (dropped_only,)) == (projected, dropped_only)
    assert union_notices((projected,), (projected,)) == (projected,)


def test_r09_union_fingerprint_isolates_origin() -> None:
    """Two notices identical except origin remain two after union."""

    from dataclasses import replace

    floor = F.floor_notice()
    origin_only = replace(floor, origin="catalog:K")
    united = union_notices((floor,), (origin_only,))
    assert united == (floor, origin_only)
    assert len(united) == 2
    assert union_notices((floor,), (floor,)) == (floor,)


def test_r09_floor_notice_with_band_requires_original() -> None:
    from dataclasses import replace

    ident, w, exp, _ref = _r03_vapour_pair()
    missing_original = replace(F.floor_notice(), original=None)
    obs = F.observation(
        "r09-floor-no-original",
        exp.experiment_id,
        ident,
        Decimal("1e-25"),
        notices=(missing_original,),
    )
    report = validate_corpus([w], [exp], [obs])
    assert not report.ok
    assert any("original and band" in i.detail for i in report.issues)
    complete = F.observation(
        "r09-floor-original-ok",
        exp.experiment_id,
        ident,
        Decimal("1e-25"),
        notices=(F.floor_notice(),),
    )
    assert validate_corpus([w], [exp], [complete]).ok


def test_r09_printed_table_pairing_requires_reaction_and_formation_elements() -> None:
    """Spec :239: table self-check matches reaction/per/p° plus formation elements."""

    from dataclasses import replace

    ident_g = F.o2_identity()
    ident_k = replace(ident_g, quantity=Quantity.LOG10_KF)
    w = F.work()
    exp = F.tabulation_experiment()
    ref = F.observation(
        "r09-pair-ref",
        exp.experiment_id,
        ident_g,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    cand = F.engine_obs("r09-pair-cand", exp.experiment_id, ident_g, Decimal("0"))
    o2 = Species("O2", Phase.G)
    different_reaction = replace(
        ident_k,
        reaction=State.of(
            Reaction((ReactionTerm(o2, Fraction(2)), ReactionTerm(o2, Fraction(-2))))
        ),
    )
    rxn_log = F.observation(
        "r09-pair-rxn-logK",
        exp.experiment_id,
        different_reaction,
        Decimal("-59.154"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    scored = F.residual(
        "r09-pair-rxn",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    assert validate_corpus([w], [exp], [ref, rxn_log, cand], [scored]).ok
    different_elements = replace(
        ident_k,
        formation_elements=State.of((("O", Species("O", Phase.G)),)),
    )
    fe_log = F.observation(
        "r09-pair-fe-logK",
        exp.experiment_id,
        different_elements,
        Decimal("-59.154"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    fe_scored = F.residual(
        "r09-pair-fe",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    assert validate_corpus([w], [exp], [ref, fe_log, cand], [fe_scored]).ok
    matching_log = F.observation(
        "r09-pair-match-logK",
        exp.experiment_id,
        ident_k,
        Decimal("-59.154"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    matching = F.residual(
        "r09-pair-match",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    matching_report = validate_corpus([w], [exp], [ref, matching_log, cand], [matching])
    assert not matching_report.ok
    assert any(i.reason is RefusalReason.INVALID_SOURCE for i in matching_report.issues)


def test_r09_printed_table_pairing_requires_per_and_species_formula() -> None:
    """Spec :239: table pairing locks per and species.formula independently."""

    from dataclasses import replace

    ident_g = F.o2_identity()
    ident_k = replace(ident_g, quantity=Quantity.LOG10_KF)
    w = F.work()
    exp = F.tabulation_experiment()
    ref = F.observation(
        "r09-per-ref",
        exp.experiment_id,
        ident_g,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    cand = F.engine_obs("r09-per-cand", exp.experiment_id, ident_g, Decimal("0"))
    different_per = replace(ident_k, per=State.of(PerBasis.MOL_O2))
    per_log = F.observation(
        "r09-per-logK",
        exp.experiment_id,
        different_per,
        Decimal("-59.154"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    per_scored = F.residual(
        "r09-per-scored",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    per_report = validate_corpus([w], [exp], [ref, per_log, cand], [per_scored])
    assert per_report.ok
    assert not any(i.reason is RefusalReason.INVALID_SOURCE for i in per_report.issues)
    different_formula = replace(ident_k, species=Species("N2", Phase.G))
    formula_log = F.observation(
        "r09-formula-logK",
        exp.experiment_id,
        different_formula,
        Decimal("-59.154"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    formula_scored = F.residual(
        "r09-formula-scored",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    formula_report = validate_corpus([w], [exp], [ref, formula_log, cand], [formula_scored])
    assert formula_report.ok
    assert not any(i.reason is RefusalReason.INVALID_SOURCE for i in formula_report.issues)


def test_r09_pressure_blocking_applies_to_partial_and_reference() -> None:
    ident_partial = replace_pref_as_partial()
    ident_pref = F.pref_identity()
    w = F.work()
    exp = F.tabulation_experiment(T_K=ident_partial.temperature_K.value)
    for ident, tag, quantity in (
        (ident_partial, "partial", Quantity.P_PARTIAL),
        (ident_pref, "pref", Quantity.P_REFERENCE),
    ):
        notice = Notice(
            kind=NoticeKind.FALLBACK,
            affected_quantities=(quantity,),
            reason="fallback producer",
            origin="adapter",
            source="melt",
            destination="vacuum-floor",
        )
        ref = F.observation(
            f"r09-{tag}-ref",
            exp.experiment_id,
            ident,
            Decimal("0.1"),
            evidence=EvidenceClass.MEASURED_DIRECT,
        )
        cand = F.engine_obs(
            f"r09-{tag}-cand",
            exp.experiment_id,
            ident,
            Decimal("1"),
            notices=(notice,),
        )
        scored = F.residual(
            f"r09-{tag}-scored",
            ref.observation_id,
            candidate=cand.observation_id,
            status=ResidualStatus.MISMATCH,
            rail=Rail.VAPOUR,
            score_eligible=True,
            notices=union_notices(cand.notices),
        )
        report = validate_corpus([w], [exp], [ref, cand], [scored])
        assert not report.ok, tag
        clean = F.engine_obs(f"r09-{tag}-clean", exp.experiment_id, ident, Decimal("0.1"))
        control = F.residual(
            f"r09-{tag}-clean",
            ref.observation_id,
            candidate=clean.observation_id,
            status=ResidualStatus.MATCH,
            rail=Rail.VAPOUR,
            score_eligible=True,
        )
        assert validate_corpus([w], [exp], [ref, clean], [control]).ok, tag


def replace_pref_as_partial():
    from dataclasses import replace

    pref = F.pref_identity()
    pot = Composition(
        "ordered_complete_mole_inventory",
        (("SiO2", Decimal("0.5")), ("NaO0.5", Decimal("0.5"))),
        AmountBasis.MOLE_FRACTION,
    )
    return replace(
        pref,
        quantity=Quantity.P_PARTIAL,
        composition=State.of(pot),
        total_pressure_Pa=State.of(Decimal("100000")),
    )


def test_r09_refused_branch_requires_refusal_payload() -> None:
    ident = F.o2_identity()
    w = F.work()
    exp = F.tabulation_experiment()
    ref = F.observation(
        "r09-refusal-ref",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    cand = F.engine_obs("r09-refusal-cand", exp.experiment_id, ident, Decimal("0"))
    missing = Residual(
        key="r09-refusal-missing",
        reference=ref.observation_id,
        execution=Execution(state=ExecutionState.PRODUCED),
        rail=Rail.THERMOCHEMISTRY,
        status=ResidualStatus.REFUSED,
        source_relation=SourceRelation.INDEPENDENT,
        score_eligible=False,
        exclusions=(),
        notices=(),
        candidate=cand.observation_id,
        numeric=None,
        refusal=None,
    )
    report = validate_corpus([w], [exp], [ref, cand], [missing])
    assert not report.ok
    assert any("refused requires refusal" in i.detail for i in report.issues)
    control = F.residual(
        "r09-refusal-ok",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.REFUSED,
        refusal=ResidualRefusal(RefusalReason.IDENTITY_MISMATCH, {"fields": ["quantity"]}),
        score_eligible=False,
    )
    assert validate_corpus([w], [exp], [ref, cand], [control]).ok


def test_r09_engine_only_opposite_quantity_is_not_invalid_source() -> None:
    """Printed-only pairing: an engine logK sibling must not become a table half."""

    from dataclasses import replace

    ident_g = F.o2_identity()
    ident_k = replace(ident_g, quantity=Quantity.LOG10_KF)
    w = F.work()
    exp = F.tabulation_experiment()
    ref = F.observation(
        "r09-engine-ref",
        exp.experiment_id,
        ident_g,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    cand = F.engine_obs("r09-engine-cand", exp.experiment_id, ident_g, Decimal("0"))
    engine_log = F.engine_obs(
        "r09-engine-logK",
        exp.experiment_id,
        ident_k,
        Decimal("-59.154"),
    )
    scored = F.residual(
        "r09-engine-scored",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    assert validate_corpus([w], [exp], [ref, engine_log, cand], [scored]).ok
    printed_log = F.observation(
        "r09-engine-printed-logK",
        exp.experiment_id,
        ident_k,
        Decimal("-59.154"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    printed = validate_corpus([w], [exp], [ref, printed_log, cand], [scored])
    assert not printed.ok
    assert any(i.reason is RefusalReason.INVALID_SOURCE for i in printed.issues)


def test_r09_engine_reference_printed_logk_is_not_invalid_source() -> None:
    """Engine ΔfG=0 reference is not a printed table half (validate.py:268).

    Same-identity engine candidate + printed O2 log10_Kf=-59.154 in the
    tabulation must stay a SAME_INPUT MATCH diagnostic, not invalid_source.
    """

    from dataclasses import replace

    ident_g = F.o2_identity()
    ident_k = replace(ident_g, quantity=Quantity.LOG10_KF)
    w = F.work()
    exp = F.tabulation_experiment()
    ref = F.engine_obs("r09-engref-ref", exp.experiment_id, ident_g, Decimal("0"))
    cand = F.engine_obs("r09-engref-cand", exp.experiment_id, ident_g, Decimal("0"))
    printed_log = F.observation(
        "r09-engref-printed-logK",
        exp.experiment_id,
        ident_k,
        Decimal("-59.154"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    own = F.residual(
        "r09-engref-own",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        source_relation=SourceRelation.SAME_INPUT,
        score_eligible=False,
    )
    report = validate_corpus([w], [exp], [ref, printed_log, cand], [own])
    assert report.ok
    assert not any(i.reason is RefusalReason.INVALID_SOURCE for i in report.issues)


def test_r06_engine_evaluation_commanded_value_needs_no_locator() -> None:
    """C(empirical value): synthetic engine commands are not literature locators."""

    from dataclasses import replace

    w = F.work()
    exp = F.tabulation_experiment()
    synthetic = replace(
        exp,
        experiment_id="synthetic",
        kind=ExperimentKind.SYNTHETIC,
        work_id=None,
        simulated_experiment_id=exp.experiment_id,
        method=State.of(MethodToken.ENGINE_EVALUATION),
        locator=None,
        conditions={"temperature_K": Located(State.of(Decimal("298.15")))},
    )
    report = validate_corpus([w], [exp, synthetic], [])
    assert report.ok
    unlocated = replace(
        exp,
        conditions={"temperature_K": Located(State.of(Decimal("298.15")))},
    )
    empirical = validate_corpus([w], [unlocated], [])
    assert not empirical.ok
    assert any("locator" in i.detail for i in empirical.issues)


def test_r05_relative_series_rejects_nonfinite_values() -> None:
    with pytest.raises(ValueError, match="finite"):
        Value(
            ValueKind.RELATIVE_SERIES,
            relative_series=((Decimal("1"), Decimal("NaN")),),
            relative_normalization="first point",
        )
    with pytest.raises(ValueError, match="finite"):
        Value(
            ValueKind.RELATIVE_SERIES,
            relative_series=((Decimal("NaN"), Decimal("1")),),
            relative_normalization="first point",
        )
    control = Value(
        ValueKind.RELATIVE_SERIES,
        relative_series=((Decimal("1"), Decimal("0.5")),),
        relative_normalization="first point",
    )
    assert control.relative_series[0][1] == Decimal("0.5")


def test_r04_domain_extrapolation_projection_are_unknown_kinds() -> None:
    """Closed policy tokens only; aliases must not bypass certification policy."""

    with pytest.raises(ValueError):
        NoticeKind("domain")
    with pytest.raises(ValueError):
        NoticeKind("extrapolation")
    with pytest.raises(ValueError):
        NoticeKind("projection")
    assert NoticeKind.OUT_OF_CERTIFIED_BAND.value == "out_of_certified_band"
    assert NoticeKind.COMPOSITION_PROJECTED.value == "composition_projected"


def test_r03_wall_identity_compares_area_and_location() -> None:
    from dataclasses import replace

    a = F.wall_deposit_identity()
    wall = a.wall.value
    assert isinstance(wall, WallIdentity)
    area = replace(a, wall=State.of(replace(wall, area_m2=State.of(Decimal("0.99")))))
    location = replace(a, wall=State.of(replace(wall, location=State.of("baffle"))))
    area_out = identity_equal(a, area)
    loc_out = identity_equal(a, location)
    assert area_out.kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert "wall.area_m2" in area_out.fields
    assert loc_out.kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert "wall.location" in loc_out.fields
    assert identity_equal(a, F.wall_deposit_identity()).kind is IdentityEqualKind.EQUAL


def test_m16_refused_residual_cannot_carry_a_numeric_score() -> None:
    ident = F.cao_identity(Phase.CR)
    w = F.work()
    exp = F.tabulation_experiment()
    ref = F.observation(
        "ref-m16",
        exp.experiment_id,
        ident,
        Decimal("0.064"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
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

    numeric_only = Residual(
        key="m16-numeric-on-refused",
        reference=ref.observation_id,
        execution=Execution(state=ExecutionState.PRODUCED),
        rail=Rail.THERMOCHEMISTRY,
        status=ResidualStatus.REFUSED,
        source_relation=SourceRelation.INDEPENDENT,
        score_eligible=False,
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
    numeric_report = validate_corpus([w], [exp], [ref, cand], [numeric_only])
    assert not numeric_report.ok
    assert any("forbids numeric" in i.detail for i in numeric_report.issues)
    eligible_only = Residual(
        key="m16-eligible-on-refused",
        reference=ref.observation_id,
        execution=Execution(state=ExecutionState.PRODUCED),
        rail=Rail.THERMOCHEMISTRY,
        status=ResidualStatus.REFUSED,
        source_relation=SourceRelation.INDEPENDENT,
        score_eligible=True,
        exclusions=(),
        notices=(),
        candidate=cand.observation_id,
        refusal=ResidualRefusal(RefusalReason.INVALID_IDENTITY, {}),
    )
    eligible_report = validate_corpus([w], [exp], [ref, cand], [eligible_only])
    assert not eligible_report.ok
    assert any("score_eligible cannot be true when status is refused" in i.detail for i in eligible_report.issues)
    match_ok = F.residual(
        "m16-match",
        ref.observation_id,
        candidate=cand.observation_id,
        status=ResidualStatus.MATCH,
        score_eligible=True,
    )
    assert validate_corpus([w], [exp], [ref, cand], [match_ok]).ok


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
        composition=State.not_applicable("pure Na melting; no invented pot"),
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
    assert identity_equal(melt, melt).kind is IdentityEqualKind.EQUAL
    assert identity_equal(melt, boil).kind is IdentityEqualKind.IDENTITY_MISMATCH
    liquidus = Identity(
        **{
            **melt.__dict__,
            "subtype": State.of("liquidus"),
            "composition": State.of(
                Composition(
                    "ordered_complete_mole_inventory",
                    (("Na", Decimal("0.5")), ("K", Decimal("0.5"))),
                    AmountBasis.MOLE_FRACTION,
                )
            ),
        }
    )
    assert identity_equal(liquidus, liquidus).kind is IdentityEqualKind.EQUAL
    missing_pot = Identity(**{**liquidus.__dict__, "composition": State.not_applicable("missing mixture")})
    assert identity_equal(liquidus, missing_pot).kind is IdentityEqualKind.INVALID_IDENTITY
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
    assert gate.passed


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
    assert liquid.species.phase == gas.species.phase
    assert outcome.fields == ("formation_elements",) or "formation_elements" in outcome.fields


def test_pi_p1_2_pref_is_not_psat() -> None:
    pref = F.pref_identity()
    psat = F.psat_identity("Na", T_K=Decimal("1156"))
    assert quantity_token(pref) is Quantity.P_REFERENCE
    assert quantity_token(psat) is Quantity.P_SAT
    pref_vs_psat = identity_equal(pref, psat)
    assert pref_vs_psat.kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert pref_vs_psat.fields == ("quantity",)
    same_reservoir_pref = F.pref_identity(endmember_formula="Na")
    same_reservoir_psat = F.psat_identity("Na", T_K=Decimal("1156"))
    quantity_only = identity_equal(same_reservoir_pref, same_reservoir_psat)
    assert quantity_only.kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert quantity_only.fields == ("quantity",)
    assert identity_equal(psat, F.psat_identity("Na", T_K=Decimal("1156"))).kind is IdentityEqualKind.EQUAL


def test_pi_p1_3_p2o5_vs_p4o10_component_basis() -> None:
    p2o5 = F.activity_identity(formula="P2O5", component_basis="P2O5", endmember_phase=Phase.L)
    p4o10 = F.activity_identity(formula="P4O10", component_basis="P4O10", endmember_phase=Phase.CR)
    assert identity_equal(p2o5, p4o10).kind is IdentityEqualKind.IDENTITY_MISMATCH


def test_pi_p1_5_fo2_channel_is_not_identity() -> None:
    a = F.activity_identity(fO2_Pa=Decimal("1e-8"))
    b = F.activity_identity(fO2_Pa=Decimal("1e-8"))
    assert identity_equal(a, b).kind is IdentityEqualKind.EQUAL
    shifted = F.activity_identity(fO2_Pa=Decimal("1e-9"))
    fo2_only = identity_equal(a, shifted)
    assert fo2_only.kind is IdentityEqualKind.IDENTITY_MISMATCH
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
    assert underdetermined_apparatus(exp, Quantity.P_SAT).passed
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


def test_kems_background_interval_uses_bounds_without_inventing_a_point() -> None:
    from dataclasses import replace as _replace

    def with_interval(low: str, high: str, *, kn: Decimal | None = Decimal("20")):
        experiment = F.kems_experiment(kn=kn)
        pressure = Located(
            State.of(
                Value(
                    ValueKind.INTERVAL,
                    interval_low=Decimal(low),
                    interval_high=Decimal(high),
                )
            ),
            locator=F.loc(),
        )
        return _replace(
            experiment,
            pressure_environment=_replace(
                experiment.pressure_environment,
                total_pressure_Pa=pressure,
            ),
        )

    safe = background_pressure_high(
        with_interval("1e-6", "1e-3"), Quantity.P_PARTIAL
    )
    assert safe.passed
    assert safe.checks[0].detail["flag"] == "background_pressure_interval_upper_bound"
    assert safe.checks[0].detail["upper_bound_Pa"] == "0.001"
    calibrated_without_kn = with_interval("1e-6", "1e-3", kn=None)
    regime = effusion_regime_unverified(calibrated_without_kn, Quantity.P_PARTIAL)
    assert regime.passed
    assert regime.checks[-1].detail["flag"] == "orifice_knudsen_not_published"
    partial = _replace(F.psat_identity("K"), quantity=Quantity.P_PARTIAL)
    assert run_validity_gates(
        calibrated_without_kn,
        F.observation("interval-kems", calibrated_without_kn.experiment_id, partial, Decimal("1")),
    ).passed

    high = background_pressure_high(
        with_interval("0.02", "0.03"), Quantity.P_PARTIAL
    )
    assert high.reason is RefusalReason.BACKGROUND_PRESSURE_HIGH

    straddled = background_pressure_high(
        with_interval("0.001", "0.02"), Quantity.P_PARTIAL
    )
    assert straddled.reason is RefusalReason.BACKGROUND_PRESSURE_INTERVAL_STRADDLES
    assert straddled.checks[0].detail["flag"] == "background_pressure_interval_straddles"


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


def test_identity_must_agree_with_experiment_or_point_conditions() -> None:
    from dataclasses import replace

    ident = F.o2_identity(T_K=Decimal("298.15"))
    w = F.work()
    exp = F.tabulation_experiment(T_K=Decimal("298.15"))
    matching = F.observation("match-T", exp.experiment_id, ident, Decimal("0"))
    assert validate_corpus([w], [exp], [matching]).ok
    disagreed = replace(
        matching,
        observation_id="bad-T",
        identity=replace(ident, temperature_K=State.of(Decimal("999"))),
        point_conditions=None,
    )
    bad_report = validate_corpus([w], [exp], [disagreed])
    assert not bad_report.ok
    assert any(i.reason is RefusalReason.INVALID_IDENTITY for i in bad_report.issues)
    varying = replace(
        matching,
        observation_id="point-T",
        identity=replace(ident, temperature_K=State.of(Decimal("999"))),
        point_conditions={"temperature_K": F.located(Decimal("999"))},
    )
    assert validate_corpus([w], [exp], [varying]).ok


def test_referential_integrity_rejects_duplicates_and_dangling_refs() -> None:
    from dataclasses import replace

    w = F.work()
    exp = F.tabulation_experiment()
    ident = F.o2_identity()
    first = F.observation("dup", exp.experiment_id, ident, Decimal("0"))
    second = F.observation("dup", exp.experiment_id, ident, Decimal("999"))
    dup_report = validate_corpus([w], [exp], [first, second])
    assert not dup_report.ok
    assert any("duplicate" in i.detail for i in dup_report.issues)
    dangling = replace(first, observation_id="dangling", read_from="nonexistent-asset", source_id="foreign-source")
    dangling_report = validate_corpus([w], [exp], [dangling])
    assert not dangling_report.ok
    assert any(i.reason is RefusalReason.REFERENTIAL_INTEGRITY for i in dangling_report.issues)
    raw = F.observation(
        "raw-lineage",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    derived = F.observation(
        "derived-lineage",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_REDUCED,
        derived_from=("raw-lineage",),
        derivation=Derivation(
            relation="ion-to-pressure",
            inputs=("missing-input",),
            parameters=(),
            output_unit="Pa",
        ),
    )
    derived_report = validate_corpus([w], [exp], [raw, derived])
    assert not derived_report.ok
    assert any("derivation input" in i.detail for i in derived_report.issues)
    good_derived = F.observation(
        "derived-ok",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.MEASURED_REDUCED,
        derived_from=("raw-lineage",),
        derivation=Derivation(
            relation="ion-to-pressure",
            inputs=("raw-lineage",),
            parameters=(),
            output_unit="Pa",
        ),
    )
    assert validate_corpus([w], [exp], [raw, good_derived]).ok


def test_closed_records_reject_invalid_payloads() -> None:
    """Closed records: invalid tokens, mixed Value branches, empty maps, missing C()."""

    from dataclasses import replace

    from simulator.battery.records import Composition, Located, Value
    from simulator.battery.enums import AmountBasis, ValueKind

    with pytest.raises(ValueError):
        Species("O2", "not-a-phase")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Value(
            kind=ValueKind.POINT,
            point=Decimal("NaN"),
            bound_operator="<",
            bound_value=Decimal("1"),
        )
    with pytest.raises(ValueError):
        Composition(
            "anything",
            (("Na", Decimal("-1")), ("Na", Decimal("2"))),
            AmountBasis.MOLE_FRACTION,
        )
    ident = F.o2_identity()
    empty_fe = replace(ident, formation_elements=State.of(()))
    w = F.work()
    exp = F.tabulation_experiment()
    empty_obs = F.observation("empty-fe", exp.experiment_id, empty_fe, Decimal("0"))
    empty_report = validate_corpus([w], [exp], [empty_obs])
    assert not empty_report.ok
    assert any("formation_elements" in i.path for i in empty_report.issues)
    pending = F.observation(
        "pending-ok",
        exp.experiment_id,
        ident,
        Decimal("0"),
        admission=AdmissionStatus.PENDING,
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    assert pending.admission.decided_by is None
    assert validate_corpus([w], [exp], [pending]).ok
    admitted = F.observation("admitted-ok", exp.experiment_id, ident, Decimal("0"))
    assert admitted.admission.decided_by is not None
    unlocated = replace(exp, conditions={"temperature_K": Located(State.of(Decimal("298.15")))})
    unlocated_report = validate_corpus([w], [unlocated], [admitted])
    assert not unlocated_report.ok
    assert any("locator" in i.detail for i in unlocated_report.issues)
    estimate = F.observation(
        "estimate",
        exp.experiment_id,
        ident,
        Decimal("0"),
        evidence=EvidenceClass.AUTHOR_ESTIMATE,
    )
    assert not validate_corpus([w], [exp], [estimate]).ok

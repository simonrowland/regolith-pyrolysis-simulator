"""VR-9 activity seam + Henrian upper-bound ground-truth tests.

Diagnostic only. Assertions bind the DESIGN-REV5 §9.1–§9.2 contracts and the
vp-acquire-5 henrian-correlations semantics — not simulator self-parity.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from simulator.chemistry.melt_activity import (
    P2O5_ACTIVITY_COEFFICIENT,
    melt_oxide_activity,
)
from simulator.physical_constants import GAS_CONSTANT
from simulator.vapour_rail.activity import (
    BOUND_NOT_POINT,
    REASON_HENRIAN_GAMMA_UNMEASURED,
    ActivityInputDeclaration,
    ActivityRefusalCode,
    ActivityVerdictKind,
    AssemblageIdentity,
    BoundDirection,
    CondensedPhaseActivityProvider,
    MageminAssemblageEvidence,
    PhaseEndmemberMap,
    SourceReactionActivity,
    StandardStateIdentity,
    StateFingerprint,
    ThermoEnginePotentialEvidence,
    activity_from_chemical_potentials,
    composition_fingerprint,
    henrian_unknown_gamma_upper_bound,
    prove_pressure_monotone_nondecreasing_in_activity,
    validation_row_may_certify,
)


ROOT = Path(__file__).resolve().parents[1]
TRACE_ELEMENTS = ROOT / "data" / "trace_elements.yaml"

_RAOULTIAN = StandardStateIdentity(
    convention="raoultian_pure_liquid_oxide",
    phase="liquid",
    reference_pressure_bar=1.0,
    reference_temperature_K=1773.15,
    component_basis="raoultian_pure_endmember",
)


def _state(T_K: float = 1773.15) -> StateFingerprint:
    return StateFingerprint(
        temperature_K=T_K,
        pressure_bar=1.0,
        fO2_bar=1.0e-8,
        composition_fingerprint=composition_fingerprint(
            {"SiO2": 0.5, "MgO": 0.3, "Al2O3": 0.2}
        ),
    )


def _assemblage(state: StateFingerprint) -> AssemblageIdentity:
    return AssemblageIdentity(
        engine="magemin",
        phase_ids=("Liquid", "Spinel"),
        endmember_ids=("MgO", "Al2O3", "SiO2", "sp"),
        bulk_composition_fingerprint=state.composition_fingerprint,
        database="ig",
    )


def test_activity_from_mu_unity_limiting_case():
    """mu = mu0 ⇒ a = 1 (documented limiting case)."""

    a = activity_from_chemical_potentials(1000.0, 1000.0, 1500.0)
    assert a == pytest.approx(1.0)


def test_activity_from_mu_hand_evaluation():
    """Independent hand evaluation of a = exp((mu-mu0)/(R T))."""

    mu = -120_000.0
    mu0 = -100_000.0
    T = 1600.0
    expected = math.exp((mu - mu0) / (GAS_CONSTANT * T))
    assert activity_from_chemical_potentials(mu, mu0, T) == pytest.approx(expected)


def test_monotonicity_proof_sign_of_exponent():
    assert prove_pressure_monotone_nondecreasing_in_activity(1.0) is True
    assert prove_pressure_monotone_nondecreasing_in_activity(0.0) is True
    assert prove_pressure_monotone_nondecreasing_in_activity(-0.5) is False
    assert prove_pressure_monotone_nondecreasing_in_activity(float("nan")) is False


def test_p2o5_dilute_activity_has_literature_grounded_gamma_upper_envelope():
    coefficient = P2O5_ACTIVITY_COEFFICIENT
    assert coefficient.gamma == pytest.approx(1.0e-6)
    assert 0.0 < coefficient.gamma <= 1.0

    activity = melt_oxide_activity(
        "P2O5",
        {"P2O5": 1.0, "SiO2": 999.0},
        temperature_K=1873.0,
    )
    assert activity is not None
    assert activity.gamma <= 1.0
    assert 0.0 < activity.activity < activity.x_single_cation < 1.0
    assert "Turkdogan" in activity.citation

    legacy_activity = melt_oxide_activity(
        "P2O5",
        {"P2O5": 1.0, "SiO2": 999.0},
    )
    assert legacy_activity is not None
    assert legacy_activity.gamma == pytest.approx(1.0)
    assert legacy_activity.authority_status == (
        "assumed_unity_fallback_non_authoritative"
    )
    assert legacy_activity.warning is not None

    c0b_activity = melt_oxide_activity(
        "P2O5",
        {"P2O5": 0.01, "SiO2": 0.99},
        temperature_K=1473.15,
    )
    assert c0b_activity is not None
    assert c0b_activity.thermodynamic_parent_activity() == pytest.approx(
        c0b_activity.activity**2
    )
    assert c0b_activity.authority_status == (
        "out_of_gamma_domain_status_bearing_non_authoritative"
    )
    assert c0b_activity.warning is not None
    provenance = c0b_activity.provenance()
    assert provenance["melt_oxide_gamma_valid_range_K"] == (1823.0, 1923.0)
    assert provenance["melt_oxide_activity_temperature_K"] == 1473.15


def test_henrian_unknown_gamma_emits_upper_bound_never_point():
    answer = henrian_unknown_gamma_upper_bound(
        component_id="LiO0.5",
        activity_exponent=1.0,
        standard_state=_RAOULTIAN,
    )
    assert answer.verdict is ActivityVerdictKind.UPPER_BOUND
    assert answer.verdict is not ActivityVerdictKind.POINT
    assert answer.value == pytest.approx(1.0)
    assert answer.bound_direction is BoundDirection.UPPER
    assert answer.reason == REASON_HENRIAN_GAMMA_UNMEASURED
    assert answer.authority is False
    assert answer.report_label == BOUND_NOT_POINT
    assert answer.may_certify() is False
    # Derivation must explain why a=1 bounds volatilization from above.
    assert "P(a) ≤ P(1)" in answer.derivation["algebra"] or "P(a) <= P(1)" in answer.derivation[
        "algebra"
    ]
    assert "a_i ≤ 1" in answer.derivation["premise"] or "a_i <= 1" in answer.derivation[
        "premise"
    ]


def test_henrian_refuses_when_monotonicity_unproved():
    answer = henrian_unknown_gamma_upper_bound(
        component_id="trace",
        activity_exponent=-1.0,
        standard_state=_RAOULTIAN,
    )
    assert answer.verdict is ActivityVerdictKind.REFUSAL
    assert answer.refusal_code is ActivityRefusalCode.MONOTONICITY_UNPROVED
    assert answer.value is None
    assert answer.authority is False


def test_henrian_refuses_non_raoultian_basis():
    bad = StandardStateIdentity(
        convention="henrian_1wtpct",
        phase="liquid",
        reference_pressure_bar=1.0,
        component_basis="henrian_1wtpct",
    )
    answer = henrian_unknown_gamma_upper_bound(
        component_id="S",
        activity_exponent=1.0,
        standard_state=bad,
    )
    assert answer.verdict is ActivityVerdictKind.REFUSAL
    assert answer.refusal_code is ActivityRefusalCode.UNITY_NOT_UPPER_BOUND


def test_provider_matched_point_and_identity_gates():
    state = _state()
    assemblage = _assemblage(state)
    mapping = PhaseEndmemberMap(
        component_id="MgO",
        phase_id="Spinel",
        endmember_id="sp",
        source="reviewed_spinel_map",
    )
    provider = CondensedPhaseActivityProvider([mapping])
    declaration = ActivityInputDeclaration(
        component_id="MgO",
        standard_state=_RAOULTIAN,
        activity_model="chemical_potential",
        compound_bearing=True,
    )
    magemin = MageminAssemblageEvidence(
        assemblage=assemblage,
        state=state,
        phase_compositions={"Spinel": {"sp": 0.4, "MgO": 0.6}},
        converged=True,
    )
    # Choose mu so a is known: ln(a) = (mu-mu0)/(RT) = ln(0.25)
    T = state.temperature_K
    a_target = 0.25
    mu0 = -200_000.0
    mu = mu0 + GAS_CONSTANT * T * math.log(a_target)
    thermo = ThermoEnginePotentialEvidence(
        component_id="MgO",
        state=state,
        standard_state=_RAOULTIAN,
        assemblage_ref=assemblage.fingerprint(),
        mu_J_per_mol=mu,
        mu0_J_per_mol=mu0,
        independent_consistency_ok=True,
    )
    answer = provider.resolve_source_reaction_activity(
        declaration,
        magemin=magemin,
        thermoengine=thermo,
        activity_exponent=1.0,
        compound_bearing_state=True,
    )
    assert answer.verdict is ActivityVerdictKind.POINT
    assert answer.value == pytest.approx(a_target, rel=1e-12)
    assert answer.authority is False
    assert answer.may_certify() is False


def test_provider_reported_activity_point_records_exact_provenance():
    declaration = ActivityInputDeclaration(
        component_id="MgO",
        standard_state=_RAOULTIAN,
        activity_model="provider_reported_thermodynamic_activity",
        allow_henrian_upper_bound=False,
        require_assemblage_match=False,
    )
    state_fingerprint = _state().fingerprint()
    answer = CondensedPhaseActivityProvider().resolve_source_reaction_activity(
        declaration,
        magemin=None,
        thermoengine=None,
        activity_exponent=1.0,
        state_fingerprint=state_fingerprint,
        reported_activity=2.5e-3,
        reported_activity_provider="InternalAnalyticalBackend",
        reported_activity_evidence_ref=(
            "InternalAnalyticalBackend:"
            "EquilibriumResult.activity_coefficients[Mg]"
        ),
        reported_activity_standard_state=_RAOULTIAN,
    )

    assert answer.verdict is ActivityVerdictKind.POINT
    assert answer.value == pytest.approx(2.5e-3)
    assert answer.state_fingerprint == state_fingerprint
    assert answer.provider == "InternalAnalyticalBackend"
    assert answer.evidence_ref.endswith("activity_coefficients[Mg]")
    assert answer.authority is False

    missing_provenance = (
        CondensedPhaseActivityProvider().resolve_source_reaction_activity(
            declaration,
            magemin=None,
            thermoengine=None,
            activity_exponent=1.0,
            reported_activity=2.5e-3,
        )
    )
    assert missing_provenance.verdict is ActivityVerdictKind.REFUSAL
    assert missing_provenance.refusal_code is ActivityRefusalCode.MISSING_EVIDENCE

    for provider, evidence_ref in (
        ("   ", "test:evidence"),
        ("test_provider", "\t"),
    ):
        whitespace_provenance = (
            CondensedPhaseActivityProvider().resolve_source_reaction_activity(
                declaration,
                magemin=None,
                thermoengine=None,
                activity_exponent=1.0,
                reported_activity=2.5e-3,
                reported_activity_provider=provider,
                reported_activity_evidence_ref=evidence_ref,
                reported_activity_standard_state=_RAOULTIAN,
            )
        )
        assert whitespace_provenance.verdict is ActivityVerdictKind.REFUSAL
        assert (
            whitespace_provenance.refusal_code
            is ActivityRefusalCode.MISSING_EVIDENCE
        )

    normalized_provenance = (
        CondensedPhaseActivityProvider().resolve_source_reaction_activity(
            declaration,
            magemin=None,
            thermoengine=None,
            activity_exponent=1.0,
            reported_activity=2.5e-3,
            reported_activity_provider="  test_provider  ",
            reported_activity_evidence_ref="  test:evidence  ",
            reported_activity_standard_state=_RAOULTIAN,
        )
    )
    assert normalized_provenance.provider == "test_provider"
    assert normalized_provenance.evidence_ref == "test:evidence"

    mismatched_standard_state = StandardStateIdentity(
        convention=_RAOULTIAN.convention,
        phase="solid",
        reference_pressure_bar=_RAOULTIAN.reference_pressure_bar,
        reference_temperature_K=_RAOULTIAN.reference_temperature_K,
        component_basis=_RAOULTIAN.component_basis,
    )
    standard_state_mismatch = (
        CondensedPhaseActivityProvider().resolve_source_reaction_activity(
            declaration,
            magemin=None,
            thermoengine=None,
            activity_exponent=1.0,
            reported_activity=2.5e-3,
            reported_activity_provider="test_provider",
            reported_activity_evidence_ref="test:evidence",
            reported_activity_standard_state=mismatched_standard_state,
        )
    )
    assert standard_state_mismatch.verdict is ActivityVerdictKind.REFUSAL
    assert (
        standard_state_mismatch.refusal_code
        is ActivityRefusalCode.STANDARD_STATE_MISMATCH
    )


@pytest.mark.parametrize(
    "fault,code",
    [
        ("timeout", ActivityRefusalCode.TIMEOUT),
        ("crash", ActivityRefusalCode.CRASH),
        ("expired", ActivityRefusalCode.EXPIRED),
        ("state", ActivityRefusalCode.STATE_FINGERPRINT_MISMATCH),
        ("assemblage", ActivityRefusalCode.ASSEMBLAGE_MISMATCH),
        ("standard_state", ActivityRefusalCode.STANDARD_STATE_MISMATCH),
        ("unmapped_phase", ActivityRefusalCode.UNMAPPED_PHASE),
        ("unmapped_endmember", ActivityRefusalCode.UNMAPPED_ENDMEMBER),
        ("consistency", ActivityRefusalCode.CONSISTENCY_GATE_FAILED),
    ],
)
def test_provider_refuses_mismatch_timeout_unmapped(fault, code):
    state = _state()
    assemblage = _assemblage(state)
    mapping = PhaseEndmemberMap(
        component_id="MgO",
        phase_id="Spinel",
        endmember_id="sp",
        source="reviewed_spinel_map",
    )
    provider = CondensedPhaseActivityProvider([mapping])
    declaration = ActivityInputDeclaration(
        component_id="MgO",
        standard_state=_RAOULTIAN,
        activity_model="chemical_potential",
        compound_bearing=True,
    )
    magemin = MageminAssemblageEvidence(
        assemblage=assemblage,
        state=state,
        phase_compositions={"Spinel": {"sp": 1.0}},
        converged=True,
        timed_out=(fault == "timeout"),
        crashed=(fault == "crash"),
        expired=(fault == "expired"),
    )
    other_state = StateFingerprint(
        temperature_K=state.temperature_K + 10.0,
        pressure_bar=state.pressure_bar,
        fO2_bar=state.fO2_bar,
        composition_fingerprint=state.composition_fingerprint,
    )
    thermo_state = other_state if fault == "state" else state
    thermo_assemblage_ref = (
        "not-the-assemblage" if fault == "assemblage" else assemblage.fingerprint()
    )
    thermo_ss = (
        StandardStateIdentity(
            convention="other",
            phase="liquid",
            reference_pressure_bar=1.0,
            component_basis="raoultian_pure_endmember",
        )
        if fault == "standard_state"
        else _RAOULTIAN
    )
    if fault == "unmapped_phase":
        magemin = MageminAssemblageEvidence(
            assemblage=AssemblageIdentity(
                engine="magemin",
                phase_ids=("Liquid",),  # Spinel missing
                endmember_ids=("MgO", "Al2O3", "SiO2", "sp"),
                bulk_composition_fingerprint=state.composition_fingerprint,
            ),
            state=state,
            phase_compositions={"Liquid": {"SiO2": 1.0}},
            converged=True,
        )
        thermo_assemblage_ref = magemin.assemblage.fingerprint()
    if fault == "unmapped_endmember":
        magemin = MageminAssemblageEvidence(
            assemblage=AssemblageIdentity(
                engine="magemin",
                phase_ids=("Liquid", "Spinel"),
                endmember_ids=("MgO", "Al2O3", "SiO2"),  # sp missing
                bulk_composition_fingerprint=state.composition_fingerprint,
            ),
            state=state,
            phase_compositions={"Spinel": {"MgO": 1.0}},
            converged=True,
        )
        thermo_assemblage_ref = magemin.assemblage.fingerprint()

    thermo = ThermoEnginePotentialEvidence(
        component_id="MgO",
        state=thermo_state,
        standard_state=thermo_ss,
        assemblage_ref=thermo_assemblage_ref,
        mu_J_per_mol=-150_000.0,
        mu0_J_per_mol=-150_000.0,
        independent_consistency_ok=False if fault == "consistency" else True,
        independent_consistency_note="spinel cross-check failed",
    )
    answer = provider.resolve_source_reaction_activity(
        declaration,
        magemin=magemin,
        thermoengine=thermo,
        activity_exponent=1.0,
        compound_bearing_state=True,
    )
    assert answer.verdict is ActivityVerdictKind.REFUSAL
    assert answer.refusal_code is code
    assert answer.authority is False
    assert answer.value is None


def test_provider_henrian_path_without_engine_evidence():
    provider = CondensedPhaseActivityProvider()
    declaration = ActivityInputDeclaration(
        component_id="RbO0.5",
        standard_state=_RAOULTIAN,
        activity_model="henrian_dilute",
        allow_henrian_upper_bound=True,
    )
    answer = provider.resolve_source_reaction_activity(
        declaration,
        magemin=None,
        thermoengine=None,
        activity_exponent=1.0,
    )
    assert answer.verdict is ActivityVerdictKind.UPPER_BOUND
    assert answer.value == pytest.approx(1.0)
    assert answer.authority is False


def test_compound_bearing_refuses_free_oxide_proxy_without_engines():
    provider = CondensedPhaseActivityProvider()
    declaration = ActivityInputDeclaration(
        component_id="MgO",
        standard_state=_RAOULTIAN,
        activity_model="proxy",
        compound_bearing=True,
        allow_henrian_upper_bound=True,
    )
    answer = provider.resolve_source_reaction_activity(
        declaration,
        magemin=None,
        thermoengine=None,
        activity_exponent=1.0,
        compound_bearing_state=True,
    )
    assert answer.verdict is ActivityVerdictKind.REFUSAL
    assert answer.refusal_code is ActivityRefusalCode.COMPOUND_PROXY_FORBIDDEN


def test_as_pressure_activity_refusal_none_and_bound_numeric_contract():
    """P2-3: refusal must not fail-open to a number; UpperBound returns float.

    Callers must still inspect verdict — a returned 1.0 under UpperBound is a
    bound, never a certified point (SC-50 watch for future pressure consumers).
    """

    refused = SourceReactionActivity(
        component_id="X",
        value=None,
        verdict=ActivityVerdictKind.REFUSAL,
        bound_direction=None,
        reason="test",
        standard_state=None,
        phase_assemblage_ref=None,
        chemical_potential_ref=None,
        state_fingerprint=None,
        solve_group_id=None,
        provider="test",
        authority=False,
        refusal_code=ActivityRefusalCode.TIMEOUT,
    )
    assert refused.as_pressure_activity() is None

    # Explicit non-None value on a refusal must still not coerce to a number.
    refused_with_junk = SourceReactionActivity(
        component_id="X",
        value=1.0,
        verdict=ActivityVerdictKind.REFUSAL,
        bound_direction=None,
        reason="test",
        standard_state=None,
        phase_assemblage_ref=None,
        chemical_potential_ref=None,
        state_fingerprint=None,
        solve_group_id=None,
        provider="test",
        authority=False,
        refusal_code=ActivityRefusalCode.TIMEOUT,
    )
    assert refused_with_junk.as_pressure_activity() is None

    bound = henrian_unknown_gamma_upper_bound(
        component_id="LiO0.5",
        activity_exponent=1.0,
        standard_state=_RAOULTIAN,
    )
    assert bound.verdict is ActivityVerdictKind.UPPER_BOUND
    assert bound.as_pressure_activity() == pytest.approx(1.0)
    assert bound.may_certify() is False

    point = SourceReactionActivity(
        component_id="MgO",
        value=0.25,
        verdict=ActivityVerdictKind.POINT,
        bound_direction=None,
        reason=None,
        standard_state=_RAOULTIAN,
        phase_assemblage_ref=None,
        chemical_potential_ref=None,
        state_fingerprint=None,
        solve_group_id=None,
        provider="test",
        authority=False,
    )
    assert point.as_pressure_activity() == pytest.approx(0.25)


def test_pending_validation_and_bound_never_certify():
    bound = henrian_unknown_gamma_upper_bound(
        component_id="X",
        activity_exponent=1.0,
        standard_state=_RAOULTIAN,
    )
    assert validation_row_may_certify(
        validation_status="pending_validation", activity=bound
    ) is False
    assert validation_row_may_certify(
        validation_status="validated", activity=bound
    ) is False
    # Point remains non-authoritative on this diagnostic seam.
    provider = CondensedPhaseActivityProvider()
    declaration = ActivityInputDeclaration(
        component_id="NaO0.5",
        standard_state=_RAOULTIAN,
        activity_model="gamma_table",
    )
    point = provider.resolve_source_reaction_activity(
        declaration,
        magemin=None,
        thermoengine=None,
        activity_exponent=1.0,
        measured_gamma=1.0e-3,
        mole_fraction=0.01,
    )
    assert point.verdict is ActivityVerdictKind.POINT
    assert point.authority is False
    assert validation_row_may_certify(
        validation_status="validated", activity=point
    ) is False
    assert validation_row_may_certify(
        validation_status="pending_validation", activity=point
    ) is False


def test_trace_elements_policy_bridge_is_diagnostic():
    payload = yaml.safe_load(TRACE_ELEMENTS.read_text())
    policy = payload["vapour_rail_activity_policy"]
    assert policy["certifies"] is False
    assert policy["pending_validation_certifies"] is False
    assert policy["unmeasured_henrian_gamma"] == "upper_bound_a_eq_1"
    assert policy["alpha_fit_from_vaporock"] == "forbidden"
    assert policy["kems_and_langmuir_regimes"] == "distinct"
    assert policy["adapter_seam"] == "CondensedPhaseActivityProvider"

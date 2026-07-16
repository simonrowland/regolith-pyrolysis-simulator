"""Tests for the BuiltinMetallothermicStepProvider -- sixth intent flip
of ``\\goal BUILTIN-ENGINE-EXTRACTION`` (#7) and the FOURTH authoritative
ledger-mutating intent in the migration (after EVAPORATION_TRANSITION,
CONDENSATION_ROUTE, ELECTROLYSIS_STEP).

Covers:

* Capability profile: the provider is authoritative for
  ``METALLOTHERMIC_STEP`` and declares the three accounts the C3/C6
  reductions touch (``process.cleaned_melt``,
  ``process.metal_phase``, ``process.reagent_inventory``).
* Wrong-intent rejection: the provider returns an ``unsupported``
  ``IntentResult`` if dispatched against an intent it does not serve.
* Account filter: the kernel filter scopes the provider's view to the
  three declared accounts only -- any other ledger account (overhead
  gas, condensation_train, terminal O2 bins) is invisible.
* Per-family ground-truth proposals: deterministic per-reaction inputs
  yield the exact stoichiometric debit/credit dicts the legacy
  ``_record_atom_transition`` calls would have produced (within
  IEEE-754 round-off on the same operand sequence).  Covered:
  C3 K-shuttle, C3 Na-shuttle (Cr + Ti combined), C6 Mg thermite
  primary, C6 Al-SiO2 back-reduction.
* Atom-balance gate engagement -- a malformed thermite proposal
  (``3 Mg + Al2O3 -> 2 MgO + 2 Al``, missing 1 mol Mg and 1 mol O) is
  rejected at :meth:`ChemistryKernel.commit_batch` with
  :class:`AtomBalanceError`.  Companion accepts-balanced test pins
  that the rejection isn't a false negative.
* Smoke parity: full C0 -> C6 run on lunar + Mars feedstocks closes
  mass balance to the existing 5e-12 % tolerance and the kernel-
  committed shuttle / thermite transitions land in
  process.cleaned_melt / metal_phase / reagent_inventory only.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import copy
import math
from pathlib import Path

import pytest

import engines.builtin.metallothermic_step as metallothermic_step_module
import simulator.chemistry.phase_context as phase_context_module
from engines.builtin.metallothermic_step import (
    BuiltinMetallothermicStepProvider,
    NA_STAGE_TARGETS,
    NA_TARGET_CR_TI,
    REACTION_FAMILY_C3_K,
    REACTION_FAMILY_C3_NA,
    REACTION_FAMILY_C6_MG,
    SPENT_REDUCTANT_RESIDUE_ACCOUNT,
)
from simulator.chemistry.melt_activity import (
    MELT_OXIDE_ACTIVITY_COEFFICIENTS,
)
from simulator.chemistry.ellingham_thermo import (
    ELLINGHAM_AUTHORITY_LIMIT_FLAG,
    ELLINGHAM_RECONSTRUCTED_AUTHORITY_FLAG,
)
from simulator.chemistry.kernel import (
    AtomBalanceError,
    ChemistryIntent,
    IntentRequest,
    IntentResult,
    LedgerTransitionProposal,
)
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.melt_backend.magemin import MAGEMinBackend
from simulator.state import (
    MOLAR_MASS,
    CampaignPhase,
    DecisionType,
)
from tests.chemistry.conftest import _atom_check, _build_sim


def _dispatch_bound_proposal(kernel, proposal):
    with patch.object(
        BuiltinMetallothermicStepProvider,
        "dispatch",
        return_value=IntentResult(
            intent=ChemistryIntent.METALLOTHERMIC_STEP,
            status="ok",
            transition=proposal,
        ),
    ):
        result = kernel.dispatch(
            ChemistryIntent.METALLOTHERMIC_STEP,
            temperature_C=1400.0,
            pressure_bar=1e-6,
        )
    assert result.transition is not None
    return result.transition


# ---------------------------------------------------------------------------
# 1. Capability profile
# ---------------------------------------------------------------------------


def test_provider_declares_only_metallothermic_step_intent():
    provider = BuiltinMetallothermicStepProvider()
    profile = provider.capability_profile()

    assert profile.intents == frozenset(
        {ChemistryIntent.METALLOTHERMIC_STEP}
    )
    assert profile.is_authoritative_for == frozenset(
        {ChemistryIntent.METALLOTHERMIC_STEP}
    )
    for intent in ChemistryIntent:
        if intent is ChemistryIntent.METALLOTHERMIC_STEP:
            assert profile.is_authoritative(intent)
        else:
            assert not profile.is_authoritative(intent)


def test_provider_declares_metallothermic_accounts():
    """Provider declares exactly the accounts touched by every
    legacy ``_record_atom_transition`` call inside
    ``_shuttle_inject_K``, ``_shuttle_inject_Na``, and
    ``_step_thermite``.  Pinning the set stops a future refactor from
    silently widening the surface (e.g. crediting overhead_gas, any
    terminal O2 bin, or condensation_train).
    """

    provider = BuiltinMetallothermicStepProvider()
    profile = provider.capability_profile()
    assert profile.declared_accounts == frozenset({
        "process.cleaned_melt",
        "process.metal_phase",
        "process.reagent_inventory",
        SPENT_REDUCTANT_RESIDUE_ACCOUNT,
    })
    assert "process.overhead_gas" not in profile.declared_accounts
    assert "process.condensation_train" not in profile.declared_accounts
    assert "terminal.oxygen_mre_anode_stored" not in profile.declared_accounts
    assert "terminal.oxygen_melt_offgas_stored" not in profile.declared_accounts
    assert "terminal.oxygen_stage0_stored" not in profile.declared_accounts


def test_ellingham_authority_flag_is_consumed_by_metallothermic_diagnostic():
    provider = BuiltinMetallothermicStepProvider()

    extrapolations = provider._ellingham_pair_fit_extrapolations(
        "Na",
        ("TiO2",),
        2350.0,
    )
    diagnostic = provider._ellingham_fit_diagnostic(extrapolations)

    authority = diagnostic["ellingham_authority"]
    assert authority["consumer"] == "builtin-metallothermic-step"
    assert authority["status"] == "extrapolation_limited"
    assert authority[ELLINGHAM_AUTHORITY_LIMIT_FLAG] is True
    assert "Na/TiO2" in authority["extrapolated_beyond_fit_range_K"]
    limited = authority["extrapolated_beyond_fit_range_K"]["Na/TiO2"][
        "limited_species"
    ]
    assert set(limited) == {"Na", "Ti"}


def test_reconstructed_mn_authority_is_not_mislabeled_as_fit_extrapolation():
    provider = BuiltinMetallothermicStepProvider()

    limits = provider._ellingham_pair_fit_extrapolations(
        "Na",
        ("MnO",),
        1600.0,
    )
    diagnostic = provider._ellingham_fit_diagnostic(limits)

    pair = limits["Na/MnO"]
    assert pair["authority_status"] == "reconstructed_limited"
    assert pair[ELLINGHAM_RECONSTRUCTED_AUTHORITY_FLAG] is True
    assert pair["limited_species"]["Mn"]["authority_status"] == (
        "reconstructed_limited"
    )
    authority = diagnostic["ellingham_authority"]
    assert authority["status"] == "authority_limited"
    assert authority[ELLINGHAM_AUTHORITY_LIMIT_FLAG] is False
    assert authority[ELLINGHAM_RECONSTRUCTED_AUTHORITY_FLAG] is True
    assert "ellingham_extrapolated_beyond_fit_range_K" not in diagnostic
    assert provider._ellingham_fit_warnings(limits) == ()


# ---------------------------------------------------------------------------
# 2. Wrong-intent rejection (defence in depth)
# ---------------------------------------------------------------------------


def test_provider_rejects_wrong_intent(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """If a future caller dispatches the provider against an intent it
    does not serve, ``reject_wrong_intent`` must return an
    ``unsupported`` ``IntentResult`` rather than producing a silent
    mis-answer."""

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinMetallothermicStepProvider()
    view = ProviderAccountView(
        accounts={},
        species_formula_registry=sim.species_formula_registry,
    )
    request = IntentRequest(
        intent=ChemistryIntent.ELECTROLYSIS_STEP,  # WRONG INTENT
        account_view=view,
        temperature_C=1300.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_C3_K,
            "reagent_available_kg": 10.0,
            "dt_hr": 1.0,
        },
    )
    result = provider.dispatch(request)
    assert result.status == "unsupported"
    assert result.transition is None


def test_provider_rejects_unknown_reaction_family(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """An unknown ``reaction_family`` discriminator must surface as an
    ``unsupported`` IntentResult, not a silent mis-dispatch.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinMetallothermicStepProvider()
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": {},
            "process.metal_phase": {},
            "process.reagent_inventory": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    request = IntentRequest(
        intent=ChemistryIntent.METALLOTHERMIC_STEP,
        account_view=view,
        temperature_C=1300.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": "future_unknown_family",
            "reagent_available_kg": 1.0,
        },
    )
    result = provider.dispatch(request)
    assert result.status == "unsupported"
    assert result.transition is None


# ---------------------------------------------------------------------------
# 3. Kernel account filter scopes the view
# ---------------------------------------------------------------------------


def test_kernel_filters_provider_to_declared_accounts_only(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """When other accounts hold material, the provider must see ONLY
    the declared metallothermic accounts. The kernel account
    filter is the enforcer (binding spec §7); a process.overhead_gas
    seed must NOT cross the boundary into this provider's view.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    # Seed unrelated accounts so the filter has something to filter.
    sim.atom_ledger.load_external(
        "process.overhead_gas", {"Na": 0.5}, source="test seed"
    )
    sim.atom_ledger.load_external(
        "process.condensation_train", {"Fe": 0.5}, source="test seed"
    )

    seen_accounts: list[frozenset[str]] = []
    original_dispatch = BuiltinMetallothermicStepProvider.dispatch

    def _spying_dispatch(self, request):
        seen_accounts.append(frozenset(request.account_view.accounts))
        return original_dispatch(self, request)

    BuiltinMetallothermicStepProvider.dispatch = _spying_dispatch
    try:
        sim._chem_kernel.dispatch(
            ChemistryIntent.METALLOTHERMIC_STEP,
            temperature_C=1300.0,
            pressure_bar=1e-6,
            control_inputs={
                "reaction_family": REACTION_FAMILY_C3_K,
                # No reagent -> early exit, but filter still engages.
                "reagent_available_kg": 0.0,
            },
        )
    finally:
        BuiltinMetallothermicStepProvider.dispatch = original_dispatch

    assert seen_accounts, "provider was never dispatched"
    expected = frozenset({
        "process.cleaned_melt",
        "process.metal_phase",
        "process.reagent_inventory",
        SPENT_REDUCTANT_RESIDUE_ACCOUNT,
    })
    for accounts in seen_accounts:
        assert accounts == expected, (
            "kernel filter leaked an undeclared account into the provider"
        )
        assert "process.overhead_gas" not in accounts
        assert "process.condensation_train" not in accounts
        assert "terminal.oxygen_mre_anode_stored" not in accounts


# ---------------------------------------------------------------------------
# 4. Atom-balance gate: malformed proposal must be rejected at commit
# ---------------------------------------------------------------------------


def test_kernel_commit_rejects_atom_unbalanced_thermite_proposal(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Construct a hand-rolled :class:`LedgerTransitionProposal` where
    the credit atoms do NOT conserve the debit atoms for thermite:
    ``3 Mg + Al2O3 -> 2 MgO + 2 Al`` (missing 1 mol Mg and 1 mol O on
    the credit side).  Verify that :meth:`ChemistryKernel.commit_batch`
    raises :class:`AtomBalanceError`.  Proves the authoritative ledger-
    write path actually engages atom-balance validation for this
    intent.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    # 3 Mg + Al2O3 -> 2 MgO + 2 Al -- credit has 2 Mg (not 3) and
    # 2 O (not 3), so the atom balance is off by 1 Mg + 1 O.
    bad_proposal = LedgerTransitionProposal(
        debits={
            "process.reagent_inventory": {"Mg": 3.0},
            "process.cleaned_melt": {"Al2O3": 1.0},
        },
        credits={
            "process.cleaned_melt": {"MgO": 2.0},
            "process.metal_phase": {"Al": 2.0},
        },
        reason="malformed_thermite_proposal_for_test",
        atom_balance_proof={"Mg": 0.0, "Al": 0.0, "O": 0.0},
    )

    with patch("simulator.chemistry.kernel.planner.validate_atom_balance"):
        bound_proposal = _dispatch_bound_proposal(sim._chem_kernel, bad_proposal)
    before_balances = sim.atom_ledger.mol_by_account()
    before_transitions = sim.atom_ledger.transitions

    with pytest.raises(AtomBalanceError):
        sim._chem_kernel.commit_batch(
            ChemistryIntent.METALLOTHERMIC_STEP, bound_proposal
        )

    assert sim.atom_ledger.mol_by_account() == before_balances
    assert sim.atom_ledger.transitions == before_transitions


def test_kernel_commit_rejects_atom_unbalanced_k_shuttle_proposal(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """K-shuttle malformed proposal: ``2 K + FeO -> K2O + Fe`` but
    credit drops the Fe metal entirely.  Atom balance fails on Fe.
    Companion to the thermite rejection -- proves the gate engages on
    both reaction families.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    bad_proposal = LedgerTransitionProposal(
        debits={
            "process.reagent_inventory": {"K": 2.0},
            "process.cleaned_melt": {"FeO": 1.0},
        },
        credits={
            # No metal_phase credit -> Fe atom missing.
            "process.cleaned_melt": {"K2O": 1.0},
        },
        reason="malformed_k_shuttle_proposal_for_test",
        atom_balance_proof={"K": 0.0, "Fe": 0.0, "O": 0.0},
    )

    with patch("simulator.chemistry.kernel.planner.validate_atom_balance"):
        bound_proposal = _dispatch_bound_proposal(sim._chem_kernel, bad_proposal)
    before_balances = sim.atom_ledger.mol_by_account()
    before_transitions = sim.atom_ledger.transitions

    with pytest.raises(AtomBalanceError):
        sim._chem_kernel.commit_batch(
            ChemistryIntent.METALLOTHERMIC_STEP, bound_proposal
        )

    assert sim.atom_ledger.mol_by_account() == before_balances
    assert sim.atom_ledger.transitions == before_transitions


def test_kernel_commit_accepts_balanced_thermite_proposal(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Companion to the rejection test: ``3 Mg + Al2O3 -> 3 MgO +
    2 Al`` must commit cleanly.  Mg: -3 + 3 = 0; Al: -2 + 2 = 0;
    O: -3 + 3 = 0.  Sanity check that the rejection above isn't a
    false negative caused by some other validator misfiring.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    # Seed reagent_inventory and cleaned_melt so the apply doesn't
    # underflow.  We use small mol values relative to the load_batch
    # seed.
    sim.atom_ledger.load_external_mol(
        "process.reagent_inventory", {"Mg": 5.0}, source="test seed"
    )

    balanced_proposal = LedgerTransitionProposal(
        debits={
            "process.reagent_inventory": {"Mg": 3.0},
            "process.cleaned_melt": {"Al2O3": 1.0},
        },
        credits={
            "process.cleaned_melt": {"MgO": 3.0},
            "process.metal_phase": {"Al": 2.0},
        },
        reason="balanced_thermite_proposal_for_test",
        atom_balance_proof={"Mg": 0.0, "Al": 0.0, "O": 0.0},
    )

    bound_proposal = _dispatch_bound_proposal(sim._chem_kernel, balanced_proposal)
    before = sim.atom_ledger.mol_by_account()
    sim._chem_kernel.commit_batch(
        ChemistryIntent.METALLOTHERMIC_STEP, bound_proposal
    )
    after = sim.atom_ledger.mol_by_account()

    assert after["process.reagent_inventory"]["Mg"] == pytest.approx(
        before["process.reagent_inventory"]["Mg"] - 3.0
    )
    assert after["process.cleaned_melt"]["Al2O3"] == pytest.approx(
        before["process.cleaned_melt"]["Al2O3"] - 1.0
    )
    assert after["process.cleaned_melt"]["MgO"] == pytest.approx(
        before.get("process.cleaned_melt", {}).get("MgO", 0.0) + 3.0
    )
    assert after["process.metal_phase"]["Al"] == pytest.approx(
        before.get("process.metal_phase", {}).get("Al", 0.0) + 2.0
    )


# ---------------------------------------------------------------------------
# 5. Unit: deterministic per-family proposals match legacy stoich exactly
# ---------------------------------------------------------------------------


def test_reduction_margin_kj_per_mol_o2_uses_ellingham_difference():
    # Margin under the JANAF-4th multiphase Ellingham re-ground:
    # Na/FeO @ 1150 C = +11.1 kJ/mol O2.
    provider = BuiltinMetallothermicStepProvider()

    margin = provider._reduction_margin_kj_per_mol_o2("Na", "FeO", 1150.0)

    assert margin == pytest.approx(11.1, abs=0.1)


def test_crossover_temperature_C_reports_physical_roots_only():
    # K/Fe crossover under the JANAF-4th multiphase re-ground is 836.25 C
    # (V1c-NEEDS-RECIPE-RETUNE finding: K shuttle no longer
    # viable in default 1150-1600 C melt window).
    # Na/Ti has only a low-T algebraic root outside the shared JANAF segment,
    # so the diagnostic refuses to report it as an authoritative crossover.
    provider = BuiltinMetallothermicStepProvider()

    assert provider._crossover_temperature_C("K", "Fe") == pytest.approx(
        836.25,
        abs=0.5,
    )
    assert provider._crossover_temperature_C("Na", "Ti") is None


def test_crossover_temperature_C_has_single_runtime_helper():
    source = Path(metallothermic_step_module.__file__).read_text()

    assert source.count("def _crossover_temperature_C(") == 1
    assert BuiltinMetallothermicStepProvider._crossover_temperature_C(
        "Na", "Fe"
    ) == pytest.approx(1181.5, abs=0.5)
    assert BuiltinMetallothermicStepProvider._crossover_temperature_C(
        "K", "Fe"
    ) == pytest.approx(836.25, abs=0.5)


def test_refused_result_has_policy_refusal_shape():
    provider = BuiltinMetallothermicStepProvider()

    result = provider._refused_result(
        "thermodynamic_margin_nonpositive",
        reductant="K",
        target_oxide="FeO",
        temperature_C=1275.0,
        margin_kJ_per_mol_O2=-8.1,
        crossover_temperature_C=1215.9,
        control_audit=None,
    )

    assert result.status == "refused"
    assert result.transition is None
    assert result.diagnostic["reason_refused"] == "thermodynamic_margin_nonpositive"
    assert result.diagnostic["target_oxide"] == "FeO"
    assert result.diagnostic["margin_kJ_per_mol_O2"] < 0.0


def test_c3_k_shuttle_refuses_1150c_feo_after_v1c_janaf_refit(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    FeO_kg = 100.0
    FeO_mol = FeO_kg / (MOLAR_MASS["FeO"] / 1000.0)
    melt_mol = {"FeO": FeO_mol}

    provider = BuiltinMetallothermicStepProvider()
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": melt_mol,
            "process.metal_phase": {},
            "process.reagent_inventory": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    K_reagent_kg = 30.0
    request = IntentRequest(
        intent=ChemistryIntent.METALLOTHERMIC_STEP,
        account_view=view,
        temperature_C=1150.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_C3_K,
            "reagent_available_kg": K_reagent_kg,
            "dt_hr": 1.0,
        },
    )
    result = provider.dispatch(request)
    assert result.status == "refused"
    assert result.transition is None
    assert result.diagnostic["reason_refused"] == "thermodynamic_margin_nonpositive"
    assert result.diagnostic["reductant"] == "K"
    assert result.diagnostic["target_oxide"] == "FeO"
    assert result.diagnostic["margin_kJ_per_mol_O2"] < 0.0


def test_c3_na_shuttle_flags_ellingham_pair_fit_band_extrapolation(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    FeO_kg = 10.0
    FeO_mol = FeO_kg / (MOLAR_MASS["FeO"] / 1000.0)
    provider = BuiltinMetallothermicStepProvider()
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": {"FeO": FeO_mol},
            "process.metal_phase": {},
            "process.reagent_inventory": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    request = IntentRequest(
        intent=ChemistryIntent.METALLOTHERMIC_STEP,
        account_view=view,
        temperature_C=800.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_C3_NA,
            "na_target_stage": "feo_cleanup",
            "reagent_available_kg": 12.0,
            "dt_hr": 1.0,
        },
    )

    result = provider.dispatch(request)

    assert result.status == "ok"
    assert result.transition is not None
    flagged = result.diagnostic[
        "ellingham_extrapolated_beyond_fit_range_K"
    ]
    assert flagged["Na/FeO"]["temperature_K"] == pytest.approx(1073.15)
    assert tuple(flagged["Na/FeO"]["fit_range_K"]) == (1100.0, 2600.0)
    assert result.diagnostic["ellingham_authority"]["consumer"] == (
        "builtin-metallothermic-step"
    )
    assert (
        result.diagnostic["ellingham_authority"][ELLINGHAM_AUTHORITY_LIMIT_FLAG]
        is True
    )
    assert any(
        "Na/FeO Ellingham JANAF high-T fit extrapolated beyond fit_range_K"
        in warning
        for warning in result.warnings
    )


@pytest.mark.parametrize("temperature_C", [1275.0, 1300.0])
def test_c3_k_shuttle_refuses_feo_above_crossover(
    temperature_C, vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    FeO_kg = 100.0
    FeO_mol = FeO_kg / (MOLAR_MASS["FeO"] / 1000.0)
    provider = BuiltinMetallothermicStepProvider()
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": {"FeO": FeO_mol},
            "process.metal_phase": {},
            "process.reagent_inventory": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    request = IntentRequest(
        intent=ChemistryIntent.METALLOTHERMIC_STEP,
        account_view=view,
        temperature_C=temperature_C,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_C3_K,
            "reagent_available_kg": 30.0,
            "dt_hr": 1.0,
        },
    )

    result = provider.dispatch(request)

    assert result.status == "refused"
    assert result.transition is None
    assert result.diagnostic["target_oxide"] == "FeO"
    assert result.diagnostic["margin_kJ_per_mol_O2"] < 0.0


def test_shuttle_refuses_K_FeO_above_crossover_via_kernel(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.atom_ledger.load_external(
        "process.cleaned_melt",
        {"FeO": 100.0 / (MOLAR_MASS["FeO"] / 1000.0)},
        source="test seed",
    )

    result = sim._chem_kernel.dispatch(
        ChemistryIntent.METALLOTHERMIC_STEP,
        temperature_C=1275.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_C3_K,
            "reagent_available_kg": 30.0,
            "dt_hr": 1.0,
        },
    )

    assert result.status == "refused"
    assert result.transition is None
    assert result.diagnostic["target_oxide"] == "FeO"
    assert result.diagnostic["margin_kJ_per_mol_O2"] < 0.0


def test_extraction_records_shuttle_refusal_diagnostic(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Autoreview r3 P2 (2026-05-27): the ``_shuttle_inject_K`` caller
    used to swallow ``status='refused'`` indistinguishably from a
    benign no-op.  Now every refused dispatch must record on
    ``sim._last_shuttle_refusal_diagnostic`` and
    ``sim._shuttle_refusal_history`` so downstream consumers can see
    the recipe step the engine rejected.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    # Seed the melt with FeO so the shuttle has something to attempt.
    sim.atom_ledger.load_external(
        "process.cleaned_melt",
        {"FeO": 100.0 / (MOLAR_MASS["FeO"] / 1000.0)},
        source="refusal recording test seed",
    )
    # Seed reagent inventory so the shuttle gate (which requires
    # ``shuttle_K_inventory_kg > 0.01``) actually fires the kernel
    # dispatch -- without K to inject the function early-returns
    # before the engine sees the operating regime.
    sim.atom_ledger.load_external(
        "process.reagent_inventory",
        {"K": 30.0 / (MOLAR_MASS["K"] / 1000.0)},
        source="refusal recording test seed",
    )
    sim.shuttle_K_inventory_kg = sim._sync_reagent_counter_from_ledger("K")
    # Park the melt above the K/FeO crossover (JANAF-4th multiphase
    # crossover is ~836 °C; this is well above so refusal is
    # deterministic).
    sim.melt.temperature_C = 1275.0
    sim.melt.campaign = CampaignPhase.C3_K
    sim.melt.hour = 24
    sim.melt.campaign_hour = 4

    # Pre-condition: no refusals recorded yet.
    assert sim._shuttle_refusal_history == []
    assert sim._last_shuttle_refusal_diagnostic == {}

    sim._shuttle_inject_K()

    # Post-condition: the refusal IS visible to downstream consumers.
    assert len(sim._shuttle_refusal_history) == 1
    refusal = sim._shuttle_refusal_history[0]
    assert refusal["reaction_family"] == REACTION_FAMILY_C3_K
    assert refusal["reagent"] == "K"
    assert refusal["hour"] == 24
    assert refusal["campaign_hour"] == 4
    assert refusal["campaign"] == CampaignPhase.C3_K.name
    assert refusal["temperature_C"] == pytest.approx(1275.0)
    diag = refusal["diagnostic"]
    assert diag.get("target_oxide") == "FeO"
    assert diag.get("margin_kJ_per_mol_O2", 0.0) < 0.0
    # The "last" attribute mirrors the most recent record.
    assert sim._last_shuttle_refusal_diagnostic == refusal


def test_c3_na_shuttle_accepts_cr_after_nao05_activity_shift(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Cr becomes favorable once NaO0.5 activity is applied; Ti is refused."""

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    # Pick masses so Cr2O3 is reducible with leftover Na available. TiO2
    # must still be refused by the margin gate rather than by reagent
    # exhaustion or fit-band extrapolation.
    Cr2O3_kg = 1.5
    TiO2_kg = 20.0
    Cr2O3_mol = Cr2O3_kg / (MOLAR_MASS["Cr2O3"] / 1000.0)
    TiO2_mol = TiO2_kg / (MOLAR_MASS["TiO2"] / 1000.0)
    melt_mol = {"Cr2O3": Cr2O3_mol, "TiO2": TiO2_mol}

    provider = BuiltinMetallothermicStepProvider()
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": melt_mol,
            "process.metal_phase": {},
            "process.reagent_inventory": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    Na_reagent_kg = 30.0  # 1/3 -> 10 kg available per hour
    request = IntentRequest(
        intent=ChemistryIntent.METALLOTHERMIC_STEP,
        account_view=view,
        temperature_C=1300.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_C3_NA,
            "reagent_available_kg": Na_reagent_kg,
            "dt_hr": 1.0,
        },
    )
    result = provider.dispatch(request)
    proposal = result.transition
    assert result.status == "ok"
    assert proposal is not None
    assert result.diagnostic["reaction_family"] == REACTION_FAMILY_C3_NA
    assert result.diagnostic["target_stage"] == "cr_ti"
    assert result.diagnostic["target_priority"] == ["Cr2O3", "TiO2"]
    assert result.diagnostic["accepted_targets"] == ["Cr2O3"]
    refused = result.diagnostic["refused_targets"]
    assert set(refused) == {"TiO2"}
    assert result.diagnostic["Na2O_activity_gamma"] == pytest.approx(
        MELT_OXIDE_ACTIVITY_COEFFICIENTS["Na2O"].gamma
    )
    assert result.diagnostic["Na2O_activity_component"] == "NaO0.5"
    assert result.diagnostic["Na2O_activity_shift_kJ_per_mol_O2"] < 0.0
    assert result.diagnostic["na_reduction_margin_kJ_per_mol_O2"]["Cr2O3"] > 0.0
    assert result.diagnostic["na_reduction_margin_kJ_per_mol_O2"]["TiO2"] < 0.0
    assert refused["TiO2"]["margin_kJ_per_mol_O2"] < 0.0
    assert "Cr2O3" in proposal.debits["process.cleaned_melt"]
    assert "TiO2" not in proposal.debits["process.cleaned_melt"]


def test_c3_na_shuttle_refuses_in_band_tio2_negative_margin(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinMetallothermicStepProvider()
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": {
                "TiO2": 20.0 / (MOLAR_MASS["TiO2"] / 1000.0),
            },
            "process.metal_phase": {},
            "process.reagent_inventory": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    result = provider.dispatch(
        IntentRequest(
            intent=ChemistryIntent.METALLOTHERMIC_STEP,
            account_view=view,
            temperature_C=1300.0,
            pressure_bar=1e-6,
            control_inputs={
                "reaction_family": REACTION_FAMILY_C3_NA,
                "target_oxides": ["TiO2"],
                "reagent_available_kg": 30.0,
                "dt_hr": 1.0,
            },
        )
    )

    assert result.status == "refused"
    assert result.transition is None
    assert result.diagnostic["target_priority"] == ["TiO2"]
    assert result.diagnostic["accepted_targets"] == []
    refused = result.diagnostic["refused_targets"]
    assert set(refused) == {"TiO2"}
    assert result.diagnostic["na_reduction_margin_kJ_per_mol_O2"]["TiO2"] < 0.0
    assert refused["TiO2"]["margin_kJ_per_mol_O2"] < 0.0
    assert "ellingham_extrapolated_beyond_fit_range_K" not in result.diagnostic


def _c3_na_feo_cleanup_request(sim, *, liquid_fraction, na_kg=12.0):
    return IntentRequest(
        intent=ChemistryIntent.METALLOTHERMIC_STEP,
        account_view=ProviderAccountView(
            accounts={
                "process.cleaned_melt": {
                    "FeO": 10.0 / (MOLAR_MASS["FeO"] / 1000.0),
                },
                "process.metal_phase": {},
                "process.reagent_inventory": {},
            },
            species_formula_registry=sim.species_formula_registry,
        ),
        temperature_C=1150.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_C3_NA,
            "na_target_stage": "feo_cleanup",
            "reagent_available_kg": na_kg,
            "liquid_fraction": liquid_fraction,
            "dt_hr": 1.0,
        },
    )


def test_metallothermic_provider_refuses_nonfinite_temperature(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    request = replace(
        _c3_na_feo_cleanup_request(sim, liquid_fraction=1.0),
        temperature_C=float("nan"),
    )

    result = BuiltinMetallothermicStepProvider().dispatch(request)

    assert result.status == "unsupported"
    assert result.transition is None


def test_c3_na_unknown_target_stage_is_refused_without_chemistry(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    request = _c3_na_feo_cleanup_request(sim, liquid_fraction=1.0)
    controls = dict(request.control_inputs)
    controls["na_target_stage"] = "typo"

    result = BuiltinMetallothermicStepProvider().dispatch(
        replace(request, control_inputs=controls)
    )

    assert result.status == "refused"
    assert result.transition is None
    assert result.diagnostic["reason_refused"] == "unknown_na_target_stage"


def _prepare_stranded_staged_na_hold(sim, *, configured_temperature_C=1250.0):
    sim.record.path = "A_staged"
    sim.melt.campaign = CampaignPhase.C3_NA
    sim.campaign_mgr._active_c3_na_scoped_overrides = {
        "inject_target_C": configured_temperature_C,
        "bakeout_target_C": configured_temperature_C,
        "ramp_rate": 600.0,
        "staged_duration_h": 3.0,
    }
    sim._init_shuttle_inventory(CampaignPhase.C3_NA)


def _assert_selected_na_fe_yield_argmax(
    diagnostic,
    *,
    configured_temperature_C,
):
    crossover_temperature_C = (
        BuiltinMetallothermicStepProvider._crossover_temperature_C("Na", "Fe")
    )
    assert crossover_temperature_C is not None
    assert diagnostic["crossover_temperature_C"] == pytest.approx(
        crossover_temperature_C
    )
    assert diagnostic["selection_tie_break"] == (
        "closest feasible argmax to configured_temperature_C"
    )

    feasible_rows = [
        row
        for row in diagnostic["rows"]
        if (
            row["status"] == "ok"
            and row["Fe_produced_kg"] > 0.0
        )
    ]
    assert feasible_rows
    max_fe_kg = max(row["Fe_produced_kg"] for row in feasible_rows)
    argmax_rows = [
        row
        for row in feasible_rows
        if abs(row["Fe_produced_kg"] - max_fe_kg) <= 1.0e-12
    ]
    expected = min(
        argmax_rows,
        key=lambda row: (
            abs(row["temperature_C"] - configured_temperature_C),
            row["temperature_C"],
        ),
    )
    selected = next(
        row
        for row in feasible_rows
        if row["temperature_C"] == diagnostic["selected_temperature_C"]
    )
    assert selected == expected
    assert diagnostic["selected_Fe_produced_kg"] == pytest.approx(max_fe_kg)
    assert selected["temperature_C"] < crossover_temperature_C
    assert selected["margin_kJ_per_mol_O2"] > 0.0
    return selected


def test_staged_na_fe_hold_recomputes_stranded_temperature_by_fe_yield_sweep(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg={"Na": 30.0},
    )
    _prepare_stranded_staged_na_hold(sim)
    curve = {
        "source": "test:liquid-na-fe-window",
        "solidus_T_C": 1000.0,
        "liquidus_T_C": 1100.0,
        "path": ((1000.0, 0.0), (1100.0, 1.0)),
    }
    monkeypatch.setattr(sim, "_melt_redox_liquidus_gate_curve", lambda: curve)

    sim._recompute_staged_na_fe_hold_setpoint()

    diagnostic = sim._last_c3_na_hold_adjustment
    assert diagnostic["status"] == "applied"
    assert diagnostic["configured_temperature_C"] == pytest.approx(1250.0)
    assert diagnostic["selected_Fe_produced_kg"] > 0.0
    assert diagnostic["liquid_fraction_curve"]["source"] == curve["source"]
    assert diagnostic["rows"]
    selected_row = _assert_selected_na_fe_yield_argmax(
        diagnostic,
        configured_temperature_C=1250.0,
    )
    assert selected_row["liquid_fraction"] > 0.0
    target, _ = sim.campaign_mgr.get_temp_target(
        CampaignPhase.C3_NA,
        0,
        sim.melt,
    )
    assert target == pytest.approx(diagnostic["selected_temperature_C"])
    bakeout_target, _ = sim.campaign_mgr.get_temp_target(
        CampaignPhase.C3_NA,
        4,
        sim.melt,
    )
    assert bakeout_target == pytest.approx(1250.0)

    sim.melt.temperature_C = target
    sim._shuttle_inject_Na(target_stage="feo_cleanup", liquid_fraction=1.0)
    assert sim._ledger_account_species_kg("process.metal_phase", "Fe") > 0.0
    assert sim._shuttle_refusal_history == []


def test_staged_na_fe_hold_recomputes_when_only_partial_melt_window_is_feasible(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg={"Na": 30.0},
    )
    _prepare_stranded_staged_na_hold(sim)
    curve = {
        "source": "test:partial-melt-only-na-fe-window",
        "solidus_T_C": 1100.0,
        "liquidus_T_C": 1500.0,
        "path": ((1100.0, 0.0), (1500.0, 1.0)),
    }
    monkeypatch.setattr(sim, "_melt_redox_liquidus_gate_curve", lambda: curve)

    sim._recompute_staged_na_fe_hold_setpoint()

    diagnostic = sim._last_c3_na_hold_adjustment
    assert diagnostic["status"] == "applied"
    selected_row = _assert_selected_na_fe_yield_argmax(
        diagnostic,
        configured_temperature_C=1250.0,
    )
    assert 0.0 < selected_row["liquid_fraction"] < 0.5
    assert all(
        0.0 < row["liquid_fraction"] < 0.5
        for row in diagnostic["rows"]
        if row["status"] == "ok"
    )


def test_staged_na_fe_hold_solves_subdegree_joint_window(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg={"Na": 30.0},
    )
    _prepare_stranded_staged_na_hold(sim)
    curve = {
        "source": "test:reviewer-subdegree-na-fe-window",
        "solidus_T_C": 1100.0,
        "liquidus_T_C": 1181.4,
        "path": (
            (1100.0, 0.0),
            (1181.2, 0.0),
            (1181.4, 0.1),
        ),
    }
    monkeypatch.setattr(sim, "_melt_redox_liquidus_gate_curve", lambda: curve)

    sim._recompute_staged_na_fe_hold_setpoint()

    diagnostic = sim._last_c3_na_hold_adjustment
    assert diagnostic["status"] == "applied"
    assert diagnostic["candidate_rule"] == (
        "solved boundaries plus curve sample knots"
    )
    assert diagnostic["first_positive_liquid_temperature_C"] == pytest.approx(
        1181.2,
        abs=diagnostic["boundary_tolerance_C"],
    )
    assert diagnostic["joint_window_T_min_exclusive_C"] == (
        diagnostic["first_positive_liquid_temperature_C"]
    )
    assert any(
        row["temperature_C"] == pytest.approx(1181.4)
        and row["status"] == "ok"
        for row in diagnostic["rows"]
    )
    selected = _assert_selected_na_fe_yield_argmax(
        diagnostic,
        configured_temperature_C=1250.0,
    )
    assert 1181.2 < selected["temperature_C"] < 1181.5
    assert selected["liquid_fraction"] > 0.0


def test_staged_na_fe_hold_refuses_non_monotone_liquid_fraction_island(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg={"Na": 30.0},
    )
    _prepare_stranded_staged_na_hold(sim)
    curve = {
        "source": "test:non-monotone-na-fe-window",
        "solidus_T_C": 1100.0,
        "liquidus_T_C": 1250.0,
        "path": (
            (1100.0, 0.0),
            (1150.0, 0.2),
            (1170.0, 0.0),
            (1200.0, 0.0),
            (1250.0, 1.0),
        ),
    }
    monkeypatch.setattr(sim, "_melt_redox_liquidus_gate_curve", lambda: curve)

    sim._recompute_staged_na_fe_hold_setpoint()

    diagnostic = sim._last_c3_na_hold_adjustment
    assert diagnostic["status"] == "unavailable"
    assert diagnostic["reason"] == (
        "lf_curve_non_monotone_window_unresolved"
    )
    assert diagnostic["liquid_fraction_curve"]["source"] == curve["source"]
    assert sim._interpolate_freeze_gate_curve(
        curve,
        diagnostic["monotonicity_validation_window_T_max_C"],
    ) == 0.0
    assert diagnostic["monotonicity_violation"]["earlier_liquid_fraction"] > (
        diagnostic["monotonicity_violation"]["later_liquid_fraction"]
    )
    target, _ = sim.campaign_mgr.get_temp_target(
        CampaignPhase.C3_NA,
        0,
        sim.melt,
    )
    assert target == pytest.approx(1250.0)


def test_staged_na_fe_hold_keeps_typed_refusal_when_joint_window_is_empty(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg={"Na": 30.0},
    )
    _prepare_stranded_staged_na_hold(sim)
    curve = {
        "source": "test:no-liquid-below-na-fe-crossover",
        "solidus_T_C": 1190.0,
        "liquidus_T_C": 1250.0,
        "path": ((1190.0, 0.0), (1250.0, 1.0)),
    }
    monkeypatch.setattr(sim, "_melt_redox_liquidus_gate_curve", lambda: curve)

    sim._recompute_staged_na_fe_hold_setpoint()

    diagnostic = sim._last_c3_na_hold_adjustment
    assert diagnostic["status"] == "empty"
    assert diagnostic["reason"] == "na_fe_hold_window_empty"
    assert diagnostic["accepted_residual_window_floor_C"] == pytest.approx(
        1.0e-9
    )
    assert diagnostic["rows"] == []
    target, _ = sim.campaign_mgr.get_temp_target(
        CampaignPhase.C3_NA,
        0,
        sim.melt,
    )
    assert target == pytest.approx(1250.0)

    sim.melt.temperature_C = target
    sim._shuttle_inject_Na(target_stage="feo_cleanup", liquid_fraction=1.0)
    refusal = sim._shuttle_refusal_history[-1]
    assert refusal["diagnostic"]["reason_refused"] == (
        "thermodynamic_margin_nonpositive"
    )


def _c3_k_feo_request(sim, *, liquid_fraction, k_kg=30.0):
    return IntentRequest(
        intent=ChemistryIntent.METALLOTHERMIC_STEP,
        account_view=ProviderAccountView(
            accounts={
                "process.cleaned_melt": {
                    "FeO": 10.0 / (MOLAR_MASS["FeO"] / 1000.0),
                },
                "process.metal_phase": {},
                "process.reagent_inventory": {},
            },
            species_formula_registry=sim.species_formula_registry,
        ),
        temperature_C=1150.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_C3_K,
            "reagent_available_kg": k_kg,
            "liquid_fraction": liquid_fraction,
            "dt_hr": 1.0,
        },
    )


def test_metallothermic_dispatch_consumes_only_phase_context_scalar_tier(
    vapor_pressure_data, feedstocks_data, setpoints_data, monkeypatch,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    captured = {}

    class _CaptureKernel:
        def dispatch(self, intent, **kwargs):
            captured.update(kwargs)
            return "proposal"

    sim._chem_kernel = _CaptureKernel()
    phase_context_calls = []

    def _tier_one_context(*args, **kwargs):
        phase_context_calls.append((args, kwargs))
        return {
            "FeO": {
                "liquid_fraction": 0.0,
                "activity_basis": "forbidden_tier_one_value",
                "provenance": {"selected_tier": "grind_cache_assemblage"},
            }
        }

    monkeypatch.setattr(phase_context_module, "PhaseContext", _tier_one_context)
    result = sim._dispatch_only(
        ChemistryIntent.METALLOTHERMIC_STEP,
        control_inputs={"liquid_fraction": 0.375},
    )

    assert result == "proposal"
    assert len(phase_context_calls) == 1
    assert captured["control_inputs"] == {"liquid_fraction": 0.375}

    sim._dispatch_only(
        ChemistryIntent.METALLOTHERMIC_STEP,
        control_inputs={"liquid_fraction": None},
    )

    assert len(phase_context_calls) == 2
    assert captured["control_inputs"] == {"liquid_fraction": None}


def test_c3_na_draw_uses_true_ledger_availability_not_quantized_view(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinMetallothermicStepProvider()
    true_feo_kg = 10.0
    quantized_feo_kg = true_feo_kg * 1.000053
    true_feo_mol = true_feo_kg / (MOLAR_MASS["FeO"] / 1000.0)
    quantized_feo_mol = quantized_feo_kg / (MOLAR_MASS["FeO"] / 1000.0)
    na_kg_for_full_true_draw = (
        true_feo_mol * 2.0 * MOLAR_MASS["Na"] / 1000.0 * 3.1
    )

    result = provider.dispatch(
        IntentRequest(
            intent=ChemistryIntent.METALLOTHERMIC_STEP,
            account_view=ProviderAccountView(
                accounts={
                    "process.cleaned_melt": {
                        "SiO2": 1000.0 / (MOLAR_MASS["SiO2"] / 1000.0),
                        "FeO": quantized_feo_mol,
                    },
                    "process.metal_phase": {},
                    "process.reagent_inventory": {},
                },
                species_formula_registry=sim.species_formula_registry,
            ),
            temperature_C=1150.0,
            pressure_bar=1e-6,
            control_inputs={
                "reaction_family": REACTION_FAMILY_C3_NA,
                "na_target_stage": "feo_cleanup",
                "reagent_available_kg": na_kg_for_full_true_draw,
                "true_available_mol_by_species": {"FeO": true_feo_mol},
                "dt_hr": 1.0,
            },
        )
    )

    assert result.status == "ok"
    assert result.transition is not None
    debited = result.transition.debits["process.cleaned_melt"]["FeO"]
    assert debited == pytest.approx(true_feo_mol)
    assert debited < quantized_feo_mol


def test_c3_na_explicit_duplicate_targets_do_not_double_debit_feo(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinMetallothermicStepProvider()
    feo_mol = 10.0 / (MOLAR_MASS["FeO"] / 1000.0)

    result = provider.dispatch(
        IntentRequest(
            intent=ChemistryIntent.METALLOTHERMIC_STEP,
            account_view=ProviderAccountView(
                accounts={
                    "process.cleaned_melt": {"FeO": feo_mol},
                    "process.metal_phase": {},
                    "process.reagent_inventory": {},
                },
                species_formula_registry=sim.species_formula_registry,
            ),
            temperature_C=1150.0,
            pressure_bar=1e-6,
            control_inputs={
                "reaction_family": REACTION_FAMILY_C3_NA,
                "target_oxides": ["FeO", "FeO"],
                "reagent_available_kg": 30.0,
                "liquid_fraction": 1.0,
                "dt_hr": 1.0,
            },
        )
    )

    assert result.status == "ok"
    assert result.transition is not None
    assert result.diagnostic["target_priority"] == ["FeO"]
    assert result.diagnostic["accepted_targets"] == ["FeO"]
    removed_kg_per_na2o_kg = MOLAR_MASS["FeO"] / MOLAR_MASS["Na2O"]
    na2o_cap_kg = (10.0 * 0.10) / (
        1.0 - 0.10 * (1.0 - removed_kg_per_na2o_kg)
    )
    na2o_limited_mol = na2o_cap_kg / (MOLAR_MASS["Na2O"] / 1000.0)
    assert result.transition.debits["process.cleaned_melt"]["FeO"] == pytest.approx(
        min(feo_mol, na2o_limited_mol)
    )


def test_empty_or_all_invalid_na_targets_do_not_widen_to_default_set():
    """SC-47: an explicitly-provided ``target_oxides`` that normalises to
    empty (an empty list, an empty string, or every entry unrecognised) must
    reduce NOTHING -- it must NOT silently widen to the default Cr/Ti
    Na-target set. Mirrors the BUG-140 empty-filter contract. The None case
    (no ``target_oxides`` key at all) is the caller's stage-default path,
    handled separately by ``_resolve_na_target_priority``'s ``is not None``.
    """
    normalize = BuiltinMetallothermicStepProvider._normalize_na_targets
    default = NA_STAGE_TARGETS[NA_TARGET_CR_TI]
    assert default  # default set is non-empty, so the checks below actually bite

    # Explicit-empty / all-invalid -> reduce nothing (), NOT the default set
    # (pre-SC-47 this returned `tuple(targets) or default` == the Cr/Ti set).
    assert normalize([]) == ()
    assert normalize("") == ()
    assert normalize(["NotAnOxide", "AlsoBogus"]) == ()
    assert normalize([]) != tuple(default)

    # Control: a valid (de-duplicated) target list is unchanged.
    assert normalize(["FeO", "FeO"]) == ("FeO",)


def test_empty_na_targets_dispatch_reduces_nothing_not_default_set(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """SC-47 dispatch-level teeth (mirrors BUG-140's end-to-end test): an
    explicit empty ``target_oxides`` drives the metallothermic step to reduce
    NOTHING (clean ``status=='ok'``, no transition), and must NOT widen to the
    default Cr/Ti set. Pre-fix the empty list normalised to the cr_ti default,
    so the step entered the reduce-Cr/Ti path and returned ``status=='refused'``
    (margin non-positive at 1150 C) -- this asserts the post-fix ``'ok'``
    no-op, which also proves the empty-priority dispatch path does not crash.
    """
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinMetallothermicStepProvider()
    melt = {
        "FeO": 10.0 / (MOLAR_MASS["FeO"] / 1000.0),
        "Cr2O3": 5.0 / (MOLAR_MASS["Cr2O3"] / 1000.0),
        "TiO2": 5.0 / (MOLAR_MASS["TiO2"] / 1000.0),
    }
    result = provider.dispatch(
        IntentRequest(
            intent=ChemistryIntent.METALLOTHERMIC_STEP,
            account_view=ProviderAccountView(
                accounts={
                    "process.cleaned_melt": dict(melt),
                    "process.metal_phase": {},
                    "process.reagent_inventory": {},
                },
                species_formula_registry=sim.species_formula_registry,
            ),
            temperature_C=1150.0,
            pressure_bar=1e-6,
            control_inputs={
                "reaction_family": REACTION_FAMILY_C3_NA,
                "target_oxides": [],
                "reagent_available_kg": 30.0,
                "liquid_fraction": 1.0,
                "dt_hr": 1.0,
            },
        )
    )

    assert result.status == "ok"
    assert result.transition is None


def test_c3_na_shuttle_returns_na2o_to_spent_reductant_residue(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinMetallothermicStepProvider()

    result = provider.dispatch(
        _c3_na_feo_cleanup_request(sim, liquid_fraction=1.0)
    )

    assert result.status == "ok"
    assert result.transition is not None
    assert result.transition.credits[SPENT_REDUCTANT_RESIDUE_ACCOUNT]["Na2O"] > 0.0
    assert "Na2O" not in result.transition.credits.get(
        "process.reagent_inventory", {}
    )
    assert "Na2O" not in result.transition.credits.get("process.cleaned_melt", {})


def test_c3_k_shuttle_primary_refuses_no_liquid_before_ellingham(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinMetallothermicStepProvider()
    result = provider.dispatch(
        IntentRequest(
            intent=ChemistryIntent.METALLOTHERMIC_STEP,
            account_view=ProviderAccountView(
                accounts={
                    "process.cleaned_melt": {
                        "FeO": 10.0 / (MOLAR_MASS["FeO"] / 1000.0),
                    },
                    "process.metal_phase": {},
                    "process.reagent_inventory": {},
                },
                species_formula_registry=sim.species_formula_registry,
            ),
            temperature_C=1150.0,
            pressure_bar=1e-6,
            control_inputs={
                "reaction_family": REACTION_FAMILY_C3_K,
                "reagent_available_kg": 30.0,
                "liquid_fraction": 0.0,
                "dt_hr": 1.0,
            },
        )
    )

    assert result.status == "refused"
    assert result.diagnostic["reason_refused"] == "no_liquid_phase"
    assert result.diagnostic["reaction_family"] == REACTION_FAMILY_C3_K


def test_c3_k_shuttle_not_numeric_liquid_fraction_preserves_legacy_raise(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinMetallothermicStepProvider()

    with pytest.raises(ValueError):
        provider.dispatch(
            _c3_k_feo_request(sim, liquid_fraction="not_numeric")
        )


def test_c3_k_shuttle_invalid_liquid_fraction_preserves_legacy_fallthrough(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinMetallothermicStepProvider()

    result = provider.dispatch(
        _c3_k_feo_request(sim, liquid_fraction=1.1)
    )

    assert result.status == "refused"
    assert result.diagnostic["reason_refused"] != "no_liquid_phase"
    divergence = result.diagnostic["melt_regime_predicate_divergences"][0]
    assert divergence["site"] == (
        "engines.builtin.metallothermic_step.liquid_fraction"
    )
    assert divergence["effective_regime"] == "partial"
    assert divergence["liquid_fraction_invalid"] == "out_of_range"


def test_c3_na_shuttle_primary_refuses_no_liquid(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinMetallothermicStepProvider()
    result = provider.dispatch(
        _c3_na_feo_cleanup_request(sim, liquid_fraction=0.0)
    )

    assert result.status == "refused"
    assert result.transition is None
    assert result.diagnostic["reason_refused"] == "no_liquid_phase"
    assert result.diagnostic["reason"] == "no_liquid_phase"
    assert result.diagnostic["reaction_family"] == REACTION_FAMILY_C3_NA
    assert result.diagnostic["liquid_fraction"] == 0.0
    assert result.diagnostic["reagent_consumed_kg"] == 0.0
    assert result.diagnostic["oxide_reduced_kg"] == 0.0
    assert result.diagnostic["metal_produced_kg"] == 0.0
    per_oxide = result.diagnostic.get("per_oxide_reduced_kg") or {}
    assert per_oxide.get("FeO", 0.0) == 0.0


@pytest.mark.parametrize("liquid_fraction", [None, 0.25])
def test_c3_na_shuttle_primary_reduces_feo_with_liquid_or_unknown(
    vapor_pressure_data, feedstocks_data, setpoints_data, liquid_fraction
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinMetallothermicStepProvider()
    result = provider.dispatch(
        _c3_na_feo_cleanup_request(sim, liquid_fraction=liquid_fraction)
    )

    assert result.status == "ok"
    assert result.transition is not None
    assert result.diagnostic["per_oxide_reduced_kg"]["FeO"] > 0.0


@pytest.mark.parametrize(
    "liquid_fraction",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(-0.1, id="negative"),
        pytest.param(float("inf"), id="inf"),
    ],
)
def test_c3_na_shuttle_invalid_liquid_fraction_preserves_legacy_fallthrough(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    liquid_fraction,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinMetallothermicStepProvider()

    result = provider.dispatch(
        _c3_na_feo_cleanup_request(sim, liquid_fraction=liquid_fraction)
    )

    assert result.status == "ok"
    assert result.transition is not None
    assert result.diagnostic["per_oxide_reduced_kg"]["FeO"] > 0.0
    divergence = result.diagnostic["melt_regime_predicate_divergences"][0]
    assert divergence["site"] == (
        "engines.builtin.metallothermic_step.liquid_fraction"
    )
    assert divergence["effective_regime"] == "partial"
    assert divergence["canonical_error"]
    assert divergence["liquid_fraction_invalid"] in {
        "non_finite",
        "out_of_range",
    }


def test_c3_na_shuttle_inject_no_liquid_no_reagent_leak(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    feo_mol = 10.0 / (MOLAR_MASS["FeO"] / 1000.0)
    sim.atom_ledger.load_external(
        "process.cleaned_melt",
        {"FeO": feo_mol},
        source="no-liquid shuttle leak test",
    )
    sim.atom_ledger.load_external(
        "process.reagent_inventory",
        {"Na": 12.0 / (MOLAR_MASS["Na"] / 1000.0)},
        source="no-liquid shuttle leak test",
    )
    sim.shuttle_Na_inventory_kg = sim._sync_reagent_counter_from_ledger("Na")
    na_mol_before = sim.atom_ledger.mol_by_account("process.reagent_inventory").get(
        "Na", 0.0
    )
    feo_mol_before = sim.atom_ledger.mol_by_account("process.cleaned_melt").get(
        "FeO", 0.0
    )
    sim.melt.temperature_C = 1150.0
    sim.melt.campaign = CampaignPhase.C3_NA
    transitions_before = len(sim.atom_ledger.transitions)

    sim._shuttle_inject_Na(
        target_stage="feo_cleanup",
        liquid_fraction=0.0,
    )

    assert len(sim.atom_ledger.transitions) == transitions_before
    assert sim.atom_ledger.mol_by_account("process.reagent_inventory").get(
        "Na", 0.0
    ) == pytest.approx(na_mol_before)
    assert sim.atom_ledger.mol_by_account("process.cleaned_melt").get(
        "FeO", 0.0
    ) == pytest.approx(feo_mol_before)


@pytest.mark.parametrize("liquid_fraction", [None, 0.25])
def test_c6_mg_thermite_primary_matches_legacy_stoich(
    vapor_pressure_data, feedstocks_data, setpoints_data, liquid_fraction
):
    """Drive the provider with an Al2O3 melt + Mg reagent; assert the
    proposal matches the 3 Mg + Al2O3 -> 3 MgO + 2 Al stoichiometry
    line-for-line with the legacy ``_step_thermite`` rate-factor
    expression (``0.20 * exp(-0.05 * wt%MgO)``).
    """

    import math

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    Al2O3_kg = 100.0
    Al2O3_mol = Al2O3_kg / (MOLAR_MASS["Al2O3"] / 1000.0)
    # Add some MgO so the rate factor is < 0.20.
    MgO_kg = 20.0
    MgO_mol = MgO_kg / (MOLAR_MASS["MgO"] / 1000.0)
    melt_mol = {"Al2O3": Al2O3_mol, "MgO": MgO_mol}

    provider = BuiltinMetallothermicStepProvider()
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": melt_mol,
            "process.metal_phase": {},
            "process.reagent_inventory": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    Mg_reagent_kg = 50.0
    request = IntentRequest(
        intent=ChemistryIntent.METALLOTHERMIC_STEP,
        account_view=view,
        temperature_C=1700.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_C6_MG,
            "reagent_available_kg": Mg_reagent_kg,
            "liquid_fraction": liquid_fraction,
            "JANAF_4th_multiphase_margin_kJ_per_mol_O2": {
                "Mg_Al_crossover_C": 1471.4,
            },
            "kinetic_driven_above_crossover": True,
            "kinetic_note": "test-local thermite support",
            "dt_hr": 1.0,
        },
    )
    result = provider.dispatch(request)
    proposal = result.transition
    assert proposal is not None
    assert result.diagnostic["reaction_family"] == REACTION_FAMILY_C6_MG
    assert result.diagnostic["back_reduction"] is False

    # Legacy re-derivation. total_kg = Al2O3_kg + MgO_kg = 120.
    # MgO wt% = 20/120 * 100 ~= 16.67%. rate_factor = 0.20 * exp(
    # -0.05 * 16.67) ~= 0.0867, clamped [0.01, 0.25].
    total_kg = Al2O3_kg + MgO_kg
    MgO_pct = MgO_kg / total_kg * 100.0
    rate_factor = 0.20 * math.exp(-0.05 * MgO_pct)
    rate_factor = max(0.01, min(0.25, rate_factor))
    Mg_available_this_hr = Mg_reagent_kg * rate_factor
    mol_Mg = Mg_available_this_hr / MOLAR_MASS["Mg"] * 1000.0
    mol_Al2O3_avail = Al2O3_kg / MOLAR_MASS["Al2O3"] * 1000.0
    mol_Al2O3_reduced = min(mol_Mg / 3.0, mol_Al2O3_avail)
    mol_Mg_used = mol_Al2O3_reduced * 3.0

    assert proposal.debits["process.reagent_inventory"]["Mg"] == pytest.approx(
        mol_Mg_used, abs=1e-12, rel=1e-12
    )
    assert proposal.debits["process.cleaned_melt"]["Al2O3"] == pytest.approx(
        mol_Al2O3_reduced, abs=1e-12, rel=1e-12
    )
    assert proposal.credits["process.cleaned_melt"]["MgO"] == pytest.approx(
        mol_Al2O3_reduced * 3.0, abs=1e-12, rel=1e-12
    )
    assert proposal.credits["process.metal_phase"]["Al"] == pytest.approx(
        mol_Al2O3_reduced * 2.0, abs=1e-12, rel=1e-12
    )

    # mol_Al_produced is on the diagnostic for the back-reduction
    # chain (the caller passes it back in as control_input to the
    # second dispatch).
    assert result.diagnostic["mol_Al_produced"] == pytest.approx(
        mol_Al2O3_reduced * 2.0, abs=1e-12, rel=1e-12
    )

    _atom_check(proposal, sim.species_formula_registry, tol=1e-12)


def _c6_mg_primary_request(sim, *, liquid_fraction):
    Al2O3_kg = 100.0
    Al2O3_mol = Al2O3_kg / (MOLAR_MASS["Al2O3"] / 1000.0)
    MgO_kg = 20.0
    MgO_mol = MgO_kg / (MOLAR_MASS["MgO"] / 1000.0)
    return IntentRequest(
        intent=ChemistryIntent.METALLOTHERMIC_STEP,
        account_view=ProviderAccountView(
            accounts={
                "process.cleaned_melt": {
                    "Al2O3": Al2O3_mol,
                    "MgO": MgO_mol,
                },
                "process.metal_phase": {},
                "process.reagent_inventory": {},
            },
            species_formula_registry=sim.species_formula_registry,
        ),
        temperature_C=1700.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_C6_MG,
            "reagent_available_kg": 50.0,
            "liquid_fraction": liquid_fraction,
            "JANAF_4th_multiphase_margin_kJ_per_mol_O2": {
                "Mg_Al_crossover_C": 1471.4,
            },
            "kinetic_driven_above_crossover": True,
            "kinetic_note": "test-local thermite support",
            "dt_hr": 1.0,
        },
    )


def test_c6_mg_thermite_invalid_liquid_fraction_preserves_legacy_fallthrough(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinMetallothermicStepProvider()

    result = provider.dispatch(
        _c6_mg_primary_request(sim, liquid_fraction=float("inf"))
    )

    assert result.status == "ok"
    assert result.transition is not None
    divergence = result.diagnostic["melt_regime_predicate_divergences"][0]
    assert divergence["site"] == (
        "engines.builtin.metallothermic_step.liquid_fraction"
    )
    assert divergence["effective_regime"] == "partial"
    assert divergence["liquid_fraction_invalid"] == "non_finite"


def test_c6_mg_thermite_refuses_above_crossover_without_local_support(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinMetallothermicStepProvider()

    result = provider.dispatch(
        IntentRequest(
            intent=ChemistryIntent.METALLOTHERMIC_STEP,
            account_view=_c6_mg_primary_request(
                sim,
                liquid_fraction=None,
            ).account_view,
            temperature_C=1500.0,
            pressure_bar=1e-6,
            control_inputs={
                "reaction_family": REACTION_FAMILY_C6_MG,
                "reagent_available_kg": 50.0,
                "liquid_fraction": None,
                "JANAF_4th_multiphase_margin_kJ_per_mol_O2": {
                    "Mg_Al_crossover_C": 1471.4,
                    "Mg_Al_1500C": -5.6,
                },
                "kinetic_driven_above_crossover": False,
                "dt_hr": 1.0,
            },
        )
    )

    assert result.status == "refused"
    assert result.transition is None
    assert result.diagnostic["reason_refused"] == (
        BuiltinMetallothermicStepProvider.C6_ABOVE_CROSSOVER_REFUSAL
    )
    assert result.diagnostic["c6_above_mg_al_crossover"] is True
    assert result.diagnostic["c6_local_thermite_support"] is False
    assert result.diagnostic["c6_mg_al_margin_kJ_per_mol_O2"] < 0.0


def test_c6_mg_thermite_proceeds_at_static_hold_without_local_support(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinMetallothermicStepProvider()

    request = _c6_mg_primary_request(sim, liquid_fraction=1.0)
    request = IntentRequest(
        intent=request.intent,
        account_view=request.account_view,
        # The yield response is numerically flat through 1400-1450 C; the
        # recipe tie-break chooses the colder point for margin headroom.
        temperature_C=1400.0,
        pressure_bar=request.pressure_bar,
        control_inputs={
            **request.control_inputs,
            "JANAF_4th_multiphase_margin_kJ_per_mol_O2": {
                "Mg_Al_crossover_C": 1471.4,
                "Mg_Al_1400C": 13.864,
            },
            "kinetic_driven_above_crossover": False,
            "kinetic_note": "",
        },
    )

    result = provider.dispatch(request)

    assert result.status == "ok"
    assert result.transition is not None
    assert result.diagnostic["reaction_family"] == REACTION_FAMILY_C6_MG
    assert result.diagnostic["c6_above_mg_al_crossover"] is False
    assert result.diagnostic["c6_local_thermite_support"] is False
    assert result.diagnostic["c6_mg_al_margin_kJ_per_mol_O2"] == pytest.approx(
        13.863535266150052
    )


def test_c6_mg_thermite_primary_refuses_no_liquid(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    Al2O3_kg = 100.0
    Al2O3_mol = Al2O3_kg / (MOLAR_MASS["Al2O3"] / 1000.0)
    provider = BuiltinMetallothermicStepProvider()
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": {"Al2O3": Al2O3_mol},
            "process.metal_phase": {},
            "process.reagent_inventory": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )

    result = provider.dispatch(
        IntentRequest(
            intent=ChemistryIntent.METALLOTHERMIC_STEP,
            account_view=view,
            temperature_C=1500.0,
            pressure_bar=1e-6,
            control_inputs={
                "reaction_family": REACTION_FAMILY_C6_MG,
                "reagent_available_kg": 50.0,
                "liquid_fraction": 0.0,
                "dt_hr": 1.0,
            },
        )
    )

    assert result.status == "refused"
    assert result.transition is None
    assert result.diagnostic["reason_refused"] == "no_liquid_phase"
    assert result.diagnostic["reason"] == "no_liquid_phase"
    assert result.diagnostic["reaction_family"] == REACTION_FAMILY_C6_MG
    assert result.diagnostic["back_reduction"] is False
    assert result.diagnostic["liquid_fraction"] == 0.0
    assert result.diagnostic["reagent_consumed_kg"] == 0.0
    assert result.diagnostic["oxide_reduced_kg"] == 0.0
    assert result.diagnostic["coproduct_kg"] == 0.0
    assert result.diagnostic["metal_produced_kg"] == 0.0
    assert result.diagnostic["mol_Al_produced"] == 0.0


def test_c6_back_reduction_matches_legacy_stoich(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Drive the provider in back-reduction mode with a known Al
    quantity + SiO2 melt; assert 4 Al + 3 SiO2 -> 2 Al2O3 + 3 Si
    stoichiometry holds at the legacy 30 % consumption fraction.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    SiO2_kg = 50.0
    Al2O3_kg = 10.0
    SiO2_mol = SiO2_kg / (MOLAR_MASS["SiO2"] / 1000.0)
    Al2O3_mol = Al2O3_kg / (MOLAR_MASS["Al2O3"] / 1000.0)
    melt_mol = {"SiO2": SiO2_mol, "Al2O3": Al2O3_mol}
    # 10 mol Al from primary thermite -- 30 % goes to back-reduction.
    mol_Al_produced = 10.0

    provider = BuiltinMetallothermicStepProvider()
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": melt_mol,
            # Back-reduction may only debit Al that the matched primary
            # transition has already credited to metal_phase.
            "process.metal_phase": {"Al": mol_Al_produced},
            "process.reagent_inventory": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    request = IntentRequest(
        intent=ChemistryIntent.METALLOTHERMIC_STEP,
        account_view=view,
        temperature_C=1700.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_C6_MG,
            "reagent_available_kg": 0.0,  # not used in back-reduction
            "back_reduction": True,
            "mol_Al_produced": mol_Al_produced,
            "liquid_fraction": 1.0,
            "JANAF_4th_multiphase_margin_kJ_per_mol_O2": {
                "Mg_Al_crossover_C": 1471.4,
            },
            "kinetic_driven_above_crossover": True,
            "kinetic_note": "test-local thermite support",
            "dt_hr": 1.0,
        },
    )
    result = provider.dispatch(request)
    proposal = result.transition
    assert proposal is not None
    assert result.diagnostic["back_reduction"] is True

    # Legacy: Al_produced_kg = mol_Al_produced * M_Al / 1000.
    Al_produced_kg = mol_Al_produced * MOLAR_MASS["Al"] / 1000.0
    mol_Al_for_back = (
        Al_produced_kg * 0.30 / MOLAR_MASS["Al"] * 1000.0
    )
    mol_SiO2_avail = SiO2_kg / MOLAR_MASS["SiO2"] * 1000.0
    mol_SiO2_consumed = min(mol_Al_for_back * 3.0 / 4.0, mol_SiO2_avail)
    mol_Al_consumed_legacy = mol_SiO2_consumed * 4.0 / 3.0

    assert proposal.debits["process.metal_phase"]["Al"] == pytest.approx(
        mol_Al_consumed_legacy, abs=1e-12, rel=1e-12
    )
    assert proposal.debits["process.cleaned_melt"]["SiO2"] == pytest.approx(
        mol_SiO2_consumed, abs=1e-12, rel=1e-12
    )
    assert proposal.credits["process.cleaned_melt"]["Al2O3"] == pytest.approx(
        mol_SiO2_consumed * 2.0 / 3.0, abs=1e-12, rel=1e-12
    )
    assert proposal.credits["process.metal_phase"]["Si"] == pytest.approx(
        mol_SiO2_consumed, abs=1e-12, rel=1e-12
    )

    _atom_check(proposal, sim.species_formula_registry, tol=1e-12)


def test_c6_back_reduction_refuses_no_liquid_before_transition(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": {
                "SiO2": 50.0 / (MOLAR_MASS["SiO2"] / 1000.0),
            },
            "process.metal_phase": {"Al": 10.0},
            "process.reagent_inventory": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )

    result = BuiltinMetallothermicStepProvider().dispatch(
        IntentRequest(
            intent=ChemistryIntent.METALLOTHERMIC_STEP,
            account_view=view,
            temperature_C=1700.0,
            pressure_bar=1e-6,
            control_inputs={
                "reaction_family": REACTION_FAMILY_C6_MG,
                "back_reduction": True,
                "mol_Al_produced": 10.0,
                "liquid_fraction": 0.0,
                "dt_hr": 1.0,
            },
        )
    )

    assert result.status == "refused"
    assert result.transition is None
    assert result.diagnostic["reason_refused"] == "no_liquid_phase"
    assert result.diagnostic["back_reduction"] is True


def test_c6_back_reduction_caps_control_to_actual_metal_al_inventory(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": {
                "SiO2": 50.0 / (MOLAR_MASS["SiO2"] / 1000.0),
            },
            "process.metal_phase": {"Al": 1.0},
            "process.reagent_inventory": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )

    result = BuiltinMetallothermicStepProvider().dispatch(
        IntentRequest(
            intent=ChemistryIntent.METALLOTHERMIC_STEP,
            account_view=view,
            temperature_C=1700.0,
            pressure_bar=1e-6,
            control_inputs={
                "reaction_family": REACTION_FAMILY_C6_MG,
                "back_reduction": True,
                "mol_Al_produced": 10.0,
                "liquid_fraction": 1.0,
                "JANAF_4th_multiphase_margin_kJ_per_mol_O2": {
                    "Mg_Al_crossover_C": 1471.4,
                },
                "kinetic_driven_above_crossover": True,
                "dt_hr": 1.0,
            },
        )
    )

    assert result.status == "ok"
    assert result.transition is not None
    assert result.diagnostic["mol_Al_control"] == pytest.approx(10.0)
    assert result.diagnostic["mol_Al_available"] == pytest.approx(1.0)
    assert result.transition.debits["process.metal_phase"]["Al"] == pytest.approx(
        BuiltinMetallothermicStepProvider.BACK_REDUCTION_FRACTION
    )


def test_provider_short_circuits_on_empty_reagent(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """No reagent inventory -> ok-no-op for every reaction family
    (mirrors the legacy ``<= 0.01 kg`` early-return guard).
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinMetallothermicStepProvider()
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": {"FeO": 100.0, "Al2O3": 50.0},
            "process.metal_phase": {},
            "process.reagent_inventory": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    for family in (
        REACTION_FAMILY_C3_K,
        REACTION_FAMILY_C3_NA,
        REACTION_FAMILY_C6_MG,
    ):
        request = IntentRequest(
            intent=ChemistryIntent.METALLOTHERMIC_STEP,
            account_view=view,
            temperature_C=1300.0,
            pressure_bar=1e-6,
            control_inputs={
                "reaction_family": family,
                "reagent_available_kg": 0.0,
            },
        )
        result = provider.dispatch(request)
        assert result.status == "ok"
        assert result.transition is None


# ---------------------------------------------------------------------------
# 6. Smoke parity: full C0 -> C6 on two feedstocks (C3 + C6 exercised)
# ---------------------------------------------------------------------------


@pytest.mark.live_engine
def test_c6_static_hold_exercises_c6_proceed_decision_path(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    readiness_timeout_s = 10.0
    magemin = MAGEMinBackend()
    if not magemin.initialize({}):
        pytest.skip("MAGEMin live backend unavailable: binary not located")
    readiness = magemin.equilibrate(
        1400.0,
        composition_kg={
            "SiO2": 49.0,
            "Al2O3": 14.0,
            "FeO": 10.0,
            "MgO": 9.0,
            "CaO": 11.0,
        },
        pressure_bar=1000.0,
        call_timeout_s=readiness_timeout_s,
    )
    readiness_detail = "; ".join(readiness.warnings)
    if "timed out after" in readiness_detail:
        pytest.skip(
            "MAGEMin live backend unavailable after "
            f"{readiness_timeout_s:g}s readiness timeout: {readiness_detail}"
        )
    assert readiness.status == "ok", readiness_detail

    patched_setpoints = copy.deepcopy(setpoints_data)
    c6_cfg = patched_setpoints["campaigns"]["C6"]
    # Pin the selected recipe mechanism: within-noise yield ties choose the
    # colder hold for greater Mg/Al2O3 margin and lower heating energy.
    c6_cfg["default_hold_T_C"] = 1400.0
    c6_cfg["hold_temp_C"] = 1400.0
    c6_cfg["kinetic_driven_above_crossover"] = False

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        patched_setpoints,
        additives_kg={"K": 30.0, "Na": 25.0, "Mg": 60.0},
    )
    sim.start_campaign(CampaignPhase.C0)
    decision_choice = {
        DecisionType.ROOT_BRANCH: "pyrolysis",
        DecisionType.PATH_AB: "A_staged",
        DecisionType.BRANCH_ONE_TWO: "two",
        DecisionType.C6_PROCEED: "yes",
    }
    steps = 0
    while not sim.is_complete() and steps < 5000:
        if sim.paused_for_decision:
            decision = sim.pending_decision
            choice = decision_choice.get(decision.decision_type)
            if choice not in (decision.options or []):
                choice = (decision.options or [None])[0]
            sim.apply_decision(decision.decision_type, choice)
            continue
        sim.step()
        steps += 1

    assert sim.is_complete()
    assert (DecisionType.C6_PROCEED, "yes") in sim.record.decisions
    assert any(
        transition.name == "c6_mg_thermite_primary"
        for transition in sim.atom_ledger.transitions
    )
    assert sim.melt.temperature_C == pytest.approx(1400.0)
    assert abs(sim._make_snapshot().mass_balance_error_pct) < 5e-12


def test_c6_waits_for_static_hold_before_thermite_dispatch(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg={"Mg": 60.0},
    )
    sim.start_campaign(CampaignPhase.C6)
    sim.melt.temperature_C = 1500.0
    sim.overhead.transport_saturation_pct = 200.0

    sim.step()

    assert sim.melt.campaign == CampaignPhase.C6
    assert sim.melt.temperature_C == pytest.approx(1500.0)
    assert sim._last_actual_ramp == pytest.approx(0.0)
    assert not sim._c6_campaign_refused
    assert not [
        transition
        for transition in sim.atom_ledger.transitions
        if transition.name in {
            "c6_mg_thermite_primary",
            "c6_al_si_back_reduction",
        }
    ]


def test_c6_ci_empty_window_refusal_precedes_zero_mg_noop(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_sim(
        "ci_carbonaceous_chondrite",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg={},
    )
    sim.start_campaign(CampaignPhase.C6)

    sim._step_thermite()

    assert sim.thermite_Mg_inventory_kg == pytest.approx(0.0)
    assert sim._c6_campaign_refused
    assert sim._last_c6_refusal_diagnostic["diagnostic"]["reason_refused"] == (
        "c6_joint_thermodynamic_liquid_fraction_window_empty"
    )
    assert not [
        transition
        for transition in sim.atom_ledger.transitions
        if transition.name in {
            "c6_mg_thermite_primary",
            "c6_al_si_back_reduction",
        }
    ]


def test_c6_ci_empty_window_records_binding_refusal_without_transitions(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_sim(
        "ci_carbonaceous_chondrite",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg={"K": 30.0, "Na": 25.0, "Mg": 60.0},
    )
    sim.start_campaign(CampaignPhase.C0)
    decision_choice = {
        DecisionType.ROOT_BRANCH: "pyrolysis",
        DecisionType.PATH_AB: "A_staged",
        DecisionType.BRANCH_ONE_TWO: "two",
        DecisionType.C6_PROCEED: "yes",
    }
    steps = 0
    al2o3_mol_before_c6 = None
    while not sim.is_complete() and steps < 5000:
        if sim.paused_for_decision:
            decision = sim.pending_decision
            if decision.decision_type == DecisionType.C6_PROCEED:
                al2o3_mol_before_c6 = sim.atom_ledger.mol_by_account(
                    "process.cleaned_melt"
                ).get("Al2O3", 0.0)
            choice = decision_choice.get(decision.decision_type)
            if choice not in (decision.options or []):
                choice = (decision.options or [None])[0]
            sim.apply_decision(decision.decision_type, choice)
            continue
        sim.step()
        steps += 1

    assert sim.is_complete()
    assert (DecisionType.C6_PROCEED, "yes") in sim.record.decisions
    refusal = sim._last_c6_refusal_diagnostic
    assert refusal["status"] == "refused"
    assert refusal["campaign"] == CampaignPhase.C6.name
    assert refusal["diagnostic"]["reason_refused"] == (
        "c6_joint_thermodynamic_liquid_fraction_window_empty"
    )
    assert refusal["diagnostic"]["liquid_fraction"] == pytest.approx(0.0)
    assert refusal["diagnostic"]["joint_window"]["status"] == "empty"
    assert not [
        transition
        for transition in sim.atom_ledger.transitions
        if transition.name in {
            "c6_mg_thermite_primary",
            "c6_al_si_back_reduction",
        }
    ]
    assert al2o3_mol_before_c6 is not None
    assert al2o3_mol_before_c6 > 0.0
    assert sim.atom_ledger.mol_by_account("process.cleaned_melt").get(
        "Al2O3", 0.0
    ) == pytest.approx(al2o3_mol_before_c6)
    assert sim._last_campaign_summary["c6_refusal_diagnostic"] == refusal
    assert abs(sim._make_snapshot().mass_balance_error_pct) < 5e-12


@pytest.mark.parametrize(
    "feedstock_key, additives_kg",
    [
        ("lunar_mare_low_ti", {"K": 30.0, "Na": 25.0, "Mg": 60.0}),
        ("mars_basalt", {"C": 60.0, "K": 30.0, "Na": 25.0, "Mg": 60.0}),
    ],
)
def test_full_run_mass_balance_holds_with_kernel_committed_metallothermic(
    feedstock_key,
    additives_kg,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    """Drive C0 -> C6 on each feedstock and verify:

    * the simulator runs to completion,
    * the AtomLedger holds a non-trivial number of metallothermic
      transitions (so we know the kernel-committed METALLOTHERMIC_STEP
      path actually fired across the C3 + C6 campaigns),
    * each metallothermic transition strictly debits / credits within
      the three declared accounts (no leak to overhead_gas, terminal
      O2 bins, condensation_train),
    * each transition closes mass within a tight 1 mg per-transition
      tolerance,
    * end-of-batch mass-balance closure stays at the same 5e-12 %
      ceiling the prior flips established.

    Asteroid feedstocks may not exercise C3/C6 in the default decision
    path -- they are excluded here; the lunar + Mars cases give the
    coverage the goal requires.
    """

    sim = _build_sim(
        feedstock_key,
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg=additives_kg,
    )
    sim.start_campaign(CampaignPhase.C0)
    decision_choice = {
        DecisionType.ROOT_BRANCH: "pyrolysis",
        DecisionType.PATH_AB: "A",
        DecisionType.BRANCH_ONE_TWO: "two",
        DecisionType.C6_PROCEED: "yes",
    }
    steps = 0
    while not sim.is_complete() and steps < 5000:
        if sim.paused_for_decision:
            decision = sim.pending_decision
            choice = decision_choice.get(decision.decision_type)
            if choice not in (decision.options or []):
                choice = (decision.options or [None])[0]
            sim.apply_decision(decision.decision_type, choice)
            continue
        sim.step()
        steps += 1

    assert sim.is_complete(), (
        f"smoke run for {feedstock_key} did not complete in 5000 steps"
    )

    transitions = sim.atom_ledger.transitions
    metallothermic_reasons = {
        "c3_k_shuttle_fe_reduction",
        "c3_na_shuttle_reduction",
        "c6_mg_thermite_primary",
        "c6_al_si_back_reduction",
    }
    metallothermic_transitions = [
        t for t in transitions
        if t.name in metallothermic_reasons
    ]
    assert len(metallothermic_transitions) > 0, (
        f"feedstock {feedstock_key} produced zero metallothermic "
        "transitions; the kernel-committed METALLOTHERMIC_STEP path "
        "never fired"
    )

    registry = sim.atom_ledger.registry
    allowed_accounts = {
        "process.cleaned_melt",
        "process.metal_phase",
        "process.reagent_inventory",
        SPENT_REDUCTANT_RESIDUE_ACCOUNT,
    }
    cumulative_imbalance_kg = 0.0
    for trans in metallothermic_transitions:
        for lot in (*trans.debits, *trans.credits):
            assert lot.account in allowed_accounts, (
                f"metallothermic transition {trans.name} touches "
                f"unexpected account {lot.account!r}; expected one "
                f"of {sorted(allowed_accounts)}"
            )
        debit_kg = trans.debit_mass_kg(registry)
        credit_kg = trans.credit_mass_kg(registry)
        delta = abs(debit_kg - credit_kg)
        assert delta < 1e-3, (
            f"metallothermic transition {trans.name} has unbalanced "
            f"mass: debit={debit_kg:.6g} credit={credit_kg:.6g}"
        )
        cumulative_imbalance_kg += delta

    assert cumulative_imbalance_kg < 1e-6, (
        f"feedstock {feedstock_key} accumulated "
        f"{cumulative_imbalance_kg:.3e} kg metallothermic imbalance "
        "(expected <1e-6 kg)"
    )

    snapshot = sim._make_snapshot()
    assert abs(snapshot.mass_balance_error_pct) < 5e-12, (
        f"feedstock {feedstock_key} mass balance closure "
        f"{snapshot.mass_balance_error_pct:.3e} % exceeds the "
        "5e-12 % kernel-path bound"
    )


def test_mars_basalt_c3_shuttle_conserves_total_elemental_fe(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_sim(
        "mars_basalt",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg={"C": 60.0, "K": 30.0, "Na": 25.0},
    )

    def fe_atoms_by_account():
        balances = sim.atom_ledger.mol_by_account()
        return {
            account: sim.atom_ledger.atom_moles_by_account(account).get("Fe", 0.0)
            for account in balances
        }

    fe_before_by_account = fe_atoms_by_account()
    fe_before = math.fsum(fe_before_by_account.values())

    sim.start_campaign(CampaignPhase.C0)
    decision_choice = {
        DecisionType.ROOT_BRANCH: "pyrolysis",
        DecisionType.PATH_AB: "A_staged",
        DecisionType.BRANCH_ONE_TWO: "two",
    }
    steps = 0
    while sim.melt.campaign != CampaignPhase.C4 and steps < 5000:
        if sim.paused_for_decision:
            decision = sim.pending_decision
            choice = decision_choice.get(decision.decision_type)
            if choice not in (decision.options or []):
                choice = (decision.options or [None])[0]
            sim.apply_decision(decision.decision_type, choice)
            continue
        sim.step()
        steps += 1

    assert sim.melt.campaign == CampaignPhase.C4, (
        "Mars-basalt run did not reach the post-C3 boundary in 5000 steps"
    )
    assert any(
        transition.name == "c3_na_shuttle_reduction"
        and transition.credit_atom_moles(sim.atom_ledger.registry).get("Fe", 0.0)
        > 0.0
        for transition in sim.atom_ledger.transitions
    ), "Mars-basalt C3 run produced no elemental Fe shuttle transition"

    fe_after_by_account = fe_atoms_by_account()
    all_accounts = set(fe_before_by_account) | set(fe_after_by_account)
    fe_after = math.fsum(
        fe_after_by_account.get(account, 0.0) for account in all_accounts
    )
    fe_error_pct = abs(fe_after - fe_before) / fe_before * 100.0

    assert fe_error_pct <= 5e-12, (
        f"total elemental Fe changed by {fe_after - fe_before:.12g} mol-atoms "
        f"({fe_error_pct:.3e} %) across {len(all_accounts)} ledger accounts; "
        f"before={fe_before:.12g}, after={fe_after:.12g}; "
        f"before_by_account={fe_before_by_account}; "
        f"after_by_account={fe_after_by_account}"
    )
